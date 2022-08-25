#!/usr/bin/python3

# switcheroo-control integration test suite
#
# Run in built tree to test local built binaries, or from anywhere else to test
# system installed binaries.
#
# Copyright: (C) 2011 Martin Pitt <martin.pitt@ubuntu.com>
# (C) 2020 Bastien Nocera <hadess@hadess.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import os
import sys
import dbus
import tempfile
import subprocess
import unittest
import time

try:
    import gi
    from gi.repository import GLib
    from gi.repository import Gio
except ImportError as e:
    sys.stderr.write('Skipping tests, PyGobject not available for Python 3, or missing GI typelibs: %s\n' % str(e))
    sys.exit(0)

try:
    gi.require_version('UMockdev', '1.0')
    from gi.repository import UMockdev
except ImportError:
    sys.stderr.write('Skipping tests, umockdev not available (https://github.com/martinpitt/umockdev)\n')
    sys.exit(0)

try:
    import dbusmock
except ImportError:
    sys.stderr.write('Skipping tests, python-dbusmock not available (http://pypi.python.org/pypi/python-dbusmock).\n')
    sys.exit(0)


SC = 'net.hadess.SwitcherooControl'
SC_PATH = '/net/hadess/SwitcherooControl'

class Tests(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        # run from local build tree if we are in one, otherwise use system instance
        builddir = os.getenv('top_builddir', '.')
        if os.access(os.path.join(builddir, 'src', 'switcheroo-control'), os.X_OK):
            cls.daemon_path = os.path.join(builddir, 'src', 'switcheroo-control')
            print('Testing binaries from local build tree (%s)' % cls.daemon_path)
        elif os.environ.get('UNDER_JHBUILD', False):
            jhbuild_prefix = os.environ['JHBUILD_PREFIX']
            cls.daemon_path = os.path.join(jhbuild_prefix, 'libexec', 'switcheroo-control')
            print('Testing binaries from JHBuild (%s)' % cls.daemon_path)
        else:
            cls.daemon_path = None
            with open('/usr/lib/systemd/system/switcheroo-control.service') as f:
                for line in f:
                    if line.startswith('ExecStart='):
                        cls.daemon_path = line.split('=', 1)[1].strip()
                        break
            assert cls.daemon_path, 'could not determine daemon path from systemd .service file'
            print('Testing installed system binary (%s)' % cls.daemon_path)

        # fail on CRITICALs on client side
        GLib.log_set_always_fatal(GLib.LogLevelFlags.LEVEL_WARNING |
                                  GLib.LogLevelFlags.LEVEL_ERROR |
                                  GLib.LogLevelFlags.LEVEL_CRITICAL)

        # set up a fake system D-BUS
        cls.test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        cls.test_bus.up()
        try:
            del os.environ['DBUS_SESSION_BUS_ADDRESS']
        except KeyError:
            pass
        os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = cls.test_bus.get_bus_address()

        cls.dbus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        cls.dbus_con = cls.get_dbus(True)

    @classmethod
    def tearDownClass(cls):
        cls.test_bus.down()
        dbusmock.DBusTestCase.tearDownClass()

    def setUp(self):
        '''Set up a local umockdev testbed.

        The testbed is initially empty.
        '''
        self.testbed = UMockdev.Testbed.new()

        self.proxy = None
        self.log = None
        self.daemon = None

    def run(self, result=None):
        super(Tests, self).run(result)
        if result and len(result.errors) + len(result.failures) > 0 and self.log:
            with open(self.log.name) as f:
                sys.stderr.write('\n-------------- daemon log: ----------------\n')
                sys.stderr.write(f.read())
                sys.stderr.write('------------------------------\n')

    def tearDown(self):
        del self.testbed
        self.stop_daemon()

    #
    # Daemon control and D-BUS I/O
    #

    def start_daemon(self):
        '''Start daemon and create DBus proxy.

        When done, this sets self.proxy as the Gio.DBusProxy for switcheroo-control.
        '''
        env = os.environ.copy()
        env['G_DEBUG'] = 'fatal-criticals'
        env['G_MESSAGES_DEBUG'] = 'all'
        # note: Python doesn't propagate the setenv from Testbed.new(), so we
        # have to do that ourselves
        env['UMOCKDEV_DIR'] = self.testbed.get_root_dir()
        self.log = tempfile.NamedTemporaryFile()
        if os.getenv('VALGRIND') != None:
            daemon_path = ['valgrind', self.daemon_path, '-v']
        else:
            daemon_path = [self.daemon_path, '-v']

        self.daemon = subprocess.Popen(daemon_path,
                                       env=env, stdout=self.log,
                                       stderr=subprocess.STDOUT)

        # wait until the daemon gets online
        timeout = 100
        while timeout > 0:
            time.sleep(0.1)
            timeout -= 1
            try:
                self.get_dbus_property('HasDualGpu')
                break
            except GLib.GError:
                pass
        else:
            self.fail('daemon did not start in 10 seconds')

        self.proxy = Gio.DBusProxy.new_sync(
            self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, SC,
            SC_PATH, SC, None)

        self.assertEqual(self.daemon.poll(), None, 'daemon crashed')

    def stop_daemon(self):
        '''Stop the daemon if it is running.'''

        if self.daemon:
            try:
                self.daemon.kill()
            except OSError:
                pass
            self.daemon.wait()
        self.daemon = None
        self.proxy = None

    def get_dbus_property(self, name):
        '''Get property value from daemon D-Bus interface.'''

        proxy = Gio.DBusProxy.new_sync(
            self.dbus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, SC,
            SC_PATH, 'org.freedesktop.DBus.Properties', None)
        return proxy.Get('(ss)', SC, name)

    def have_text_in_log(self, text):
        return self.count_text_in_log(text) > 0

    def count_text_in_log(self, text):
        with open(self.log.name) as f:
            return f.read().count(text)

    def assertEventually(self, condition, message=None, timeout=50):
        '''Assert that condition function eventually returns True.

        Timeout is in deciseconds, defaulting to 50 (5 seconds). message is
        printed on failure.
        '''
        while timeout >= 0:
            context = GLib.MainContext.default()
            while context.iteration(False):
                pass
            if condition():
                break
            timeout -= 1
            time.sleep(0.1)
        else:
            self.fail(message or 'timed out waiting for ' + str(condition))

    def add_intel_gpu(self):
        parent = self.testbed.add_device('pci', 'i915 VGA controller', None,
                [ 'boot_vga', '1' ],
                [ 'DRIVER', 'i915',
                  'PCI_CLASS', '30000',
                  'PCI_ID', '8086:5917',
                  'PCI_SUBSYS_ID', '1043:1A00'
                  'PCI_SLOT_NAME', '0000:00:02.0'
                  'MODALIAS', 'pci:v00008086d00005917sv00001043sd00001A00bc03sc00i00',
                  'ID_PCI_CLASS_FROM_DATABASE', 'Display controller',
                  'ID_PCI_SUBCLASS_FROM_DATABASE', 'VGA compatible controller',
                  'ID_PCI_INTERFACE_FROM_DATABASE', 'VGA controller',
                  'ID_VENDOR_FROM_DATABASE', 'Intel Corporation',
                  'ID_MODEL_FROM_DATABASE', 'UHD Graphics 620',
                  'SWITCHEROO_CONTROL_PRODUCT_NAME', 'UHD Graphics 620 (Kabylake GT2)',
                  'SWITCHEROO_CONTROL_VENDOR_NAME', 'Intel(R)',
                  'FWUPD_GUID', '0x8086:0x5917' ]
                )

        self.testbed.add_device('drm', 'dri/card0', parent,
                [],
                [ 'DEVNAME', '/dev/dri/card0',
                  'ID_PATH', 'pci-0000:00:02.0',
                  'ID_PATH_TAG', 'pci-0000_00_02_0' ]
                )

        self.testbed.add_device('drm', 'dri/renderD128', parent,
                [],
                [ 'DEVNAME', '/dev/dri/renderD128',
                  'ID_PATH', 'pci-0000:00:02.0',
                  'ID_PATH_TAG', 'pci-0000_00_02_0' ]
                )

    def add_nouveau_gpu(self):
        parent = self.testbed.add_device('pci', 'NVidia VGA controller', None,
                [ 'boot_vga', '0' ],
                [ 'DRIVER', 'nouveau',
                  'PCI_CLASS', '30200',
                  'PCI_ID', '10DE:134E',
                  'PCI_SUBSYS_ID', '1043:143E'
                  'PCI_SLOT_NAME', '0000:01:00.0'
                  'MODALIAS', 'pci:v000010DEd0000134Esv00001043sd0000143Ebc03sc02i00',
                  'ID_PCI_CLASS_FROM_DATABASE', 'Display controller',
                  'ID_PCI_SUBCLASS_FROM_DATABASE', '3D controller',
                  'ID_PCI_INTERFACE_FROM_DATABASE', 'NVIDIA Corporation',
                  'ID_MODEL_FROM_DATABASE', 'GM108M [GeForce 930MX]',
                  'FWUPD_GUID', '0x10de:0x134e' ]
                )

        self.testbed.add_device('drm', 'dri/card1', parent,
                [],
                [ 'DEVNAME', '/dev/dri/card1',
                  'ID_PATH', 'pci-0000:01:00.0',
                  'ID_PATH_TAG', 'pci-0000_01_00_0' ]
                )

        self.testbed.add_device('drm', 'dri/renderD129', parent,
                [],
                [ 'DEVNAME', '/dev/dri/renderD129',
                  'ID_PATH', 'pci-0000:01:00.0',
                  'ID_PATH_TAG', 'pci-0000_01_00_0' ]
                )

    def add_nvidia_gpu(self):
        parent = self.testbed.add_device('pci', 'NVidia VGA controller', None,
                [ 'boot_vga', '0' ],
                [ 'DRIVER', 'nvidia',
                  'PCI_CLASS', '30000',
                  'PCI_ID', '10DE:1C03',
                  'PCI_SUBSYS_ID', '1043:85AC'
                  'PCI_SLOT_NAME', '0000:01:00.0'
                  'MODALIAS', 'pci:v000010DEd00001C03sv00001043sd000085ACbc03sc00i00',
                  'ID_PCI_CLASS_FROM_DATABASE', 'Display controller',
                  'ID_PCI_SUBCLASS_FROM_DATABASE', 'VGA compatible controller',
                  'ID_PCI_INTERFACE_FROM_DATABASE', 'VGA controller',
                  'ID_VENDOR_FROM_DATABASE', 'NVIDIA Corporation',
                  'ID_MODEL_FROM_DATABASE', 'GP106 [GeForce GTX 1060 6GB]',
                  'FWUPD_GUID', '0x10de:0x85ac' ]
                )

        self.testbed.set_attribute_link(parent, 'driver', '../../nvidia')

        self.testbed.add_device('drm', 'dri/card1', parent,
                [],
                [ 'DEVNAME', '/dev/dri/card1',
                  'ID_PATH', 'pci-0000:01:00.0',
                  'ID_PATH_TAG', 'pci-0000_01_00_0' ]
                )

        self.testbed.add_device('drm', 'dri/renderD129', parent,
                [],
                [ 'DEVNAME', '/dev/dri/renderD129',
                  'ID_PATH', 'pci-0000:01:00.0',
                  'ID_PATH_TAG', 'pci-0000_01_00_0' ]
                )

    def add_vc4_gpu(self):
        parent = self.testbed.add_device('platform', 'VC4 platform device', None,
                [],
                [ 'DRIVER', 'vc4-drm',
                  'OF_NAME', 'gpu',
                  'OF_FULLNAME', '/soc/gpu',
                  'OF_COMPATIBLE_0', 'brcm,bcm2835-vc4',
                  'OF_COMPATIBLE_N', '1',
                  'MODALIAS', 'of:NgpuT(null)Cbrcm,bcm2835-vc4',
                  'ID_PATH', 'platform-soc:gpu',
                  'ID_PATH_TAG', 'platform-soc_gpu' ]
                )

        self.testbed.set_attribute_link(parent, 'driver', '../../vc4-drm')

        self.testbed.add_device('drm', 'dri/card1', parent,
                [],
                [ 'DEVNAME', '/dev/dri/card1',
                  'ID_PATH', 'platform-soc:gpu',
                  'ID_PATH_TAG', 'platform-soc_gpu' ]
                )

        self.testbed.add_device('drm', 'dri/renderD129', parent,
                [],
                [ 'DEVNAME', '/dev/dri/renderD129',
                  'ID_PATH', 'platform-soc:gpu',
                  'ID_PATH_TAG', 'platform-soc_gpu' ]
                )

    #
    # Actual test cases
    #

    def test_single_device(self):
        '''single device'''

        self.add_intel_gpu()

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), False)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 1)

        gpus = self.get_dbus_property('GPUs')
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]['Name'], 'Intel® UHD Graphics 620 (Kabylake GT2)')
        sc_env = gpus[0]['Environment']
        self.assertEqual(len(sc_env), 2)
        self.assertEqual(sc_env[0], 'DRI_PRIME')
        self.assertEqual(sc_env[1], 'pci-0000_00_02_0')
        self.assertEqual(gpus[0]['Default'], True)

        # process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', 'net.hadess.SwitcherooControl', '--object-path', '/net/hadess/SwitcherooControl'])
        # print (self.get_dbus_property('GPUs'))

        self.stop_daemon()

    def test_rpi(self):
        self.add_vc4_gpu()

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), False)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 1)

        gpus = self.get_dbus_property('GPUs')
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]['Name'], 'Unknown Graphics Controller')
        sc_env = gpus[0]['Environment']

        self.assertEqual(len(sc_env), 2)
        self.assertEqual(sc_env[0], 'DRI_PRIME')
        self.assertEqual(sc_env[1], 'platform-soc_gpu')
        self.assertEqual(gpus[0]['Default'], True)

        # process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', 'net.hadess.SwitcherooControl', '--object-path', '/net/hadess/SwitcherooControl'])
        # print (self.get_dbus_property('GPUs'))

        self.stop_daemon()

    def test_dual_open_source(self):
        '''dual open source devices'''

        self.add_intel_gpu()
        self.add_nouveau_gpu()

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), True)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 2)

        gpus = self.get_dbus_property('GPUs')
        self.assertEqual(len(gpus), 2)

        gpu = gpus[0]
        self.assertEqual(gpu['Name'], 'GM108M [GeForce 930MX]')
        sc_env = gpu['Environment']
        self.assertEqual(len(sc_env), 2)
        self.assertEqual(sc_env[0], 'DRI_PRIME')
        self.assertEqual(sc_env[1], 'pci-0000_01_00_0')
        self.assertEqual(gpu['Default'], False)

        gpu = gpus[1]
        self.assertEqual(gpu['Name'], 'Intel® UHD Graphics 620 (Kabylake GT2)')
        sc_env = gpu['Environment']
        self.assertEqual(len(sc_env), 2)
        self.assertEqual(sc_env[0], 'DRI_PRIME')
        self.assertEqual(sc_env[1], 'pci-0000_00_02_0')
        self.assertEqual(gpu['Default'], True)

        # process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', 'net.hadess.SwitcherooControl', '--object-path', '/net/hadess/SwitcherooControl'])

        self.stop_daemon()

    def test_dual_open_source_with_ttm(self):
        '''dual open source devices'''

        self.add_intel_gpu()
        self.add_nouveau_gpu()

        self.testbed.add_device('drm', 'ttm', None,
                [],
                [ 'DEVPATH', '/devices/virtual/drm/ttm',
                  'DEVTYPE', 'ttm' ]
                )

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), True)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 2)

        gpus = self.get_dbus_property('GPUs')
        self.assertEqual(len(gpus), 2)

        gpu = gpus[0]
        self.assertEqual(gpu['Name'], 'GM108M [GeForce 930MX]')
        self.assertEqual(gpu['Default'], False)

        gpu = gpus[1]
        self.assertEqual(gpu['Name'], 'Intel® UHD Graphics 620 (Kabylake GT2)')
        self.assertEqual(gpu['Default'], True)

        # process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', 'net.hadess.SwitcherooControl', '--object-path', '/net/hadess/SwitcherooControl'])

        self.stop_daemon()

    def test_dual_proprietary(self):
        '''oss intel + nvidia blob'''

        self.add_intel_gpu()
        self.add_nvidia_gpu()

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), True)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 2)

        gpus = self.get_dbus_property('GPUs')
        self.assertEqual(len(gpus), 2)

        gpu1 = gpus[0]
        self.assertEqual(gpu1['Name'], 'NVIDIA Corporation GP106 [GeForce GTX 1060 6GB]')
        self.assertEqual(gpu1['Default'], False)

        gpu2 = gpus[1]
        self.assertEqual(gpu2['Name'], 'Intel® UHD Graphics 620 (Kabylake GT2)')
        self.assertEqual(gpu2['Default'], True)

        sc_env = gpu1['Environment']

        self.assertIn('__GLX_VENDOR_LIBRARY_NAME', sc_env)
        self.assertIn('__NV_PRIME_RENDER_OFFLOAD', sc_env)
        self.assertIn('__VK_LAYER_NV_optimus', sc_env)

        def get_sc_env(name):
            i = sc_env.index(name)
            return sc_env[i+1]

        self.assertEqual(get_sc_env('__GLX_VENDOR_LIBRARY_NAME'), 'nvidia')
        self.assertEqual(get_sc_env('__NV_PRIME_RENDER_OFFLOAD'), '1')
        self.assertEqual(get_sc_env('__VK_LAYER_NV_optimus'), 'NVIDIA_only')

        self.stop_daemon()


    def test_dual_hotplug(self):
        '''dual open source devices'''

        self.add_intel_gpu()

        self.start_daemon()
        self.assertEqual(self.get_dbus_property('HasDualGpu'), False)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 1)

        self.add_nouveau_gpu()

        self.assertEqual(self.get_dbus_property('HasDualGpu'), True)
        self.assertEqual(self.get_dbus_property('NumGPUs'), 2)

        # process = subprocess.Popen(['gdbus', 'introspect', '--system', '--dest', 'net.hadess.SwitcherooControl', '--object-path', '/net/hadess/SwitcherooControl'])

        self.stop_daemon()

    def test_cmdline_tool(self):
        '''test the command-line tool'''

        self.add_intel_gpu()
        self.add_nouveau_gpu()
        self.start_daemon()

        builddir = os.getenv('top_builddir', '.')
        tool_path = os.path.join(builddir, 'src', 'switcherooctl')

        out = subprocess.run([tool_path], capture_output=True)
        self.assertEqual(out.returncode, 0, "'switcherooctl' call failed")
        self.assertEqual(out.stdout, b'Device: 0\n  Name:        Intel\xc2\xae UHD Graphics 620 (Kabylake GT2)\n  Default:     yes\n  Environment: DRI_PRIME=pci-0000_00_02_0\n\nDevice: 1\n  Name:        GM108M [GeForce 930MX]\n  Default:     no\n  Environment: DRI_PRIME=pci-0000_01_00_0\n')

        out = subprocess.run([tool_path, 'launch', '--gpu', '0', 'env'], capture_output=True)
        self.assertEqual(out.returncode, 0, "'switcherooctl launch --gpu 0' failed")
        assert('DRI_PRIME=pci-0000_00_02_0' in str(out.stdout))

        out = subprocess.run([tool_path, 'launch', '--gpu', '1', 'env'], capture_output=True)
        self.assertEqual(out.returncode, 0, "'switcherooctl launch --gpu 1' failed")
        assert('DRI_PRIME=pci-0000_01_00_0' in str(out.stdout))

        out = subprocess.run([tool_path, 'launch', '--gpu=1', 'env'], capture_output=True)
        self.assertEqual(out.returncode, 0, "'switcherooctl launch --gpu=1' failed")
        assert('DRI_PRIME=pci-0000_01_00_0' in str(out.stdout))

    #
    # Helper methods
    #

    @classmethod
    def _props_to_str(cls, properties):
        '''Convert a properties dictionary to uevent text representation.'''

        prop_str = ''
        if properties:
            for k, v in properties.items():
                prop_str += '%s=%s\n' % (k, v)
        return prop_str

if __name__ == '__main__':
    # run ourselves under umockdev
    if 'umockdev' not in os.environ.get('LD_PRELOAD', ''):
        os.execvp('umockdev-wrapper', ['umockdev-wrapper'] + sys.argv)

    unittest.main()

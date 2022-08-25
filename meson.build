project('switcheroo-control', 'c',
  version : '2.6',
  license: 'GPLv3+',
  default_options : [
    'buildtype=debugoptimized',
    'warning_level=1',
    'c_std=c99',
  ],
  meson_version : '>= 0.50.0',
)

cc = meson.get_compiler('c')

prefix = get_option('prefix')
libexecdir = prefix / get_option('libexecdir')
datadir = get_option('datadir')

gnome = import('gnome')

glib = dependency('glib-2.0', version: '>= 2.56.0')
gio = dependency('gio-2.0', version: '>= 2.56.0')
gudev = dependency('gudev-1.0', version: '>= 232')

systemd_systemunitdir = get_option('systemdsystemunitdir')
if systemd_systemunitdir == ''
  systemd_systemunitdir = dependency('systemd').get_pkgconfig_variable('systemdsystemunitdir')
endif

hwdb_dir = get_option('hwdbdir')
if hwdb_dir == ''
  udevdir = dependency('udev').get_pkgconfig_variable('udevdir')
  hwdb_dir = udevdir / 'hwdb.d'
endif

# Make like license available in the build root for docs
configure_file(
  input: 'COPYING',
  output: 'COPYING',
  copy: true,
)

subdir('data')
subdir('src')

if get_option('gtk_doc')
  subdir('docs')
endif

if get_option('tests')
  # Python 3 required modules
  python3_required_modules = ['dbus', 'dbusmock', 'gi']

  python = import('python')
  python3 = python.find_installation('python3')
  foreach p : python3_required_modules
    # Source: https://docs.python.org/3/library/importlib.html#checking-if-a-module-can-be-imported
    script = 'import importlib.util; import sys; exit(1) if importlib.util.find_spec(\''+ p +'\') is None else exit(0)'
    if run_command(python3, '-c', script, check: false).returncode() != 0
      error('Python3 module \'' + p + '\' required for running tests but not found')
    endif
  endforeach

  subdir('tests')
endif

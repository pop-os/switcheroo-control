project('switcheroo-control', 'c',
  version : '2.4',
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
sysconfdir = get_option('sysconfdir')

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

subdir('tests')

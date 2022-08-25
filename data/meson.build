data_conf = configuration_data()
data_conf.set('libexecdir', libexecdir)

configure_file(
  input: 'switcheroo-control.service.in',
  output: 'switcheroo-control.service',
  configuration: data_conf,
  install_dir: systemd_systemunitdir,
)

install_data(
  'net.hadess.SwitcherooControl.conf',
  install_dir: datadir / 'dbus-1/system.d',
)

install_data(
  '30-pci-intel-gpu.hwdb',
  install_dir: hwdb_dir,
)

switcheroo-control
==================

D-Bus service to check the availability of dual-GPU

See https://developer.gnome.org/switcheroo-control/ for
developer information.

Installation
------------
```sh
$ meson _build -Dprefix=/usr
$ ninja -v -C _build install
```
It requires libgudev and systemd.

```
gdbus introspect --system --dest net.hadess.SwitcherooControl --object-path /net/hadess/SwitcherooControl
```

If that doesn't work, please file an issue, make sure any running switcheroo-control
has been stopped:
`systemctl stop switcheroo-control.service`
and attach the output of:
`G_MESSAGES_DEBUG=all /usr/sbin/switcheroo-control`
running as ```root```.

Testing
-------

The easiest way to test switcheroo-control is to load a recent version
of gnome-shell and see whether the “Launch using Dedicated Graphics Card”
menu item appears in docked application's contextual menu.

You can use it to launch the [GLArea example application](https://github.com/ebassi/glarea-example/)
to verify that the right video card/GPU is used when launching the application
normally, and through “Launch using Dedicated Graphics Card”.

Or run `make -C tests/app install` to install a test application that uses
the `PrefersNonDefaultGPU` `.desktop` property.

Tested on
---------

- MacBook Pro (8,2)
- Thinkpad T430s

all: switcheroo-control-test-app.desktop switcheroo-control-test-app

switcheroo-control-test-app.desktop: switcheroo-control-test-app.desktop.in
	cat $< | sed "s,@BINDIR@,`readlink -f ~/.local/bin`," > $@

install: switcheroo-control-test-app.desktop switcheroo-control-test-app
	echo $$PATH | grep -q `readlink -f ~/.local/bin` || exit 1
	install -D switcheroo-control-test-app.desktop ~/.local/share/applications/switcheroo-control-test-app.desktop
	install -D switcheroo-control-test-app ~/.local/bin/

uninstall:
	rm -f ~/.local/share/applications/switcheroo-control-test-app.desktop \
	~/.local/bin/switcheroo-control-test-app

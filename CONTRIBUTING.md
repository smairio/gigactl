# Contributing to gigactl

Thank you for helping. gigactl writes to the Embedded Controller (EC) of real
laptops, so some of the rules below are about safety. Each rule says why.

## Run the tests

The tests need no root and no Gigabyte laptop. They use fakes and stubs, and
they never touch the EC. On Ubuntu 24.04, install what CI installs:

```bash
sudo apt install python3-pytest python3-yaml dpkg-dev groff-base \
  python3-gi python3-gi-cairo gir1.2-glib-2.0 gir1.2-gtk-4.0 \
  gir1.2-adw-1 dbus desktop-file-utils
```

Then run these from the top of your checkout:

```bash
python3 -m pytest packaging -q        # debian/, man pages, docs and workflows
python3 -m pytest cli -q              # gfan and gkbd, with stub busctl and ec_probe
(cd daemon && python3 -m pytest -q)   # the daemon, with a fake EC
(cd gui && python3 -m pytest -q)      # the app's logic; no display needed
python3 -m pytest tools -q            # the owner tools, with a fake bus and fake DMI
desktop-file-validate gui/data/*.desktop
for f in gfan gkbd gigactl-cli-lib.sh install.sh; do bash -n "$f"; done
```

Give each suite its own `pytest` command. Every suite keeps its helpers in a
top-level `conftest.py`, and in a shared run pytest imports only one of them as
the module `conftest`. The other suite's tests then fail to collect.

CI runs the same checks on every pull request
([.github/workflows/test.yml](.github/workflows/test.yml)). On your first pull
request, GitHub may wait for the maintainer to approve that run. This is normal.

To run the daemon or the app from a checkout, see
[daemon/README.md](daemon/README.md) and [gui/README.md](gui/README.md).

## The rules

1. **Only the daemon writes the EC, and every write takes its lock.** The EC
   has one command mailbox, and two writers that mix their bytes can send a
   wrong command. New code asks the daemon over D-Bus. (With no daemon running,
   `gfan` and `gkbd` write the EC themselves, and take the same lock.)
2. **Firmware auto is the failsafe. Nothing may trap the fans.** The firmware's
   own fan curve protects the laptop when our code cannot. Every screen and
   command stays one action away from it, and a write the EC refuses hands the
   fans back to it.
3. **Every D-Bus control method is checked by polkit and checks its input.**
   The daemon runs as root, and any program on the system bus can call it.
4. **Be honest about hardware.** In the README, ✅ means the maintainer ran and
   measured gigactl on that model, and 👍 means a user reported that it works.
   A pull request must not mark a model ✅; `packaging/test_docs.py` checks the
   table against the code. A protocol finding from another model goes under
   "Open questions" in [docs/PROTOCOL.md](docs/PROTOCOL.md), not into the
   register tables or the code.
5. **Beginner-first, in simple words.** Many users are new to Linux, and many
   read English as a second language. Read [PRODUCT.md](PRODUCT.md) and
   [DESIGN.md](DESIGN.md) before you change the app or any message.
6. **One change per pull request, with tests.** A small change is easier to
   review and to try on a real laptop. When behaviour changes, update the man
   pages in `man/`, README.md and `docs/` in the same pull request; the
   packaging suite fails when a man page misses a subcommand or an option.

## Reporting hardware

Do you own a Gigabyte G5, G6 or G7? Please tell us if gigactl works on it, with
the [hardware report form](https://github.com/smairio/gigactl/issues/new?template=hardware-report.yml).
"It works" is useful too: the maintainer owns only a G6 KF, so reports are how
we learn about every other model. If something is broken, use the
[bug report form](https://github.com/smairio/gigactl/issues/new?template=bug-report.yml).

## AI-assisted work

AI-assisted contributions are welcome. Please claim only what you ran
yourself, on your own machine: say which tests you ran and which laptop you
tried the change on, or write "not tested on hardware".

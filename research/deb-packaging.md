# .deb packaging for gigactl v1 (GTK4 GUI + root daemon + DBus + polkit + EC access)

Research for [issue #6](https://github.com/smairio/gigactl/issues/6). Verified against Debian Policy, debhelper/dh-python man pages, freedesktop dbus/polkit docs, and the actual packaging of system76-power, TLP, tuxedo-control-center, power-profiles-daemon and nbfc-linux (source links inline). Date: 2026-07-24.

## ANSWER

**One binary package `gigactl`, built with `dpkg-buildpackage` + debhelper compat 13, Python installed as a private app (no setuptools/pybuild), daemon exposing a system-bus DBus API with polkit checks inside the daemon, and the EC register read/write reimplemented in ~100 lines of Python inside the daemon (drop the ec_probe dependency entirely).**

### Target package layout

```
/usr/bin/gigactl                                    # GUI launcher (2-line exec of /usr/lib/gigactl/...)
/usr/bin/gfan, /usr/bin/gkbd                        # CLI tools (become thin DBus clients of the daemon)
/usr/lib/gigactl/                                   # private Python package (gigactl/*.py incl. ec.py, daemon.py, gui/)
/usr/lib/systemd/system/gigactld.service            # root daemon unit (NOT /etc/systemd/system)
/usr/lib/systemd/system-sleep/gigactl               # resume hook (or better: daemon subscribes to logind PrepareForSleep)
/usr/share/dbus-1/system.d/com.gigactl.Daemon.conf  # bus policy: root may own name, users may call it
/usr/share/polkit-1/actions/com.gigactl.policy      # polkit actions (e.g. com.gigactl.control, allow_active=yes)
/usr/share/applications/gigactl.desktop
/usr/share/icons/hicolor/scalable/apps/gigactl.svg
/var/lib/gigactl/                                   # daemon state (saved color / fan curve)
```

`debian/` skeleton: `control` (Depends: `python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, ${python3:Depends}, ${misc:Depends}`), `rules` = `dh $@ --with python3` plus `execute_after_dh_install: dh_python3 /usr/lib/gigactl`, `gigactl.service` → picked up by `dh_installsystemd` (default: enable + start on install, restart after upgrade — exactly what we want), `gigactl.install` for the data files, `changelog`, `copyright` (MIT), `source/format` = `3.0 (native)` (we are upstream).

### The five decisions

1. **File locations** — as in the tree above; every path is the "packages install here, admins override in /etc" convention (evidence §1).
2. **GUI privilege model** — GUI and CLIs are unprivileged DBus clients of `gigactld` on the **system bus**; the daemon calls `org.freedesktop.PolicyKit1.Authority.CheckAuthorization` with the caller's bus name before executing write methods (read-only status methods unauthenticated). Action defaults: `allow_active=yes` (console user controls fans/LEDs without a password), `auth_admin` for anything persistent/system-wide if we add it. **No pkexec.** This is what system76-power, TLP (tlp-pd) and power-profiles-daemon do (evidence §2).
3. **ec_probe** — reimplement in the daemon. gfan/gkbd use exactly two ec_probe operations (`read reg`, `write reg val`). The whole nbfc-linux EC layer is 216 lines of C implementing the ACPI-spec EC handshake; a Python port working from the ACPI spec (not the GPL code) is ~60–100 lines, MIT-clean, and removes the only external dependency. Mirror nbfc's backend order: `/sys/kernel/debug/ec/ec0/io` (kernel-serialized) first, raw `/dev/port` as fallback (evidence §3).
4. **CI** — `dpkg-buildpackage -us -uc -b` on `ubuntu-24.04` (== `ubuntu-latest` today), triggered on `v*` tag push, upload the .deb with `softprops/action-gh-release` (or plain `gh release upload`). Not fpm (evidence §4).
5. **Python packaging** — no setup.py/pyproject/pybuild. Plain `debian/install` of the module tree into the private dir `/usr/lib/gigactl` + `dh_python3 /usr/lib/gigactl` for byte-compilation, shebang rewriting and `${python3:Depends}`. All Python deps must come from apt (PEP 668: noble's python3.12 is externally-managed) — we only need `python3-gi` (evidence §5).

---

## Evidence

### 1. File locations + dh_installsystemd

| File | Path | Authority |
|---|---|---|
| systemd system unit | `/usr/lib/systemd/system/` | Debian Policy §9.3.2: units shipped by packages go to `/usr/lib/systemd/system/`; `/etc/systemd/system` is the admin's ([policy ch-opersys](https://www.debian.org/doc/debian-policy/ch-opersys.html), [systemd.unit(5) load path table](https://manpages.debian.org/bookworm/systemd/systemd.unit.5.en.html)) |
| DBus system bus policy | `/usr/share/dbus-1/system.d/` | [dbus-daemon(1)](https://dbus.freedesktop.org/doc/dbus-daemon.1.html): packages install here; `/etc/dbus-1/system.d/` is for sysadmin overrides |
| DBus activation file (optional) | `/usr/share/dbus-1/system-services/` | same dbus-daemon(1); needs `SystemdService=` to activate via systemd |
| polkit actions (.policy) | `/usr/share/polkit-1/actions/` | [polkit(8)](https://www.freedesktop.org/software/polkit/docs/latest/polkit.8.html) "Declaring Actions" |
| polkit rules (.rules) | `/usr/share/polkit-1/rules.d/` (packages) vs `/etc/polkit-1/rules.d/` (admin) | polkit(8) "Authorization Rules"; we don't need a rules file — the `.policy` defaults suffice |
| .desktop | `/usr/share/applications/` | [freedesktop menu spec](https://specifications.freedesktop.org/menu-spec/latest/) |
| icon | `/usr/share/icons/hicolor/...` | [icon theme spec](https://specifications.freedesktop.org/icon-theme-spec/icon-theme-spec-latest.html) (hicolor = fallback theme) |

Real-world confirmation, same paths in all three healthy projects:
- system76-power [Makefile](https://github.com/pop-os/system76-power/blob/master/Makefile) installs `data/$(ID).conf` → `/usr/share/dbus-1/system.d/`, `data/$(ID).policy` → `/usr/share/polkit-1/actions/`, unit → `$(libdir)/systemd/system/`; [debian/rules](https://github.com/pop-os/system76-power/blob/master/debian/rules) is a plain `dh $@` with `override_dh_installsystemd: dh_installsystemd --name=com.system76.PowerDaemon`.
- TLP [Makefile](https://github.com/linrunner/TLP/blob/main/Makefile): `TLP_SYSD=/usr/lib/systemd/system`, `TLP_POLKIT=/usr/share/polkit-1/actions`, `TLP_DBCONF=/usr/share/dbus-1/system.d`, `TLP_DBSVC=/usr/share/dbus-1/system-services`.
- power-profiles-daemon [data/meson.build](https://gitlab.freedesktop.org/upower/power-profiles-daemon/-/blob/main/data/meson.build): unit → `systemd_system_unit_dir`, bus policy → `dbusconfdir`, activation → `dbusservicedir`, + polkit policy.

**dh_installsystemd** ([man page](https://manpages.debian.org/bookworm/debhelper/dh_installsystemd.1.en.html), compat 13 — Ubuntu 24.04 ships debhelper 13.14): ship the unit as `debian/gigactl.gigactld.service` (or install it and let dh find it). Defaults do the right thing:
- postinst: `deb-systemd-helper enable` + `deb-systemd-invoke start` on first install (Debian Policy [§9.3](https://www.debian.org/doc/debian-policy/ch-opersys.html#starting-system-services): services start on install by default; local overrides via policy-rc.d/mask — never call `systemctl enable` directly in maintscripts).
- upgrades: `--restart-after-upgrade` is the default — the daemon restarts after the new files are configured, minimizing downtime.
- postrm purge: helper cleanup + `daemon-reload`. All snippets generated; we write **no** postinst by hand. (Snippet templates: [debhelper autoscripts](https://sources.debian.org/src/debhelper/latest/autoscripts/).)
- Skip the DBus activation file for v1 (statically enabled service is enough; TLP's `tlp.service` works the same way). Add it later only if we want on-demand start.

Unit hardening worth shipping: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/gigactl`, `DeviceAllow=/dev/port rw`, `NoNewPrivileges=yes` — the daemon needs root for port/debugfs IO but nothing else.

### 2. GUI privilege model — what comparable projects do

- **system76-power** (Rust): root daemon owns `com.system76.PowerDaemon` on the system bus. [Bus policy](https://github.com/pop-os/system76-power/blob/master/data/com.system76.PowerDaemon.conf): `<policy user="root"><allow own=.../></policy>` + `<policy context="default"><allow send_destination=.../></policy>` — any user may *call*; the daemon decides. Sensitive method `set_charge_thresholds` calls polkit `check_authorization` **inside the daemon** with the caller as subject ([src/daemon/mod.rs ~lines 300–335](https://github.com/pop-os/system76-power/blob/master/src/daemon/mod.rs)); the [.policy](https://github.com/pop-os/system76-power/blob/master/data/com.system76.PowerDaemon.policy) sets `auth_admin` for it. Zero pkexec.
- **TLP**: CLI tools just require root (`check_root` in [tlp-func-base.in](https://github.com/linrunner/TLP/blob/main/tlp-func-base.in)) — no pkexec anywhere in the repo. Where TLP *does* face our exact problem (unprivileged desktop → privileged daemon), i.e. its power-profiles-daemon shim `tlp-pd`, it uses system-bus DBus + polkit with [`allow_active=yes`](https://github.com/linrunner/TLP/blob/main/tlp-pd.policy) ("only users with an active session are allowed to perform actions").
- **power-profiles-daemon** (the freedesktop reference): same daemon + bus policy + polkit action files pattern ([data/meson.build](https://gitlab.freedesktop.org/upower/power-profiles-daemon/-/blob/main/data/meson.build)).
- **tuxedo-control-center** — the cautionary tale: Electron GUI + root `tccd` daemon on the system bus ([TccDBusService.ts](https://github.com/tuxedocomputers/tuxedo-control-center/blob/master/src/service-app/classes/TccDBusService.ts)), **but** it additionally shells out to `pkexec` from ~10 Electron backend APIs for one-off writes, and its [after_install.sh](https://github.com/tuxedocomputers/tuxedo-control-center/blob/master/build-src/after_install.sh) copies the unit into `/etc/systemd/system` and runs `systemctl enable` by hand — both anti-patterns from the electron-builder/fpm packaging route.

**Conclusion**: daemon-with-in-daemon-polkit is the norm; pkexec appears only where a project has no daemon for that operation. gigactl *must* have a daemon anyway (fan-curve loop, resume re-apply), so every privileged operation goes through it. In Python the check is one call on the caller's unique bus name:
`Gio.DBusProxy(...PolicyKit1.Authority...).CheckAuthorization(("system-bus-name", {"name": GLib.Variant("s", sender)}), "com.gigactl.control", {}, 1 /*ALLOW_USER_INTERACTION*/, "")` — same subject type system76-power uses.

Suggested actions: `com.gigactl.control` (set fan duty/curve, LED color) `allow_active=yes / allow_inactive=no / allow_any=no` (mirrors tlp-pd; a laptop's console user shouldn't type a password to change fan speed), and read-only status methods with no polkit check at all.

### 3. ec_probe strategy — reimplement (recommended)

What we actually use today: `ec_probe read <reg>` / `ec_probe write <reg> <val>` only (gfan lines 73–78, gkbd equivalents). No dumps, no monitoring.

Upstream facts ([nbfc-linux](https://github.com/nbfc-linux/nbfc-linux)):
- License **GPL-3.0** ([LICENSE](https://github.com/nbfc-linux/nbfc-linux/blob/main/LICENSE)); gigactl is MIT.
- Not in the Ubuntu archive: only install source on this machine is the local dpkg status (`apt-cache policy nbfc-linux` → `100 /var/lib/dpkg/status`); upstream distributes debs only via [GitHub releases](https://github.com/nbfc-linux/nbfc-linux/releases) (`ubuntu-noble-nbfc-linux_0.5.2_amd64.deb`, per-distro assets).
- The entire EC access layer is small and spec-derived: [src/ec_linux.c](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/ec_linux.c) is **216 lines** including word ops and retries — open `/dev/port`, `pread`/`pwrite` at port offsets, and the standard ACPI EC handshake: status/command port **0x66** (EC_SC), data port **0x62** (EC_DATA), commands **RD_EC 0x80 / WR_EC 0x81**, poll IBF/OBF status bits with a bounded spin (500 iterations, 5 retries). The file's own comments cite "ACPI specs ch.12.2 / ch.12.3" — this protocol is specified in the ACPI spec, ch. 12 "ACPI Embedded Controller Interface Specification" (§12.2 registers/status bits, §12.3 command set): https://uefi.org/specs/ACPI/6.5/12_ACPI_Embedded_Controller_Interface_Specification.html
- ec_probe's backend auto-selection ([src/ec.c](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/ec.c) `EC_FindWorking`): **1)** `/sys/kernel/debug/ec/ec0/io` (`ec_sys` module, `write_support=1`), **2)** `/dev/ec` (`acpi_ec` module), **3)** raw `/dev/port`. So today's gfan most likely already goes through the debugfs file when available.

Reimplementation plan (inside the daemon, `gigactl/ec.py`):
- **Backend A (preferred)**: `os.pread/os.pwrite` on `/sys/kernel/debug/ec/ec0/io` at offset=register — the kernel's EC driver performs the actual 0x62/0x66 transaction and serializes it against its own ACPI EC traffic (the raw-port race with the kernel driver is the one real reliability hazard of userspace EC banging; debugfs avoids it). Needs `modprobe ec_sys write_support=1` (daemon does this at start, like nbfc's [`EC_SysLinux_LoadKernelModule`](https://github.com/nbfc-linux/nbfc-linux/blob/main/src/ec_sys_linux.c)).
- **Backend B (fallback)**: `/dev/port` with the spec handshake — ~60 lines of Python (`wait_ibf_clear`, `wait_obf_set`, `read_reg`, `write_reg`). Timing is not a concern: the protocol is "poll status until ready", with no upper-bound hazard — Python being slower than C only makes the poll later, never wrong; nbfc itself just spins with a retry cap.
- **Licensing**: implement from the ACPI spec and our own PROTOCOL.md, not by translating ec_linux.c — the port numbers, commands and status bits are interface facts from the spec, not GPL expression. Result stays MIT and self-contained. Keep the `Credits` mention of nbfc-linux (it earned it).
- Single daemon = single writer, so serialize all EC transactions behind one lock in the daemon; CLIs go through the daemon instead of racing it.

### 4. GitHub Actions

- `ubuntu-latest` **is** `ubuntu-24.04` (x64) today — [actions/runner-images README](https://github.com/actions/runner-images#available-images) (26.04 exists but is `preview`). Pin `runs-on: ubuntu-24.04` anyway: the deb is built against noble's python3/debhelper and targets noble, so don't let `ubuntu-latest` silently move.
- Workflow shape (`on: push: tags: ['v*']`):
  1. `sudo apt-get update && sudo apt-get install -y --no-install-recommends debhelper dh-python devscripts lintian`
  2. `dpkg-buildpackage -us -uc -b` (binary-only, unsigned — fine for GitHub releases)
  3. `lintian ../gigactl_*.deb || true` (advisory)
  4. attach: [`softprops/action-gh-release`](https://github.com/softprops/action-gh-release) with `files: ../gigactl_*_amd64.deb`, or equivalently `gh release create "$TAG" ../gigactl_*.deb` (gh is preinstalled on runners; needs `permissions: contents: write`).
- Keep `debian/changelog` as the version source; a small pre-step can assert the tag matches `dpkg-parsechangelog -S Version`. This exact pattern (matrix of distro debs built in CI, attached to releases) is what nbfc-linux itself does for its [release assets](https://github.com/nbfc-linux/nbfc-linux/releases).
- If we ever want G5/G6 users on 22.04, add a container job (`container: ubuntu:22.04`) building a jammy variant — same debian/ dir, different asset name; not needed for v1.

### 5. Python-in-deb

- Debian Python Policy: public modules → `/usr/lib/python3/dist-packages` (only for things other packages import); **private** modules → `/usr/share/<pkg>` or `/usr/lib/<pkg>` ([Debian Python Policy, "Programs Shipping Private Modules"](https://www.debian.org/doc/packaging-manuals/python-policy/)). gigactl is an app: private dir `/usr/lib/gigactl`.
- [`dh_python3`](https://manpages.debian.org/bookworm/dh-python/dh_python3.1.en.html) handles private dirs: pass the dir explicitly (`dh_python3 /usr/lib/gigactl` in an override) → byte-compile triggers (py3compile/py3clean), shebang normalization, `${python3:Depends}` generation. [`pybuild`](https://manpages.debian.org/bookworm/dh-python/pybuild.1.en.html) exists to drive setup.py/pyproject builds — for an application with no PyPI ambitions it adds a build system only to fight it back out of `dist-packages`; skip it. (Same conclusion embodied by TLP/system76-power: apps install files, they don't "build a Python distribution".)
- PEP 668: noble's python3.12 is [externally managed](https://peps.python.org/pep-0668/) — no pip at install time, ever; every import must be an apt package. We need only `python3-gi` (+ `gir1.2-gtk-4.0`, `gir1.2-adw-1` typelibs — [packages.ubuntu.com/noble](https://packages.ubuntu.com/noble/python3-gi)). DBus client + daemon both use Gio's DBus (in python3-gi); no python3-dbus, no external fan-math deps.
- `/usr/bin/gigactl`, `/usr/bin/gfan`, `/usr/bin/gkbd` are tiny launchers (`#!/usr/bin/python3` + `sys.path.insert(0, "/usr/lib/gigactl")` + entry call), shipped via `debian/install`.

## Rejected alternatives

| Option | Why not |
|---|---|
| **pkexec wrappers** for GUI actions | Spawns a fresh root process per click, no persistent state — a fan-*curve* needs a resident control loop, so the daemon must exist anyway; two privilege paths = double audit surface. TCC (the only surveyed project using pkexec) does so as an Electron workaround alongside its daemon, and pairs it with hand-rolled install scripts. GUIs-poking-hardware-via-pkexec is the pattern every freedesktop daemon (ppd, upower, fwupd) exists to replace. |
| **Depends: nbfc-linux** | Not in any apt archive (GitHub-only debs) → the dependency is unresolvable at `apt install` time; a deb that can't install from a plain apt run on noble is broken by definition. |
| **Vendor ec_probe binary in our deb** | Ships a GPL-3.0 binary inside an MIT package (fine as mere aggregation but forces us to carry corresponding-source obligations for a 3rd-party blob), pins us to their build, x86-64 only, and imports 100% of their update/security surface for two syscalls' worth of function. |
| **Build nbfc-linux from source in CI** | Same GPL-in-MIT-package and maintenance issues as vendoring, plus a C toolchain + their build system in our CI, to obtain 216 lines we can express in ~80 lines of stdlib Python. |
| **fpm / electron-builder-style packaging** | Bypasses debhelper: no `dh_installsystemd` maintscripts (deb-systemd-helper/-invoke, policy-rc.d compliance), no `${python3:Depends}`, no byte-compile triggers; leads exactly to TCC's `cp` + `systemctl enable` in after_install.sh with units in `/etc/systemd/system`. `dpkg-buildpackage` with a 10-line `debian/rules` is less code than an fpm invocation once maintscripts are counted. |
| **pybuild/setuptools packaging into dist-packages** | gigactl is not an importable library; public module path pollutes the system namespace and pybuild assumes an upstream build system we'd have to invent. Debian Python Policy's private-module route is designed for this case. |
| **DBus bus-activation for the daemon (v1)** | Extra file + `SystemdService=` alias for no gain: the daemon must run from boot anyway (restore LED color, apply fan curve). Can be added later without breaking anything. |
| **`/etc/systemd/system` + manual `systemctl enable` in postinst** | Policy violation (that dir belongs to the admin; maintscripts must use deb-systemd-helper, which dh generates) — see Debian Policy §9.3.2. |
| **Ship a polkit .rules file** | JS rules are for admin-site policy (group grants etc.); package defaults belong in the `.policy` action file. None of the surveyed projects ship rules files. |

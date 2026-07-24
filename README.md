# gigactl

**Fan and RGB keyboard backlight control for Gigabyte G5 / G6 gaming laptops on Linux.**

Gigabyte ships no Linux software for its G-series laptops. The firmware fan curve is lazy (it holds low fan duty until the CPU throttles at ~96 °C), the keyboard backlight can't be changed, and tools like NBFC, fancontrol and OpenRGB don't support these machines.

`gigactl` fixes that with two small, dependency-light CLI tools built on a reverse-engineered Embedded Controller (EC) protocol — these laptops are Clevo-ODM designs, and the EC speaks a Clevo-style mailbox protocol documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

| Tool | What it does |
|------|--------------|
| `gfan` | Manual fan speed (per-fan %), live temps + RPM, instant return to firmware auto |
| `gkbd` | Keyboard backlight color (single-zone RGB), brightness, on/off — persists across reboot and suspend |

Both tools check for the `gigactl` daemon first. When it is running they drive it
over D-Bus — no `sudo`, no `ec_probe`, and no risk of two writers taking turns on
one EC mailbox. With no daemon they write the EC themselves as they always have,
serialising on the same lock the daemon uses. And when they *cannot tell* — no
`busctl`, no answer from the bus — they refuse rather than guess.

## Supported hardware

| Model | Status |
|-------|--------|
| Gigabyte **G6 KF** (i7-13620H / RTX 4060) | ✅ Verified |
| Gigabyte G5 KF / KF5 / MF, G6 MF (same Clevo platform) | 🟢 Expected to work — untested, please report! |
| Other Gigabyte laptops | ❌ Refused by the built-in model guard |

Both tools check your DMI vendor/model before writing anything, and `gfan` verifies the EC actually obeyed each command — if it didn't, it automatically reverts to firmware control. Untested-but-same-family models are allowed with a notice; anything else is refused (override with `GFAN_UNSAFE=1` / `GKBD_UNSAFE=1` at your own risk).

## Install

### The package (recommended)

```bash
sudo apt install ./gigactl_1.0.0_amd64.deb
```

One `.deb` installs everything: the **GigaControl** desktop app, the `gigactld`
daemon that owns EC access, and both CLI tools. The daemon starts immediately and
at every boot; the app appears in your app grid and keeps a tray icon from the
next login. Installation is refused on hardware outside the Gigabyte G5/G6/G7
range (override with `GIGACTL_FORCE_INSTALL=1`).

`sudo apt remove gigactl` stops the daemon and hands the fans back to the
firmware curve; `sudo apt purge gigactl` also drops the saved profile, curves and
keyboard colour.

To build it yourself:

```bash
sudo apt install debhelper dh-python librsvg2-bin desktop-file-utils
python3 -m pytest packaging -q      # checks debian/ against the source tree
python3 -m pytest cli -q            # drives gfan/gkbd against stub busctl/ec_probe
dpkg-buildpackage -us -uc -b        # the .deb lands in the parent directory
```

Releases are built by CI: pushing a `v*` tag builds the `.deb` on a pinned
ubuntu-24.04 runner and attaches it to the GitHub Release, with the notes taken
from that `debian/changelog` entry. The tag has to match the changelog version —
add the entry first, then `git tag v1.0.0 && git push origin v1.0.0`.

### From a source checkout (CLI only)

```bash
git clone https://github.com/smairio/gigactl
cd gigactl
sudo ./install.sh
```

This is the pre-daemon path: it fetches `ec_probe` (from the excellent
[nbfc-linux](https://github.com/nbfc-linux/nbfc-linux) project) if needed,
installs both tools to `/usr/local/bin`, and sets up a systemd service that
restores your keyboard color at boot and after suspend. The package supersedes
that service with the daemon's own restore — and note that `/usr/local/bin` comes
first on `PATH`, so run `sudo ./install.sh --uninstall` before installing the
package if you have used it.

To uninstall: `sudo ./install.sh --uninstall`

## Usage

### Fans

```bash
gfan              # status: temps, duty %, RPM
gfan watch        # live dashboard (Ctrl-C to quit)
gfan 70           # both fans -> 70%
gfan 40 90        # CPU fan 40%, GPU fan 90%
gfan max          # both fans 100%
gfan auto         # give control back to firmware
```

Safety: speeds below 30% are refused while the CPU is ≥ 85 °C (override with `--force`), and everything resets to firmware auto on reboot — no experiment can leave you stuck.

### Keyboard backlight

```bash
gkbd red              # named: red green blue white orange yellow cyan purple pink
gkbd ff6600           # or any hex color
gkbd brightness 40    # 0-100%
gkbd off / gkbd on
gkbd status
```

Your color is saved and re-applied automatically at boot and after suspend.

## How it works (short version)

The EC exposes a command mailbox on the standard ACPI EC ports. Commands are written parameter-first, doorbell-last:

- **Fans**: doorbell `0xC1` — set fan *N* to duty 0–255, or return it to auto. Duty/tach/temperature registers are readable directly.
- **Keyboard**: doorbell `0xCA` — but only after a one-time **master enable**, without which the EC silently ignores every LED command. Colors are sent in **B,R,G order** (not RGB!).

Both quirks cost real debugging time and are documented in full — register maps, byte orders, dead ends and all — in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Credits

- [nbfc-linux](https://github.com/nbfc-linux/nbfc-linux) — the `ec_probe` utility used for EC access
- [wessel-novacustom/clevo-keyboard](https://github.com/wessel-novacustom/clevo-keyboard) (TUXEDO Computers fork) — driver source that revealed the keyboard master-enable command and B,R,G byte order
- [NovaCustom's Clevo backlight article](https://novacustom.com/clevo-keyboard-backlight-control-for-linux/) — the pointer that cracked the keyboard case

## Disclaimer

This software writes to your laptop's Embedded Controller. It is tested on the Gigabyte G6 KF and guarded against running on unsupported hardware, but you use it at your own risk. A reboot always returns the EC to firmware defaults.

## License

MIT

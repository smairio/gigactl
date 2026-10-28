# Gigabyte G5/G6 (Clevo-ODM) EC protocol

Reverse-engineered on a **Gigabyte G6 KF** (i7-13620H, RTX 4060), Ubuntu 24.04, July 2026.
Everything below was verified live on hardware unless marked otherwise.

## The platform

Despite the Gigabyte branding, the G5/G6 series are **Clevo ODM designs**:

- ACPI device `DCHU` with `_HID = CLV0001` (Clevo's ACPI driver interface)
- WMI method GUID `ABBC0F6D-8EA1-11D1-00A0-C90629100000` (the classic Clevo WMI GUID)
- Insyde EC firmware with the Clevo command set

This means the [tuxedo/clevo driver family](https://github.com/wessel-novacustom/clevo-keyboard) command IDs apply (0x63/0x64 fan info, 0x67 KB LED, 0x68/0x69 fan set/auto, …).

## EC access

Standard ACPI EC on ports 0x62/0x66 (use `ec_probe` from nbfc-linux, or the kernel `acpi_ec`/`ec_sys` interfaces). The EC also exposes memory-mapped RAM windows at physical `0xFE0B0100` (bank 1: mirrors the ACPI EC space) and `0xFE0B0300` (bank 3: keyboard/fan-curve block, WMI mailbox).

## Command mailbox

At EC offsets (from the DSDT `ECMD` method):

| Offset | Name | Role |
|--------|------|------|
| `0xF8` | FCMD | command / **doorbell — write LAST, triggers execution** |
| `0xF9` | FDAT | sub-command / first parameter |
| `0xFA` | FBUF | parameter 1 |
| `0xFB` | FBF1 | parameter 2 |
| `0xFC` | FBF2 | parameter 3 |
| `0xFD` | FBF3 | parameter 4 |

Write parameters first, then the command byte to `0xF8`. The EC consumes the command and clears `FCMD` to 0.

## Fan control — doorbell `0xC1` ✅ verified

| Action | FDAT | FBUF |
|--------|------|------|
| Set fan N to duty D (0–255) | N (1=CPU, 2=GPU) | D |
| Return fan N to firmware auto | `0xFF` | N |

Equivalent WMI calls: `WMBB 0 0x68 <fan1_duty | fan2_duty<<8>>` (set), `WMBB 0 0x69 <fan_bitmask>` (auto).

The EC ramps duty gradually toward the target (~11 units/sec downward; upward is much faster).

### Fan/thermal registers (read directly)

| Offset | Field | Meaning |
|--------|-------|---------|
| `0x07` | TMP | CPU temperature (°C) |
| `0x0A`, `0x0B` | DPT1/DPT2 | GPU temperatures (°C) |
| `0xCE` | DUT1 | fan 1 current duty (0–255) |
| `0xCF` | DUT2 | fan 2 current duty |
| `0xD0-D1` | RPM1 | fan 1 tach period, 16-bit big-endian |
| `0xD2-D3` | RPM2 | fan 2 tach period |

**RPM ≈ 2,156,220 / tach_period** (standard Clevo formula; sanity-checked: idle ≈ 2,400 RPM, duty 180 ≈ 5,000 RPM).

Direct writes to `DUT1`/`DUT2` take effect for <1 s before the EC's auto loop overwrites them — use the `0xC1` command instead, which switches the fan to manual.

## Keyboard backlight — doorbell `0xCA` ✅ verified

The G5/G6 KF keyboard is **single-zone RGB** (firmware `GET_SPECS` byte `0x0F` = `0x06` = `CLEVO_KB_BACKLIGHT_TYPE_1_ZONE_RGB`). The factory blue is just the firmware default color `(0,0,200)`.

### Gotcha 1: master enable required

After boot the EC **silently ignores all LED commands** until it receives the keyboard-status-enable:

```
FDAT=0x0C  FBUF=0x3F  doorbell 0xC4      (disable: FBUF=0x20)
```

(WMI form: `WMBB 0 0x67 0xE007F001` enable / `0xE0003001` disable — from the tuxedo driver's `clevo_evaluate_set_keyboard_status`.)

### Gotcha 2: colors are B,R,G — not R,G,B

| Action | FDAT | FBUF | FBF1 | FBF2 |
|--------|------|------|------|------|
| Set color (zone 0 = whole kbd) | `0x03` | **Blue** | **Red** | **Green** |
| Brightness 0–255 | `0x06` | level | – | – |

WMI form: `WMBB 0 0x67 0xF0BBRRGG` (the tuxedo driver swizzles RGB→BRG in `clevo_evaluate_set_rgb_color`; sending `0xF0RRGGBB` on an already-blue keyboard looks like "nothing happened" — hours were lost this way).

Zones 1/2 (`FDAT=0x04/0x05`) and the "all zones" variant (`0x07`) exist for 3-zone keyboards.

### State notes

- Mirror fields at `0xFE0B0380` (`KLC*`, `KBBH`, …) are **stale after boot** — they do *not* reflect live LED commands. Don't use them for verification.
- The EC forgets everything at reboot; re-apply at boot/resume (gigactl ships a systemd unit + sleep hook).
- Fn backlight hotkeys are dead on Linux by design: the EC forwards them as events to an OS driver (`DCHU.HKDR`), which doesn't exist without GCC/tuxedo drivers.

## Useful getters (WMI `WMBB 0 <id> 0`)

| ID | Returns |
|----|---------|
| `0x0D` | GET_SPECS buffer — byte `0x0F` is keyboard backlight type (`0x06` = 1-zone RGB) |
| `0x52` | BIOS features 1 (bit `0x00400000` = 3-zone RGB class, `0x40000000` = white-only) |
| `0x3D` | white/single brightness readback |

## Open questions (reported elsewhere, not reproduced here)

Everything above this section was verified on the G6 KF. What follows are
claims from *other* machines that have not been reproduced here — recorded so
they are not lost, and kept apart from the register tables so they cannot be
mistaken for findings. **No code follows any of them.** The value of this
document is that it separates what was proven on hardware from what was
guessed; folding an unverified third-party report into the tables above would
destroy exactly that.

### Are the fan duty registers swapped on the G5 KF?

[#24](https://github.com/smairio/gigactl/issues/24) reports that `DUT1`/`DUT2`
are reversed on a G5 KF — `0xCF` for the CPU fan and `0xCE` for the GPU fan,
the opposite of the mapping above.

The report is not self-consistent: it states the swap in one section and then
restates the original mapping with an instruction to verify it, and the same
issue says the stock build drives that machine correctly. Two explanations fit
the evidence given, and nothing in it separates them:

- the registers really are reversed in that firmware, or
- the registers are identical and the two **fans** are the other way round in
  that chassis, so the fan this project calls "CPU" is the one cooling the GPU.

The fixes are opposite. The first is a register-map change; the second is no
code change at all.

**What would settle it:** drive one fan alone to a distinctive duty — fan 1 to
80%, say — and then read *both* `0xCE` and `0xCF`. If the register the command
did not target is the one that moved, the mapping is reversed. If the expected
register moved but the wrong fan audibly spun up, the register map is right and
the physical labelling differs.

**A data point from a G5 GE.** The owner of a G5 GE (BIOS FB06) ran the first
half of that test with `tools/fan_check.py`
([#36](https://github.com/smairio/gigactl/pull/36)). Driving fan 1 alone moved
`0xCE` while `0xCF` stayed at 0; driving fan 2 alone moved `0xCF` while `0xCE`
stayed at its firmware value. So on that machine the registers are not
swapped: the command for fan *N* moves duty register *N*, and its tach. Which
*physical* fan spun up was not recorded, so the second half of the test — is
fan 1 really the fan that cools the CPU? — is still open for the G5 GE too. And
it is a different model: it settles nothing for the G5 KF in #24.

### Does the fan command need four parameters?

The same report describes the `0xC1` fan command as requiring `FBF1` set to a
duplicate of the duty byte and `FBF2` set to `0x03`, where this project sends
the duty alone.

The one-parameter form is proven on the G6 KF, and the same reporter says the
unmodified build drives their fans — so the extra bytes may simply be inert
padding copied from a driver that sets both fans in a single call. The EC
ignores leftover scratch bytes, which would make both forms work and neither
observation wrong. Untested here.

The one-parameter form has also been reported working on a G5 GE
([#36](https://github.com/smairio/gigactl/pull/36)): the duty byte alone drove
each fan to the commanded 80 % — its duty register reached the target and its
RPM rose — and the handback returned it to the firmware.

### Reports from other machines

Results from machines nobody here owns, as their owners reported them. They are
evidence about those machines, not findings: nothing in the tables above, and
no code, changes because of them.

#### Gigabyte G5 GE (BIOS FB06, Fedora 44), 26 October 2026 — [#36](https://github.com/smairio/gigactl/pull/36)

Measured by the owner with the scripts in `tools/`. That kernel has no `ec_sys`
module, so every EC access went through `/dev/port`. The probe output is kept
in [`docs/probe/g5-ge-fb06.txt`](probe/g5-ge-fb06.txt).

- **Reading.** At idle the EC reported the CPU at 43 °C, matching coretemp,
  fan 1 at 27 % duty and about 2,372 RPM, and fan 2 stopped. Three samples
  agreed.
- **One fan at a time.** `tools/fan_check.py` drove each fan alone to 80 % for
  five seconds. Fan 1: `DUT1` 27 % → 80 % and about 2,372 → 4,991 RPM, while
  `DUT2` stayed at 0 %; after the handback the firmware brought it back to
  about 30 %. Fan 2: `DUT2` 0 % → 80 % and 0 → about 4,637 RPM, with fan 1
  untouched; back to 0 % after the handback. What this does and does not settle
  is written under the two questions above.
- **The daemon's own loop.** `tools/daemon_smoke.py` ran the daemon's tick with
  the `performance` profile for four ticks. Both fans went from firmware idle
  (27 % / 0 %) to the curve's duty (43 % / 41 %, about 3,360 / 3,100 RPM) with
  no engine fault, and were then handed back to the firmware.
- **Stopping the daemon.** With both fans held at 100 % through the daemon,
  `systemctl stop gigactl` gave them back to the firmware: a read a few seconds
  later showed fan 1 at its idle duty (27 %, about 2,372 RPM) and fan 2
  stopped.
- **Keyboard.** `tools/keyboard_check.py` sent the master enable (`0xC4`), then
  colours (`0xCA`). The keys turned orange at full brightness and then back to
  the factory blue. Both colours came out as intended, which is what the B,R,G
  byte order documented above predicts.

## Dead ends (so you don't repeat them)

- The DSDT's `0xC4`-family fan commands (fixed mode `0x0D/0x0E`, duty subregs `0x03/0x04`, mode presets `0x07–0x0B`) are **ignored** by this EC firmware — legacy Insyde reference code. Fans use `0xC1` only. (`0xC4` sub `0x0C` — the keyboard master enable — is the one live member of that family.)
- Writing colors into the `0xFE0B0380` RAM fields: values stick in RAM, LEDs don't care.
- No USB RGB controller exists (nothing for OpenRGB to find).
- NBFC config format cannot express the 3-write doorbell sequence; that's why these tools exist instead of an NBFC config.

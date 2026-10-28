# Checking a new model

These scripts test gigactl on a Gigabyte laptop that nobody here has tested.
Your results help every owner of the same model.

They talk to the EC: the small controller inside the laptop that runs the fans
and the keyboard light. Run them from a copy of this repository:

```bash
git clone https://github.com/smairio/gigactl
cd gigactl
```

## Before you start

1. **Stop the gigactl daemon**, if you installed the gigactl package:

   ```bash
   sudo systemctl stop gigactl
   ```

   Only one program may write the EC at a time. The tools that change things
   refuse to run while the daemon is running. If you never installed the
   package, there is no daemon, so skip this step.

2. Let the laptop cool down, and close heavy programs.

**When you are done, start the daemon again:**

```bash
sudo systemctl start gigactl
```

It puts back your saved fan profile and keyboard colour.

## The tools

Run them in this order. Each one prints what it is doing.

| Command | What it does | Changes anything? |
|---------|--------------|-------------------|
| `sudo ./tools/probe.sh probe.txt` | Saves facts about the laptop in `probe.txt`: model, BIOS, kernel, sensors, and the fan and temperature registers. | No. It only reads. |
| `sudo python3 tools/ec_read.py` | Reads the temperatures, fan duty and fan speed three times. | No. It only reads. |
| `sudo python3 tools/fan_check.py --fan 1` | Runs fan 1 at 80 % for five seconds, then gives it back to the firmware. Then run it with `--fan 2`. | Yes, for five seconds. |
| `sudo python3 tools/keyboard_check.py` | Turns the keyboard orange for six seconds. Then it sets the colour and brightness back: to what the daemon saved, or to the factory blue. | Yes, for six seconds. |
| `sudo python3 tools/daemon_smoke.py` | Runs the daemon's own fan loop with the `performance` profile for about eight seconds, then gives the fans back to the firmware. | Yes, for eight seconds. |

`daemon_smoke.py` needs PyGObject: `python3-gi` on Ubuntu, `python3-gobject`
on Fedora.

The tools use the EC the same way the daemon does: through the kernel's
`ec_sys` module when it is loaded, otherwise through `/dev/port` (Fedora's
kernel has no `ec_sys`).

### Which fan spins? This matters most

When you run `fan_check.py --fan 1`, **listen, and feel for the air.** Write
down which fan speeds up: left or right (looking at the open screen), and if
you can tell, the one near the CPU or the one near the graphics chip. Then do
the same for `--fan 2`.

This answers an open question in [docs/PROTOCOL.md](../docs/PROTOCOL.md). On
some models the two fans may be the other way round, and the registers alone
cannot show that. Only your ears can.

## Safety

- The tools that change things check first. They refuse to run when:
  - the gigactl daemon is running, or they cannot tell whether it is;
  - the laptop is not a Gigabyte G5, G6 or G7 (`GIGACTL_TOOLS_UNSAFE=1`
    overrides this one check, at your own risk);
  - the kernel's EC interface is read-only.

  When a tool refuses, it says why. Read the message.
- `fan_check.py` does not start if the CPU or GPU is already at 75 °C or more.
  80 % is more than the firmware normally uses below that, so the worst case
  should be a loud laptop for a few seconds, not a hotter one.
- The fan tools always give the fans back to the firmware when they end, even
  if you press Ctrl-C. So does stopping the daemon.
- A reboot always resets the EC to the firmware defaults.

## What to send

Open an issue at <https://github.com/smairio/gigactl/issues> and paste:

1. Your laptop model, BIOS version and Linux distribution.
2. The file `probe.txt`. Read it first: the serial number and system UUID are
   removed, but make sure you are happy to share the rest.
3. The full output of every tool you ran.
4. **Which physical fan spun** for `--fan 1`, and for `--fan 2`.
5. What the keyboard did: did it turn orange, and did it come back?

# gigactld

The **GigaControl daemon** — the single root process that owns Embedded
Controller access on Gigabyte G5/G6 laptops and exposes it over the D-Bus
**system** bus. See [`docs/PROTOCOL.md`](../docs/PROTOCOL.md) for the EC protocol
and issue #7 for the API design.

## Status

Ticket #12 — **daemon spine + live telemetry**. Implemented:

- EC access layer (`ec_sys` backend, `/dev/port` fallback, flock transactions) — `ec.py`
- Telemetry decode (CPU/GPU temp, fan RPM, duty) — `telemetry.py`
- D-Bus system service emitting a `Telemetry(uuuuuu)` signal every 2 s — `service.py`

Control methods, polkit, the curve engine and keyboard paths arrive in later tickets.

## Run (dev)

```bash
# one-time: let root own the bus name
sudo install -m644 data/io.github.smairio.gigactl.conf /etc/dbus-1/system.d/
sudo systemctl reload dbus

# run in the foreground
sudo python3 -m gigactld

# watch the telemetry signal from another terminal
dbus-monitor --system "interface='io.github.smairio.gigactl.Control'"
# or: busctl introspect io.github.smairio.gigactl /io/github/smairio/gigactl
```

## Test

```bash
cd daemon && python3 -m pytest -q
```

The pure logic (EC file access, telemetry decode, the flock) is unit-tested
against fakes — no hardware needed. The `/dev/port` backend and the live D-Bus
loop are verified manually on a real G6 KF.

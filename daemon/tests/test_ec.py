"""EC access layer — the ec_sys backend and the flock transaction are TDD'd here
against a temp file. The /dev/port backend needs real hardware and is not unit-tested."""
import fcntl

from gigactld.ec import EcSysBackend, EmbeddedController


def _fill(path, mutate=None):
    data = bytearray(range(256))  # register N holds value N by default
    if mutate:
        mutate(data)
    path.write_bytes(bytes(data))
    return path


def test_ecsys_reads_byte_at_offset(tmp_path):
    io = _fill(tmp_path / "io")
    b = EcSysBackend(str(io))
    assert b.read(0x07) == 0x07
    assert b.read(0xCE) == 0xCE


def test_ecsys_writes_only_target_byte(tmp_path):
    io = _fill(tmp_path / "io")
    b = EcSysBackend(str(io))
    b.write(0x10, 0xAB)
    raw = (tmp_path / "io").read_bytes()
    assert raw[0x10] == 0xAB
    assert raw[0x11] == 0x11  # neighbour untouched


def test_ecsys_available_reflects_path(tmp_path):
    io = _fill(tmp_path / "io")
    assert EcSysBackend.available(str(io)) is True
    assert EcSysBackend.available(str(tmp_path / "nope")) is False


def test_controller_read_u8_via_backend(tmp_path):
    io = _fill(tmp_path / "io")
    ec = EmbeddedController(backend=EcSysBackend(str(io)),
                            lock_path=str(tmp_path / "lock"))
    with ec.transaction():
        assert ec.read_u8(0x0A) == 0x0A


def test_transaction_takes_an_exclusive_lock(tmp_path):
    io = _fill(tmp_path / "io")
    lock = tmp_path / "lock"
    ec = EmbeddedController(backend=EcSysBackend(str(io)), lock_path=str(lock))
    with ec.transaction():
        # while the transaction holds LOCK_EX, a non-blocking grab must fail
        with open(lock) as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                fcntl.flock(probe, fcntl.LOCK_UN)
            except BlockingIOError:
                got = False
    assert got is False

"""Profile persistence — save/load/restore round-trips, no hardware."""
from gigactld import state, curve


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")
    data = {"profile": "balanced", "linked": True, "cpu": [], "gpu": []}
    state.save(p, data)
    assert state.load(p) == data


def test_load_missing_returns_none(tmp_path):
    assert state.load(str(tmp_path / "absent.json")) is None


def test_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not json")
    assert state.load(str(p)) is None


def test_snapshot_reflects_active_profile():
    e = curve.ProfileEngine()
    e.set_profile("quiet")
    assert state.snapshot(e)["profile"] == "quiet"


def test_restore_predefined_profile():
    e = curve.ProfileEngine()
    state.restore(e, {"profile": "performance", "linked": True, "cpu": [], "gpu": []})
    assert e.profile == "performance"


def test_restore_custom_curve_survives_roundtrip(tmp_path):
    src = curve.ProfileEngine()
    src.set_custom(cpu_points=[(40, 30), (95, 100)], gpu_points=None, linked=True)
    p = str(tmp_path / "state.json")
    state.save(p, state.snapshot(src))

    dst = curve.ProfileEngine()
    state.restore(dst, state.load(p))
    assert dst.profile == "custom"
    # the restored curve behaves like the original
    assert dst.decide(curve.CPU_FAN, 95) == src.decide(curve.CPU_FAN, 95)

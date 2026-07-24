"""Model classification + banner text — pure, no DMI hardware needed."""
from gigactl_gui import model
from gigactl_gui.model import Support


def test_verified_is_g6kf():
    assert model.classify("GIGABYTE", "G6 KF") is Support.VERIFIED


def test_family_members_are_expected():
    for name in ("G5 MF", "G6 KE", "G7 GD"):
        assert model.classify("GIGABYTE", name) is Support.EXPECTED


def test_non_gigabyte_is_unsupported():
    assert model.classify("Dell Inc.", "G6 KF") is Support.UNSUPPORTED


def test_long_vendor_string_is_matched():
    # Prefix match, like gfan/gkbd: some boards report the long vendor string.
    assert model.classify("Gigabyte Technology Co., Ltd.", "G6 KF") is Support.VERIFIED
    assert model.classify("GIGABYTE Technology", "G5 MF") is Support.EXPECTED


def test_other_gigabyte_model_is_unsupported():
    assert model.classify("GIGABYTE", "AERO 15") is Support.UNSUPPORTED


def test_classify_ignores_case_and_whitespace():
    assert model.classify(" gigabyte ", " g6 kf ") is Support.VERIFIED


def test_banner_text_per_support():
    assert "fully verified" in model.Model("GIGABYTE", "G6 KF", Support.VERIFIED).banner
    assert "expected to work" in model.Model("GIGABYTE", "G5 MF", Support.EXPECTED).banner
    assert "not a supported" in model.Model("Dell", "XPS", Support.UNSUPPORTED).banner


def test_detect_reads_injected_paths(tmp_path):
    v = tmp_path / "vendor"; v.write_text("GIGABYTE\n")
    m = tmp_path / "model"; m.write_text("G6 KF\n")
    got = model.detect(str(v), str(m))
    assert got.support is Support.VERIFIED
    assert got.name == "G6 KF"


def test_detect_missing_files_is_unsupported(tmp_path):
    got = model.detect(str(tmp_path / "absent"), str(tmp_path / "absent2"))
    assert got.support is Support.UNSUPPORTED

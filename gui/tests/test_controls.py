"""Pure control-surface logic: the swatch palette, profile metadata, and the
plain-English wording of a refused write."""
import pytest

from gigactl_gui import errors, palette, profiles


# --- palette -----------------------------------------------------------------

def test_named_colours_are_rgb_triples():
    for name, rgb in palette.SWATCHES:
        assert len(rgb) == 3
        assert all(0 <= c <= 255 for c in rgb), f"{name} out of range"


def test_palette_covers_the_gkbd_names():
    names = {name.lower() for name, _ in palette.SWATCHES}
    assert {"red", "green", "blue", "white", "yellow", "purple"} <= names


def test_name_for_finds_an_exact_match():
    assert palette.name_for((255, 0, 0)) == "Red"
    assert palette.name_for((0, 0, 255)) == "Blue"


def test_name_for_returns_none_for_a_custom_colour():
    assert palette.name_for((17, 99, 123)) is None


def test_blue_matches_the_daemon_default():
    # a fresh daemon reports (0,0,255); that must light up the Blue swatch
    assert palette.name_for((0, 0, 255)) == "Blue"


def test_hex_of_formats_for_css():
    assert palette.hex_of((255, 0, 0)) == "#ff0000"
    assert palette.hex_of((0, 0, 255)) == "#0000ff"


# --- profiles ----------------------------------------------------------------

def test_selectable_profiles_are_in_design_order():
    ids = [p.id for p in profiles.SELECTABLE]
    assert ids == ["quiet", "balanced", "performance", "max", "firmware"]


def test_every_selectable_profile_has_label_and_subtitle():
    for p in profiles.SELECTABLE:
        assert p.label and p.subtitle


def test_firmware_is_marked_as_the_yield_state():
    firmware = next(p for p in profiles.SELECTABLE if p.id == "firmware")
    assert firmware.is_firmware is True
    assert all(not p.is_firmware for p in profiles.SELECTABLE if p.id != "firmware")


def test_display_name_covers_states_without_a_button():
    # custom/manual are real daemon profiles the row cannot select, but the
    # "Now:" label must still name them honestly
    assert profiles.display_name("balanced") == "Balanced"
    assert profiles.display_name("custom") == "Custom"
    assert profiles.display_name("manual") == "Manual"
    assert profiles.display_name("firmware") == "Firmware"


def test_display_name_falls_back_to_the_raw_id():
    assert profiles.display_name("something-new") == "something-new"


# --- error wording -----------------------------------------------------------

def test_refused_write_is_explained_plainly():
    msg = errors.human_message(
        "GDBus.Error:io.github.smairio.gigactl.Error.WriteRejected: "
        "fan(s) [1] did not accept the duty; all reverted to firmware auto")
    assert "firmware" in msg.lower()
    assert "GDBus" not in msg and "io.github" not in msg


def test_not_authorized_is_explained_plainly():
    msg = errors.human_message(
        "GDBus.Error:io.github.smairio.gigactl.Error.NotAuthorized: "
        "not authorized to control the fans")
    assert "allowed" in msg.lower() or "permission" in msg.lower()


def test_daemon_missing_is_explained_plainly():
    msg = errors.human_message(
        "GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: "
        "The name io.github.smairio.gigactl was not provided by any .service files")
    assert "not running" in msg.lower()


def test_unknown_error_falls_back_to_its_detail():
    msg = errors.human_message("GDBus.Error:some.Other.Thing: the disk is on fire")
    assert msg == "the disk is on fire"


@pytest.mark.parametrize("raw", ["", "no colons here", "GDBus.Error:bare.Name:"])
def test_unparseable_errors_still_produce_something(raw):
    assert errors.human_message(raw)

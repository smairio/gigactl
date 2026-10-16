"""The keyboard-effect choices, their wording, and the preview's frame maths —
including that the preview computes exactly what the daemon does, so the two
processes never disagree about the colour on the keys."""
import sys
from pathlib import Path

import pytest

from gigactl_gui import backlight, lighting, palette
from gigactl_gui.client import EffectsSnapshot

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))
from gigactld import keyboard as daemon_kb  # noqa: E402
from gigactld import lighting as daemon_lt  # noqa: E402


# --- agreement with the daemon -------------------------------------------------

def test_effect_names_match_the_daemon():
    assert {e.id for e in lighting.EFFECTS} == set(daemon_kb.EFFECTS)


def test_default_speeds_match_the_daemon():
    assert lighting.DEFAULT_SPEED == daemon_kb.DEFAULT_PERIOD_S


def test_every_offered_speed_and_timeout_is_one_the_daemon_accepts():
    for effect, speeds in lighting.SPEEDS.items():
        for seconds, _label in speeds:
            assert daemon_kb.check_effect(effect, seconds) == seconds
    for seconds in lighting.IDLE_CHOICES:
        assert daemon_kb.check_idle_timeout(seconds) == seconds


def test_default_speeds_are_on_the_menu():
    for effect, default in lighting.DEFAULT_SPEED.items():
        assert default in {s for s, _ in lighting.SPEEDS[effect]}


@pytest.mark.parametrize("wall", [0.0, 7.3, 151.0, 299.9, 1_760_000_123.4])
def test_preview_cycle_colour_matches_the_daemon(wall):
    rgb, _ = backlight.frame(lighting.CYCLE, 300, (0, 0, 0), 1.0, wall)
    assert rgb == daemon_lt.cycle_rgb(wall, 300)


@pytest.mark.parametrize("temp", [None, 30, 50, 66, 77, 88, 99])
def test_preview_heat_colour_matches_the_daemon(temp):
    assert backlight.temperature_rgb(temp) == daemon_lt.temperature_rgb(temp)


def test_temperature_stops_match_the_daemon():
    assert backlight.TEMP_STOPS == daemon_lt.TEMP_STOPS


# --- preview frames -------------------------------------------------------------

def test_static_frame_is_the_chosen_colour():
    assert backlight.frame(lighting.STATIC, 0, (1, 2, 3), 0.7, 99.0) == ((1, 2, 3), 0.7)


def test_breathing_frame_dips_and_peaks():
    _, low = backlight.frame(lighting.BREATHE, 4, (1, 2, 3), 1.0, 0.0)
    _, high = backlight.frame(lighting.BREATHE, 4, (1, 2, 3), 1.0, 2.0)
    assert low < 0.05 and high == pytest.approx(1.0)


def test_dark_stays_dark_whatever_the_effect():
    for effect in (lighting.CYCLE, lighting.BREATHE, lighting.TEMPERATURE):
        assert backlight.frame(effect, 6, (1, 2, 3), 0.0, 1.0)[1] == 0.0


def test_only_moving_effects_animate():
    assert backlight.is_animated(lighting.CYCLE)
    assert backlight.is_animated(lighting.BREATHE)
    assert not backlight.is_animated(lighting.STATIC)
    assert not backlight.is_animated(lighting.TEMPERATURE)  # moves with telemetry


# --- menus and words ---------------------------------------------------------------

def test_duration_words():
    assert lighting.duration_text(0) == "Never"
    assert lighting.duration_text(10) == "10 seconds"
    assert lighting.duration_text(60) == "1 minute"
    assert lighting.duration_text(300) == "5 minutes"
    assert lighting.duration_text(3600) == "1 hour"
    assert lighting.duration_text(90) == "1 min 30 s"


def test_idle_menu_keeps_a_value_set_from_the_cli():
    assert lighting.idle_choices(30) == list(lighting.IDLE_CHOICES)
    choices = lighting.idle_choices(45)
    assert 45 in choices and choices == sorted(choices)


def test_speed_menu_keeps_an_off_menu_value_and_skips_static():
    assert lighting.speed_choices(lighting.STATIC, 0) == []
    assert lighting.speed_choices(lighting.TEMPERATURE, 0) == []
    cycle = lighting.speed_choices(lighting.CYCLE, 120)
    assert 120 in [s for s, _ in cycle]


def test_effects_snapshot_maps_the_property_tuple():
    e = EffectsSnapshot.from_tuple(("breathe", 6, 30, True))
    assert (e.effect, e.period_s, e.idle_timeout_s, e.notify_flash) == ("breathe", 6, 30, True)
    with pytest.raises(ValueError):
        EffectsSnapshot.from_tuple(("static", 0))


# --- palette ---------------------------------------------------------------------

def test_palette_has_plenty_of_unique_colours():
    names = [n for n, _ in palette.SWATCHES]
    values = [v for _, v in palette.SWATCHES]
    assert len(names) >= 20
    assert len(set(names)) == len(names)
    assert len(set(values)) == len(values)  # every swatch is a distinct colour


def test_palette_fills_the_grid_exactly():
    assert len(palette.SWATCHES) % palette.GRID_COLUMNS == 0


def _hue(rgb):
    import colorsys
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360


def test_vivid_presets_walk_the_colour_wheel_in_order():
    """Red, orange, yellow, green, cyan, blue, purple, pink — never doubling
    back (the complaint that prompted this test)."""
    hues = [_hue(rgb) for _, rgb in palette.VIVID]
    assert hues == sorted(hues)
    assert all(s == 1.0 for s in (
        __import__("colorsys").rgb_to_hsv(*(c / 255 for c in rgb))[1]
        for _, rgb in palette.VIVID))


def test_soft_row_runs_warm_to_white_to_cool():
    names = [n for n, _ in palette.SOFT]
    white = names.index("White")
    warm = [(n, rgb) for n, rgb in palette.SOFT[:white]]
    cool = [(n, rgb) for n, rgb in palette.SOFT[white + 1:]]
    # warm side: from the red end (rose wraps just below 360) towards white
    warm_hues = [(_hue(rgb) + 20) % 360 for _, rgb in warm]
    assert warm_hues == sorted(warm_hues)
    assert [_hue(rgb) for _, rgb in cool] == sorted(_hue(rgb) for _, rgb in cool)


def test_vivid_rows_are_whole():
    assert len(palette.VIVID) % palette.GRID_COLUMNS == 0

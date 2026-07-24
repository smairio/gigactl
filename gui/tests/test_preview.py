"""The keyboard preview's pure part: how brightly it should glow."""
from gigactl_gui.preview import intensity


def test_disabled_backlight_does_not_glow():
    assert intensity(enabled=False, brightness_pct=100) == 0.0


def test_zero_brightness_does_not_glow():
    # a literal depiction: 0 % really is dark (DESIGN.md)
    assert intensity(enabled=True, brightness_pct=0) == 0.0


def test_glow_tracks_brightness():
    assert intensity(enabled=True, brightness_pct=50) == 0.5
    assert intensity(enabled=True, brightness_pct=100) == 1.0


def test_out_of_range_brightness_is_clamped():
    assert intensity(enabled=True, brightness_pct=150) == 1.0
    assert intensity(enabled=True, brightness_pct=-10) == 0.0

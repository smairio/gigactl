"""The semantic temperature scale — band thresholds, colours, gauge math."""
from gigactl_gui import temperature as t
from gigactl_gui.temperature import Band


def test_band_thresholds():
    assert t.band(50) is Band.COOL
    assert t.band(74.9) is Band.COOL
    assert t.band(75) is Band.WARM       # warm starts at 75
    assert t.band(89) is Band.WARM
    assert t.band(90) is Band.HOT        # hot starts at 90
    assert t.band(97) is Band.HOT


def test_color_hex_dark_variant_differs():
    assert t.color_hex(95) == "#e01b24"
    assert t.color_hex(95, dark=True) == "#ff6b6b"
    assert t.color_hex(50) != t.color_hex(80)  # cool vs warm


def test_gauge_fraction_clamps_and_scales():
    assert t.gauge_fraction(30) == 0.0
    assert t.gauge_fraction(100) == 1.0
    assert t.gauge_fraction(10) == 0.0    # below range clamps
    assert t.gauge_fraction(130) == 1.0   # above range clamps
    assert abs(t.gauge_fraction(65) - 0.5) < 1e-9


def test_summary_reflects_hottest_sensor():
    text, band = t.summary(cpu_c=50, gpu_c=95)
    assert band is Band.HOT and text == "Running hot"
    text, band = t.summary(cpu_c=60, gpu_c=55)
    assert band is Band.COOL and text == "Running cool"

"""Curve-editor maths: prediction, monotonic dragging, and the graph mapping.
All pure — no GTK, no daemon."""
import pytest

from gigactl_gui import curves

BALANCED = [(40, 25), (55, 35), (70, 55), (85, 80), (95, 100)]


# --- what the fans will actually do -----------------------------------------

def test_duty_for_interpolates_between_points():
    assert curves.duty_for(BALANCED, 40) == 25
    assert curves.duty_for(BALANCED, 55) == 35
    assert curves.duty_for(BALANCED, 62.5) == 45  # midpoint of 55->70 (35->55)
    assert curves.duty_for(BALANCED, 62) == 44    # and it really interpolates


def test_duty_for_clamps_outside_the_curve():
    assert curves.duty_for(BALANCED, 10) == 25    # below the first point
    assert curves.duty_for(BALANCED, 120) == 100  # above the last


def test_predicted_applies_the_safety_floor():
    lazy = [(40, 0), (55, 0), (70, 0), (85, 0), (95, 0)]
    # the daemon never idles a fan while the component is hot, so the editor
    # must not promise 0 % either
    assert curves.predicted(lazy, 90) == curves.FLOOR_PCT
    assert curves.predicted(lazy, 84) == 0  # below the floor temp, 0 stands


def test_predicted_floors_on_the_hotter_of_fan_and_cpu():
    lazy = [(40, 0), (55, 0), (70, 0), (85, 0), (95, 0)]
    # the GPU is cool but the CPU is hot: the daemon floors the fan anyway
    assert curves.predicted(lazy, 50, cpu_temp=95) == curves.FLOOR_PCT
    # both cool: no floor
    assert curves.predicted(lazy, 50, cpu_temp=60) == 0


def test_annotation_names_the_temperature_and_the_result():
    text = curves.annotation("CPU", 91, BALANCED)
    assert "91" in text and "%" in text
    assert str(curves.predicted(BALANCED, 91)) in text


# --- dragging ---------------------------------------------------------------

def test_move_point_sets_the_dragged_value():
    moved = curves.move_point(BALANCED, 2, temp=72, duty=60)
    assert moved[2] == (72, 60)


def test_move_point_keeps_temperatures_strictly_increasing():
    # drag the middle point left past its neighbour
    moved = curves.move_point(BALANCED, 2, temp=10, duty=55)
    assert moved[1][0] < moved[2][0] < moved[3][0]


def test_move_point_keeps_duty_non_decreasing():
    # a curve that fell as it got hotter would be silently "fixed" by the
    # daemon, so refuse it here instead of showing the user a lie
    moved = curves.move_point(BALANCED, 2, temp=70, duty=5)
    assert moved[1][1] <= moved[2][1] <= moved[3][1]


def test_move_point_clamps_to_the_axis_range():
    low = curves.move_point(BALANCED, 0, temp=-50, duty=-20)
    assert low[0][0] == curves.TEMP_MIN and low[0][1] == 0
    high = curves.move_point(BALANCED, 4, temp=500, duty=500)
    assert high[4][0] <= curves.TEMP_MAX and high[4][1] == 100


def test_move_point_leaves_other_points_alone():
    moved = curves.move_point(BALANCED, 2, temp=72, duty=60)
    assert moved[0] == BALANCED[0] and moved[4] == BALANCED[4]


def test_move_point_rejects_a_bad_index():
    with pytest.raises(IndexError):
        curves.move_point(BALANCED, 9, temp=50, duty=50)


# --- graph mapping ----------------------------------------------------------

def test_pixel_roundtrip():
    for temp, duty in ((40, 0), (70, 50), (100, 100)):
        x, y = curves.to_pixel(temp, duty, 300, 200)
        back_t, back_d = curves.from_pixel(x, y, 300, 200)
        assert (round(back_t), round(back_d)) == (temp, duty)


def test_pixel_origin_is_bottom_left():
    x0, y0 = curves.to_pixel(curves.TEMP_MIN, 0, 300, 200)
    assert (x0, y0) == (0.0, 200.0)          # coldest, slowest
    x1, y1 = curves.to_pixel(curves.TEMP_MAX, 100, 300, 200)
    assert (x1, y1) == (300.0, 0.0)          # hottest, fastest


def test_from_pixel_survives_a_zero_sized_area():
    # a gesture can fire before the first allocation; dividing by zero there
    # would escape into the GLib dispatcher
    assert curves.from_pixel(10, 10, 0, 0) == (float(curves.TEMP_MIN), 0.0)


def test_from_pixel_clamps_outside_the_area():
    temp, duty = curves.from_pixel(-40, 400, 300, 200)
    assert temp == curves.TEMP_MIN and duty == 0


def test_hit_test_finds_the_nearest_point_within_reach():
    x, y = curves.to_pixel(*BALANCED[2], 300, 200)
    assert curves.hit_test(BALANCED, x + 3, y - 3, 300, 200) == 2


def test_hit_test_misses_when_far_away():
    assert curves.hit_test(BALANCED, 5, 5, 300, 200) is None


# --- the editor's starting shape ---------------------------------------------

def test_default_seed_is_a_valid_rising_curve():
    seed = curves.DEFAULT_SEED
    assert len(seed) == curves.POINTS
    temps = [t for t, _ in seed]
    duties = [d for _, d in seed]
    assert temps == sorted(temps) and len(set(temps)) == len(temps)
    assert duties == sorted(duties)


def test_seeded_from_takes_the_daemon_shape_when_it_has_one():
    assert curves.seeded_from(BALANCED) == BALANCED


def test_seeded_from_falls_back_when_the_daemon_drives_nothing():
    # firmware/manual report empty lists; the editor still needs a shape to show
    assert curves.seeded_from([]) == curves.DEFAULT_SEED
    assert curves.seeded_from(None) == curves.DEFAULT_SEED


def test_seeded_from_normalises_a_short_curve():
    got = curves.seeded_from([(40, 20), (95, 90)])
    assert len(got) == curves.POINTS
    assert curves.is_valid(got)


def test_seeded_from_leaves_a_valid_curve_untouched():
    # even one that starts colder than the drawn range: the daemon accepts such
    # a curve, so rewriting it here would show (and later send) a shape the user
    # never chose
    cold = [(20, 15), (45, 30), (65, 50), (85, 75), (95, 100)]
    assert curves.is_valid(cold)
    assert curves.seeded_from(cold) == cold


def test_is_valid_rejects_malformed_curves():
    assert not curves.is_valid([(40, 20), (95, 90)])                 # too few
    assert not curves.is_valid([(40, 20), (40, 30), (70, 40),
                                (85, 50), (95, 60)])                 # flat temps
    assert not curves.is_valid([(40, 50), (55, 40), (70, 60),
                                (85, 80), (95, 100)])                # falling duty


def test_normalise_uses_the_daemon_lower_bound_not_the_drawn_one():
    # a malformed curve is coerced by the daemon's rules, which allow the first
    # point below the drawn range
    got = curves.seeded_from([(5, 10), (5, 20), (70, 40), (85, 50), (95, 60)])
    assert got[0][0] == curves.DAEMON_TEMP_MIN or got[0][0] < curves.TEMP_MIN

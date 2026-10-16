"""The rainbow square's colour maths — where a point is, and back again."""
import pytest

from gigactl_gui.colour_square import position_of, rgb_at


def test_top_edge_is_the_full_colour_wheel():
    assert rgb_at(0.0, 0.0) == (255, 0, 0)
    assert rgb_at(1 / 3, 0.0) == (0, 255, 0)
    assert rgb_at(2 / 3, 0.0) == (0, 0, 255)


def test_bottom_edge_is_white():
    for x in (0.0, 0.3, 0.9):
        assert rgb_at(x, 1.0) == (255, 255, 255)


def test_never_dark():
    # brightness is the slider's job, so nothing on the square is dim
    for x in (0.0, 0.2, 0.5, 0.8):
        for y in (0.0, 0.5, 1.0):
            assert max(rgb_at(x, y)) == 255


def test_out_of_bounds_drags_are_clamped():
    assert rgb_at(-1, -1) == rgb_at(0, 0)
    assert rgb_at(2, 2) == rgb_at(1, 1)


@pytest.mark.parametrize("rgb", [(255, 0, 0), (0, 140, 255), (255, 170, 90),
                                 (170, 110, 255)])
def test_a_colour_lands_where_it_came_from(rgb):
    assert rgb_at(*position_of(rgb)) == rgb

import pytest

from wyze_view.grid import grid_dims, grid_position


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, (0, 0)),
        (1, (1, 1)),
        (2, (1, 2)),
        (3, (2, 2)),
        (4, (2, 2)),
        (5, (3, 3)),
        (9, (3, 3)),
        (10, (4, 4)),
        (16, (4, 4)),
        (17, (4, 5)),
        (20, (4, 5)),
    ],
)
def test_grid_dims(n, expected):
    assert grid_dims(n) == expected


def test_grid_position_fills_rows_left_to_right():
    rows, cols = grid_dims(17)
    assert grid_position(0, rows, cols) == (0, 0)
    assert grid_position(4, rows, cols) == (0, 4)
    assert grid_position(5, rows, cols) == (1, 0)
    assert grid_position(16, rows, cols) == (3, 1)

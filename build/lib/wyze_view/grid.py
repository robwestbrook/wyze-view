from __future__ import annotations

import math


def grid_dims(n: int) -> tuple[int, int]:
    if n <= 0:
        return (0, 0)
    if n == 1:
        return (1, 1)
    if n == 2:
        return (1, 2)
    if n <= 4:
        return (2, 2)
    if n <= 9:
        return (3, 3)
    if n <= 16:
        return (4, 4)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return (rows, cols)


def grid_position(index: int, rows: int, cols: int) -> tuple[int, int]:
    if cols <= 0:
        return (0, 0)
    return (index // cols, index % cols)

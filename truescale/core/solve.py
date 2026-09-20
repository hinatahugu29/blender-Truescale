"""小さな数値計算。

型紙の配置や注記の向きを決めるために使う。
Blender の Mesh には触らず、座標だけを受け取る。
"""

import math

from mathutils import Vector


def nearest_point_on_xy_segment(point, a, b):
    ab = b - a
    denom = ab.length_squared

    if denom <= 1e-20:
        return a.copy(), 0.0

    t = (point - a).dot(ab) / denom
    t = max(0.0, min(1.0, t))
    return a + ab * t, t


def solve_3x3_regularized(matrix, rhs, ridge=1e-9):
    m = [
        [
            float(matrix[row][col])
            + (float(ridge) if row == col else 0.0)
            for col in range(3)
        ]
        + [float(rhs[row])]
        for row in range(3)
    ]

    for col in range(3):
        pivot = max(
            range(col, 3),
            key=lambda row: abs(m[row][col]),
        )
        if abs(m[pivot][col]) <= 1e-14:
            return None

        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]

        div = m[col][col]
        for j in range(col, 4):
            m[col][j] /= div

        for row in range(3):
            if row == col:
                continue

            factor = m[row][col]
            if abs(factor) <= 1e-20:
                continue

            for j in range(col, 4):
                m[row][j] -= factor * m[col][j]

    return Vector((m[0][3], m[1][3], m[2][3]))


def solve_2d_gradient(samples):
    """Fit scalar ~= ax*x + ay*y + c and return 2D gradient (ax, ay).

    samples: [(x, y, scalar), ...]
    """
    if len(samples) < 3:
        return None

    n = float(len(samples))
    sx = sum(s[0] for s in samples)
    sy = sum(s[1] for s in samples)
    sz = sum(s[2] for s in samples)

    sxx = sum(s[0] * s[0] for s in samples)
    syy = sum(s[1] * s[1] for s in samples)
    sxy = sum(s[0] * s[1] for s in samples)
    sxz = sum(s[0] * s[2] for s in samples)
    syz = sum(s[1] * s[2] for s in samples)

    # Solve normal equations:
    # [sxx sxy sx] [ax]   [sxz]
    # [sxy syy sy] [ay] = [syz]
    # [sx  sy  n ] [ c]   [ sz ]
    m = [
        [sxx, sxy, sx, sxz],
        [sxy, syy, sy, syz],
        [sx,  sy,  n,  sz ],
    ]

    # Gaussian elimination with partial pivoting.
    for col in range(3):
        pivot = max(
            range(col, 3),
            key=lambda row: abs(m[row][col]),
        )
        if abs(m[pivot][col]) <= 1e-12:
            return None

        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]

        div = m[col][col]
        for j in range(col, 4):
            m[col][j] /= div

        for row in range(3):
            if row == col:
                continue
            factor = m[row][col]
            if abs(factor) <= 1e-20:
                continue
            for j in range(col, 4):
                m[row][j] -= factor * m[col][j]

    return Vector((m[0][3], m[1][3], 0.0))


def bezier_point(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        p0 * (u ** 3)
        + p1 * (3.0 * u * u * t)
        + p2 * (3.0 * u * t * t)
        + p3 * (t ** 3)
    )

"""Equivalence tests for the vectorised state-boundary geometry helpers.

These lock the NumPy fast paths (`_ring_visible_np`, `_rdp_keep_mask_np`,
`geodetic_to_enu_batch`) to the original scalar algorithms they replaced, so a
future change can't silently alter what gets culled or simplified.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from pocketscope.core.geo import ecef_to_enu, geodetic_to_ecef, geodetic_to_enu_batch
from pocketscope.render.view_ppi import _rdp_keep_mask_np, _ring_visible_np


def _ref_cull(en_pts: list[tuple[float, float]], r2: float) -> bool:
    """Original scalar three-phase cull, operating on ENU points."""
    keep = False
    for x1, y1 in en_pts:
        if (x1 * x1 + y1 * y1) <= r2:
            keep = True
    if not keep:
        for i in range(len(en_pts)):
            x1, y1 = en_pts[i]
            x2, y2 = en_pts[(i + 1) % len(en_pts)]
            dx = x2 - x1
            dy = y2 - y1
            seg_len2 = dx * dx + dy * dy
            if seg_len2 <= 1e-12:
                d2 = x1 * x1 + y1 * y1
            else:
                t = -(x1 * dx + y1 * dy) / seg_len2
                if t < 0:
                    px, py = x1, y1
                elif t > 1:
                    px, py = x2, y2
                else:
                    px = x1 + t * dx
                    py = y1 + t * dy
                d2 = px * px + py * py
            if d2 <= r2:
                keep = True
                break
    if not keep and len(en_pts) >= 3:
        crossings = 0
        for i in range(len(en_pts)):
            x1, y1 = en_pts[i]
            x2, y2 = en_pts[(i + 1) % len(en_pts)]
            if (y1 <= 0 < y2) or (y2 <= 0 < y1):
                x_int = x1 + (0 - y1) * (x2 - x1) / (y2 - y1)
                if x_int >= 0:
                    crossings += 1
        if (crossings % 2) == 1:
            keep = True
    return keep


def _ref_rdp_keep(en_pts: list[tuple[float, float]], tol_m: float) -> list[int]:
    """Original recursive RDP, returning sorted kept indices."""

    def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _rdp(indices: list[int]) -> list[int]:
        if len(indices) <= 2:
            return indices
        first, last = indices[0], indices[-1]
        a = en_pts[first]
        b = en_pts[last]
        seg_len = _dist(a, b)
        max_d = -1.0
        max_i = None
        for i in indices[1:-1]:
            p = en_pts[i]
            if seg_len == 0:
                d = _dist(a, p)
            else:
                num = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1]))
                d = num / max(1e-12, seg_len)
            if d > max_d:
                max_d = d
                max_i = i
        if max_d > tol_m and max_i is not None:
            left = _rdp(indices[: indices.index(max_i) + 1])
            right = _rdp(indices[indices.index(max_i) :])
            return left[:-1] + right
        return [first, last]

    return sorted(set(_rdp(list(range(len(en_pts))))))


def _make_rings(seed: int) -> list[list[tuple[float, float]]]:
    rng = np.random.default_rng(seed)
    rings = []
    # Jagged blobs of various sizes around different ENU offsets.
    for cx, cy, rad, npts in [
        (0.0, 0.0, 50_000.0, 200),  # encloses origin
        (300_000.0, 200_000.0, 40_000.0, 120),  # far away
        (0.0, 120_000.0, 130_000.0, 300),  # edge may pass near origin
        (-80_000.0, -80_000.0, 90_000.0, 60),
    ]:
        ang = np.sort(rng.uniform(0, 2 * math.pi, npts))
        jitter = rng.uniform(0.6, 1.4, npts)
        xs = cx + np.cos(ang) * rad * jitter
        ys = cy + np.sin(ang) * rad * jitter
        rings.append([(float(x), float(y)) for x, y in zip(xs, ys)])
    return rings


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_ring_visible_matches_scalar(seed: int) -> None:
    for en in _make_rings(seed):
        e = np.array([p[0] for p in en], dtype=np.float64)
        n = np.array([p[1] for p in en], dtype=np.float64)
        for range_m in (60_000.0, 150_000.0, 400_000.0):
            r2 = (range_m * 1.02) ** 2
            assert bool(_ring_visible_np(e, n, r2)) == _ref_cull(en, r2)


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_rdp_keep_mask_matches_scalar(seed: int) -> None:
    for en in _make_rings(seed):
        e = np.array([p[0] for p in en], dtype=np.float64)
        n = np.array([p[1] for p in en], dtype=np.float64)
        for tol_m in (10.0, 100.0, 1000.0, 5000.0):
            mask = _rdp_keep_mask_np(e, n, tol_m)
            got = [int(i) for i in np.nonzero(mask)[0]]
            assert got == _ref_rdp_keep(en, tol_m)


def test_batch_enu_matches_scalar() -> None:
    rng = np.random.default_rng(11)
    lat0, lon0 = 42.0, -71.2
    lats = lat0 + rng.uniform(-3, 3, 500)
    lons = lon0 + rng.uniform(-4, 4, 500)
    e_arr, n_arr = geodetic_to_enu_batch(lats, lons, lat0, lon0)
    for i in range(len(lats)):
        x, y, z = geodetic_to_ecef(float(lats[i]), float(lons[i]), 0.0)
        e, n, _ = ecef_to_enu(x, y, z, lat0, lon0, 0.0)
        assert abs(e - float(e_arr[i])) < 1e-4
        assert abs(n - float(n_arr[i])) < 1e-4

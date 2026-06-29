"""Spatial-grid neighbour search for soft-sphere (DEM) contacts: O(N) instead of
the dense O(N^2) all-pairs check, so the `collide` scenario scales to many
thousands of bodies (the dense version also allocates an (N,N,3) array per step —
hundreds of MB at N=5000 — which this avoids entirely).

A uniform grid (cell = one bulk-particle diameter) buckets the small bodies; each
only tests the 27 neighbouring cells. A body much LARGER than a cell (e.g. a
central "sun") would be missed by that local search, so big bodies are pulled out
and tested against everything directly — cheap when there are only a few.

contact_accel(pos, vel, radii, inv_mass, k, gamma) -> (N,3) contact acceleration,
matching the dense reference exactly: for each pair with overlap o = r_i+r_j-dist > 0,
    f = max(k*o - gamma * (v_i-v_j)·n, 0),   n = (r_i - r_j)/dist
applied +to i and -to j, divided by each body's mass.
"""
from __future__ import annotations

import numpy as np
from numba import njit, prange


@njit(cache=True)
def _build(pos, small, ox, oy, oz, nx, ny, nz, cell):
    """Linked-list grid (head/next) over the `small` bodies. Cell indices are
    clamped, so far-flung escapees just share an edge cell — harmless, since the
    real overlap test below uses true positions."""
    head = np.full(nx * ny * nz, -1, np.int64)
    nxt = np.full(pos.shape[0], -1, np.int64)
    for s in range(small.shape[0]):
        p = small[s]
        ix = int((pos[p, 0] - ox) / cell); ix = 0 if ix < 0 else (nx - 1 if ix >= nx else ix)
        iy = int((pos[p, 1] - oy) / cell); iy = 0 if iy < 0 else (ny - 1 if iy >= ny else iy)
        iz = int((pos[p, 2] - oz) / cell); iz = 0 if iz < 0 else (nz - 1 if iz >= nz else iz)
        c = (ix * ny + iy) * nz + iz
        nxt[p] = head[c]; head[c] = p
    return head, nxt


@njit(cache=True, parallel=True)
def _small_small(pos, vel, radii, inv_mass, k, gamma, small, head, nxt,
                 ox, oy, oz, nx, ny, nz, cell, acc):
    for s in prange(small.shape[0]):
        i = small[s]
        xi = pos[i, 0]; yi = pos[i, 1]; zi = pos[i, 2]
        ix = int((xi - ox) / cell); ix = 0 if ix < 0 else (nx - 1 if ix >= nx else ix)
        iy = int((yi - oy) / cell); iy = 0 if iy < 0 else (ny - 1 if iy >= ny else iy)
        iz = int((zi - oz) / cell); iz = 0 if iz < 0 else (nz - 1 if iz >= nz else iz)
        ax = 0.0; ay = 0.0; az = 0.0; ri = radii[i]; imi = inv_mass[i]
        for dx in range(-1, 2):
            jx = ix + dx
            if jx < 0 or jx >= nx:
                continue
            for dy in range(-1, 2):
                jy = iy + dy
                if jy < 0 or jy >= ny:
                    continue
                for dz in range(-1, 2):
                    jz = iz + dz
                    if jz < 0 or jz >= nz:
                        continue
                    j = head[(jx * ny + jy) * nz + jz]
                    while j != -1:
                        if j != i:
                            rx = xi - pos[j, 0]; ry = yi - pos[j, 1]; rz = zi - pos[j, 2]
                            d2 = rx * rx + ry * ry + rz * rz
                            sr = ri + radii[j]
                            if 1e-12 < d2 < sr * sr:
                                dist = np.sqrt(d2)
                                ov = sr - dist
                                nx_ = rx / dist; ny_ = ry / dist; nz_ = rz / dist
                                vrn = ((vel[i, 0] - vel[j, 0]) * nx_ +
                                       (vel[i, 1] - vel[j, 1]) * ny_ +
                                       (vel[i, 2] - vel[j, 2]) * nz_)
                                fm = k * ov - gamma * vrn
                                if fm > 0.0:
                                    fm *= imi
                                    ax += fm * nx_; ay += fm * ny_; az += fm * nz_
                        j = nxt[j]
        acc[i, 0] += ax; acc[i, 1] += ay; acc[i, 2] += az


@njit(cache=True, parallel=True)
def _big_all(pos, vel, radii, inv_mass, k, gamma, big, is_big, acc):
    """Each big body tested against every (small) body. big-big pairs are skipped
    (vanishingly rare — typically a single central seed)."""
    for bi in range(big.shape[0]):
        b = big[bi]; rb = radii[b]; imb = inv_mass[b]
        xb = pos[b, 0]; yb = pos[b, 1]; zb = pos[b, 2]
        fbx = 0.0; fby = 0.0; fbz = 0.0
        for j in prange(pos.shape[0]):
            if j == b or is_big[j]:
                continue
            rx = xb - pos[j, 0]; ry = yb - pos[j, 1]; rz = zb - pos[j, 2]
            d2 = rx * rx + ry * ry + rz * rz
            sr = rb + radii[j]
            if 1e-12 < d2 < sr * sr:
                dist = np.sqrt(d2)
                ov = sr - dist
                nx_ = rx / dist; ny_ = ry / dist; nz_ = rz / dist
                vrn = ((vel[b, 0] - vel[j, 0]) * nx_ +
                       (vel[b, 1] - vel[j, 1]) * ny_ +
                       (vel[b, 2] - vel[j, 2]) * nz_)
                fm = k * ov - gamma * vrn
                if fm > 0.0:
                    imj = inv_mass[j]
                    acc[j, 0] -= fm * nx_ * imj; acc[j, 1] -= fm * ny_ * imj; acc[j, 2] -= fm * nz_ * imj
                    fbx += fm * nx_; fby += fm * ny_; fbz += fm * nz_
        acc[b, 0] += fbx * imb; acc[b, 1] += fby * imb; acc[b, 2] += fbz * imb


def contact_accel(pos, vel, radii, inv_mass, k, gamma):
    """O(N) soft-sphere contact acceleration via a uniform grid (+ direct test for
    any bodies larger than a grid cell). pos/vel (N,3), radii/inv_mass (N,)."""
    pos = np.ascontiguousarray(pos, np.float64)
    vel = np.ascontiguousarray(vel, np.float64)
    radii = np.ascontiguousarray(radii, np.float64)
    inv_mass = np.ascontiguousarray(inv_mass, np.float64)
    N = pos.shape[0]
    acc = np.zeros((N, 3))

    # Split bodies much bigger than the bulk (they can't be found by the local
    # cell search) from the rest. The grid cell is one bulk-particle diameter, so
    # any small-small contact pair lands in adjacent cells (the 27-cell search).
    rmed = float(np.median(radii))
    is_big = radii > 4.0 * rmed
    big = np.where(is_big)[0]
    small = np.where(~is_big)[0]

    if small.size:
        cell = 2.0 * float(radii[small].max())
        sp = pos[small]
        lo = np.percentile(sp, 1.0, axis=0)      # robust box (ignore escapee tails)
        hi = np.percentile(sp, 99.0, axis=0)
        span = np.maximum(hi - lo, cell)
        nx = int(min(max(int(span[0] / cell) + 1, 1), 256))
        ny = int(min(max(int(span[1] / cell) + 1, 1), 256))
        nz = int(min(max(int(span[2] / cell) + 1, 1), 256))
        head, nxt = _build(pos, small, lo[0], lo[1], lo[2], nx, ny, nz, cell)
        _small_small(pos, vel, radii, inv_mass, k, gamma, small, head, nxt,
                     lo[0], lo[1], lo[2], nx, ny, nz, cell, acc)

    if big.size:
        _big_all(pos, vel, radii, inv_mass, k, gamma, big, is_big, acc)

    return acc

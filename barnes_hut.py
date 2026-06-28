"""Barnes-Hut tree-code gravity: O(N log N) replacement for the O(N²) all-pairs sum.

Builds an octree over the bodies; a distant tree cell is treated as a single point
mass at its centre of mass (opening criterion s/d < theta), so only nearby bodies
are summed directly. Plummer softening (r²+eps²)^-1.5 matches the exact solver, so
this is a drop-in for the gravity block in physics.py — contacts are untouched.

`bh_accel(pos, mass, eps, theta)` returns the G=1 acceleration
    a_i = Σ_j m_j (r_j - r_i) / (|r_j-r_i|² + eps²)^1.5   (j != i)
which the caller scales by g_eff and mass to get forces, exactly as before.
"""
from __future__ import annotations

import numpy as np
from numba import njit, prange


@njit(cache=True)
def _build(pos, mass):
    """Insert all bodies into an octree; return node arrays.
    com holds the mass-weighted position SUM (divide by nmass for the centre)."""
    N = pos.shape[0]
    # Bounding cube of all bodies.
    lo0 = lo1 = lo2 = pos[0, 0]
    hi0 = hi1 = hi2 = pos[0, 0]
    lo1 = hi1 = pos[0, 1]
    lo2 = hi2 = pos[0, 2]
    for i in range(N):
        if pos[i, 0] < lo0: lo0 = pos[i, 0]
        if pos[i, 0] > hi0: hi0 = pos[i, 0]
        if pos[i, 1] < lo1: lo1 = pos[i, 1]
        if pos[i, 1] > hi1: hi1 = pos[i, 1]
        if pos[i, 2] < lo2: lo2 = pos[i, 2]
        if pos[i, 2] > hi2: hi2 = pos[i, 2]
    half = max(hi0 - lo0, max(hi1 - lo1, hi2 - lo2)) * 0.5 * 1.0001 + 1e-6
    min_half = half * 1e-6  # below this, coincident bodies share a bucket leaf

    max_nodes = 8 * N + 128
    children = np.full((max_nodes, 8), -1, dtype=np.int64)
    com = np.zeros((max_nodes, 3))
    nmass = np.zeros(max_nodes)
    center = np.zeros((max_nodes, 3))
    nhalf = np.zeros(max_nodes)
    leaf = np.full(max_nodes, -1, dtype=np.int64)  # body index for a leaf, -1 internal/empty

    center[0, 0] = 0.5 * (lo0 + hi0)
    center[0, 1] = 0.5 * (lo1 + hi1)
    center[0, 2] = 0.5 * (lo2 + hi2)
    nhalf[0] = half
    nnodes = 1

    for i in range(N):
        node = 0
        while True:
            com[node, 0] += mass[i] * pos[i, 0]
            com[node, 1] += mass[i] * pos[i, 1]
            com[node, 2] += mass[i] * pos[i, 2]
            nmass[node] += mass[i]
            oc = 0
            if pos[i, 0] > center[node, 0]: oc |= 1
            if pos[i, 1] > center[node, 1]: oc |= 2
            if pos[i, 2] > center[node, 2]: oc |= 4
            ch = children[node, oc]
            if ch == -1:
                # Empty octant: drop a new leaf here.
                nc = nnodes
                nnodes += 1
                children[node, oc] = nc
                leaf[nc] = i
                hh = nhalf[node] * 0.5
                center[nc, 0] = center[node, 0] + (hh if (oc & 1) else -hh)
                center[nc, 1] = center[node, 1] + (hh if (oc & 2) else -hh)
                center[nc, 2] = center[node, 2] + (hh if (oc & 4) else -hh)
                nhalf[nc] = hh
                com[nc, 0] += mass[i] * pos[i, 0]
                com[nc, 1] += mass[i] * pos[i, 1]
                com[nc, 2] += mass[i] * pos[i, 2]
                nmass[nc] += mass[i]
                break
            elif leaf[ch] >= 0:
                # Occupied leaf: either bucket (if too small / out of room) or split.
                if nhalf[ch] < min_half or nnodes >= max_nodes - 1:
                    com[ch, 0] += mass[i] * pos[i, 0]
                    com[ch, 1] += mass[i] * pos[i, 1]
                    com[ch, 2] += mass[i] * pos[i, 2]
                    nmass[ch] += mass[i]
                    break
                j = leaf[ch]
                leaf[ch] = -1  # ch becomes internal; push j into a grandchild
                jo = 0
                if pos[j, 0] > center[ch, 0]: jo |= 1
                if pos[j, 1] > center[ch, 1]: jo |= 2
                if pos[j, 2] > center[ch, 2]: jo |= 4
                ng = nnodes
                nnodes += 1
                children[ch, jo] = ng
                leaf[ng] = j
                hh = nhalf[ch] * 0.5
                center[ng, 0] = center[ch, 0] + (hh if (jo & 1) else -hh)
                center[ng, 1] = center[ch, 1] + (hh if (jo & 2) else -hh)
                center[ng, 2] = center[ch, 2] + (hh if (jo & 4) else -hh)
                nhalf[ng] = hh
                com[ng, 0] += mass[j] * pos[j, 0]
                com[ng, 1] += mass[j] * pos[j, 1]
                com[ng, 2] += mass[j] * pos[j, 2]
                nmass[ng] += mass[j]
                node = ch  # continue placing i below ch
            else:
                node = ch  # internal node: descend
    return children, com, nmass, center, nhalf, leaf


@njit(cache=True, parallel=True)
def _accel(pos, children, com, nmass, center, nhalf, leaf, eps2, theta2):
    N = pos.shape[0]
    acc = np.zeros((N, 3))
    for i in prange(N):
        px = pos[i, 0]; py = pos[i, 1]; pz = pos[i, 2]
        ax = 0.0; ay = 0.0; az = 0.0
        stack = np.empty(256, dtype=np.int64)
        sp = 0
        stack[0] = 0
        sp = 1
        while sp > 0:
            sp -= 1
            node = stack[sp]
            if leaf[node] == i:
                continue  # self
            m = nmass[node]
            if m == 0.0:
                continue
            cx = com[node, 0] / m
            cy = com[node, 1] / m
            cz = com[node, 2] / m
            dx = cx - px; dy = cy - py; dz = cz - pz
            r2 = dx * dx + dy * dy + dz * dz
            s = 2.0 * nhalf[node]
            if leaf[node] >= 0 or (s * s < theta2 * r2):
                inv = (r2 + eps2) ** -1.5
                f = m * inv
                ax += f * dx; ay += f * dy; az += f * dz
            else:
                for c in range(8):
                    ch = children[node, c]
                    if ch != -1 and sp < 256:
                        stack[sp] = ch
                        sp += 1
        acc[i, 0] = ax; acc[i, 1] = ay; acc[i, 2] = az
    return acc


def bh_accel(pos, mass, eps, theta=0.5):
    """G=1 gravitational acceleration via Barnes-Hut. pos (N,3), mass (N,)."""
    pos = np.ascontiguousarray(pos, dtype=np.float64)
    mass = np.ascontiguousarray(mass, dtype=np.float64)
    children, com, nmass, center, nhalf, leaf = _build(pos, mass)
    return _accel(pos, children, com, nmass, center, nhalf, leaf,
                  float(eps) * float(eps), float(theta) * float(theta))

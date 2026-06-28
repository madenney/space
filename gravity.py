"""The shared N-body gravity kernel: the ONE softened point-mass force law that
every scenario uses.

Collisions are what differ between scenarios (none / soft-sphere DEM / Chrono hard
contact) and they live in the scenarios. Gravity is what they all SHARE, so it
lives here exactly once — including the exact↔Barnes-Hut choice — instead of being
copy-pasted (and silently drifting) across rigid / gravity / collide.

    a_i = Σ_{j≠i} m_j (r_j - r_i) / (|r_j - r_i|² + eps²)^1.5      (G = 1)

Callers scale the result by g_eff (and by mass, for a force). `pos` is (N,3),
`mass` is (N,); the return is float64 (N,3) acceleration.
"""
from __future__ import annotations

import numpy as np

try:
    from barnes_hut import bh_accel   # O(N log N) tree gravity (needs numba)
    HAS_BH = True
except Exception:
    HAS_BH = False

# Body count at which the O(N²) all-pairs sum becomes the bottleneck and "auto"
# switches to the tree. Below this, exact is faster (no tree-build overhead).
AUTO_TREE_THRESHOLD = 2500


def exact_accel(pos, mass, soft):
    """Dense O(N²) softened acceleration (G=1). The bit-faithful reference."""
    diff = pos[None, :, :] - pos[:, None, :]                  # (N,N,3) r_j - r_i
    inv_r3 = (np.square(diff).sum(-1) + soft * soft) ** -1.5  # (N,N)
    np.fill_diagonal(inv_r3, 0.0)                             # no self-force
    return (mass[None, :, None] * inv_r3[:, :, None] * diff).sum(axis=1)


def use_tree(solver, n):
    """Resolve the solver policy to a bool: 'exact' | 'tree' | 'auto'. 'auto'
    picks the tree once the quadratic sum dominates; both tree modes fall back to
    exact when numba/barnes_hut is unavailable."""
    if solver == "tree":
        return HAS_BH
    if solver == "auto":
        return HAS_BH and n >= AUTO_TREE_THRESHOLD
    return False


def describe(solver, n, theta=0.5):
    """One-line human label for which solver `solver` resolves to at N=n."""
    if use_tree(solver, n):
        return f"Barnes-Hut tree (theta={theta})"
    return "exact O(N^2)"


def gravity_accel(pos, mass, soft, solver="auto", theta=0.5):
    """Softened N-body acceleration (G=1), exact or Barnes-Hut per `solver`.
    Plummer softening `soft` matches both paths, so the tree is a drop-in for the
    exact sum (~0.5% force error at theta=0.5)."""
    if use_tree(solver, pos.shape[0]):
        return bh_accel(pos, mass, soft, theta)
    return exact_accel(pos, mass, soft)

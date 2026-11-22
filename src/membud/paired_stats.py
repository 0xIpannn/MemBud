"""Small, dependency-free paired statistics used by the relational/QA runners."""
from __future__ import annotations

import math
import random
from typing import Sequence

Z_95 = 1.959963984540054


def wilson(numerator: int, denominator: int, z: float = Z_95) -> dict[str, float | int | None]:
    if denominator == 0:
        return {"numerator": 0, "denominator": 0, "rate": None, "low": None, "high": None}
    p = numerator / denominator
    denom = 1 + z * z / denominator
    centre = (p + z * z / (2 * denominator)) / denom
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator * denominator)) / denom
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(p, 6),
        "low": round(max(0.0, centre - half), 6),
        "high": round(min(1.0, centre + half), 6),
    }


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts ``b`` and ``c``."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 10)


def paired_bootstrap(
    left: Sequence[float],
    right: Sequence[float],
    *,
    seed: int,
    resamples: int = 4000,
) -> dict[str, float]:
    """Mean of ``left - right`` with a percentile bootstrap 95% CI over units."""
    if len(left) != len(right) or not left:
        raise ValueError("paired samples must be non-empty and equal length")
    diffs = [a - b for a, b in zip(left, right)]
    n = len(diffs)
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    lo = means[int(0.025 * (resamples - 1))]
    hi = means[int(math.ceil(0.975 * (resamples - 1)))]
    return {
        "mean_diff": round(sum(diffs) / n, 6),
        "ci95_low": round(lo, 6),
        "ci95_high": round(hi, 6),
        "units": n,
        "resamples": resamples,
    }

from __future__ import annotations

import random
from typing import Tuple

import numpy as np


def gray_gain_array(
    x_u8: np.ndarray,
    left_peak: int,
    right_peak: int,  # 用于决定强/弱光（当前实现不再依赖该逻辑，仅保留兼容签名）
    min_left: int = 30,
    a_range: Tuple[float, float] = (0.01, 1.2),
    b_range: Tuple[float, float] = (-30, 30),
    target_weak: Tuple[float, float] = (0.3, 0.98),  # 保留兼容签名
    target_strong: Tuple[float, float] = (1.05, 1.80),  # 保留兼容签名
    max_clip_frac: float = 0.02,
    tries: int = 80,
):
    """
    Reduce contrast by compressing the distance between two histogram peaks.

    Steps:
    1) Randomly pick a scale factor `a` to scale BOTH peaks and pull them closer.
       - The post-scale peak distance must be >= 30.
       - If the original peak distance is already < 30, skip scaling (a = 1.0).
    2) Randomly pick an offset `b` to shift BOTH scaled peaks into a valid range:
       - left_peak' >= min_left
       - right_peak' < 250
       - Additionally constrained by `b_range`.

    Returns:
        y_u8, a, b, new_left_peak
    """
    if x_u8.dtype != np.uint8:
        # keep behavior explicit; caller can still pass uint8-like arrays
        x_u8 = x_u8.astype(np.uint8, copy=False)

    # Normalize ordering (expect left < right; swap if needed to be robust).
    if right_peak < left_peak:
        left_peak, right_peak = right_peak, left_peak

    x_f = x_u8.astype(np.float32, copy=False)
    p1, p99 = np.percentile(x_f, [1, 99])

    def clip_frac(yf: np.ndarray) -> float:
        return float(((yf <= 0.0) | (yf >= 255.0)).mean())

    # Degenerate input: just try to shift into the valid range.
    if p99 <= p1 + 1e-6:
        a = 1.0
        b_low = max(float(b_range[0]), float(min_left) - float(left_peak))
        b_high = min(float(b_range[1]), (250.0 - 1e-3) - float(right_peak))
        if b_low <= b_high:
            b = random.uniform(b_low, b_high)
        else:
            b = float(np.clip(min_left - left_peak, b_range[0], b_range[1]))
        y = np.clip(x_f * a + b, 0, 255).astype(np.uint8)
        return y, float(a), float(b), float(left_peak) * float(a) + float(b)

    d0 = float(right_peak - left_peak)

    for _ in range(int(tries)):
        # Step 1: scale peaks closer using `a`.
        if d0 < 30.0:
            a = 1.0
        else:
            # Ensure scaled distance >= 30, and (when possible) do not increase distance.
            a_min = max(float(a_range[0]), 30.0 / d0)
            a_max = min(float(a_range[1]), 1.0)
            if a_min > a_max:
                # Should be rare; fall back to no scaling.
                a = 1.0
            else:
                a = random.uniform(a_min, a_max)

        l_scaled = float(left_peak) * float(a)
        r_scaled = float(right_peak) * float(a)

        # Enforce min-distance constraint explicitly (protect against float edge cases).
        if (r_scaled - l_scaled) + 1e-6 < 30.0 and d0 >= 30.0:
            continue

        # Step 2: shift peaks into [min_left, 250) using random `b`.
        b_low = max(float(b_range[0]), float(min_left) - l_scaled)
        b_high = min(float(b_range[1]), (250.0 - 1e-3) - r_scaled)
        if b_low > b_high:
            continue

        b = random.uniform(b_low, b_high)
        new_left_peak = l_scaled + float(b)
        new_right_peak = r_scaled + float(b)

        # Range checks (should already be guaranteed by bounds above).
        if new_left_peak < float(min_left):
            continue
        if new_right_peak >= 250.0:
            continue

        # Avoid extreme collapse/saturation.
        y_f = x_f * float(a) + float(b)
        if clip_frac(y_f) > float(max_clip_frac):
            continue

        y = np.clip(y_f, 0, 255).astype(np.uint8)
        return y, float(a), float(b), float(new_left_peak)

    # Fallback: deterministic feasible attempt.
    if d0 < 30.0:
        a = 1.0
    else:
        a = min(1.0, max(float(a_range[0]), 30.0 / d0))

    l_scaled = float(left_peak) * float(a)
    r_scaled = float(right_peak) * float(a)
    b_low = max(float(b_range[0]), float(min_left) - l_scaled)
    b_high = min(float(b_range[1]), (250.0 - 1e-3) - r_scaled)
    if b_low <= b_high:
        b = 0.5 * (b_low + b_high)
    else:
        b = float(np.clip(min_left - left_peak, b_range[0], b_range[1]))

    y_f = x_f * float(a) + float(b)
    y = np.clip(y_f, 0, 255).astype(np.uint8)
    return y, float(a), float(b), float(left_peak) * float(a) + float(b)

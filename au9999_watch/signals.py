from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import RollingWindow
from .types import Quote, SignalSnapshot


@dataclass
class SignalEngine:
    """
    Heuristic signals from quote snapshots.

    IMPORTANT: Public quote feeds typically do NOT provide participant-level flow
    (retail vs institution). This module emits "participant_hint" based on
    liquidity/turnover spikes and top-of-book depth patterns only.
    """

    window: int = 60
    spike_k: float = 6.0  # robust z-score threshold
    min_history: int = 10

    def __post_init__(self) -> None:
        self._d_amount = RollingWindow(self.window)
        self._d_volume = RollingWindow(self.window)
        self._depth_total = RollingWindow(self.window)
        self._prev: Quote | None = None

    def step(self, q: Quote) -> SignalSnapshot:
        prev = self._prev

        d_last = None
        d_volume = None
        d_amount = None
        if prev and q.last is not None and prev.last is not None:
            d_last = q.last - prev.last
        if prev and q.volume is not None and prev.volume is not None:
            d_volume = q.volume - prev.volume
            if d_volume < 0:
                d_volume = None
        if prev and q.amount is not None and prev.amount is not None:
            d_amount = q.amount - prev.amount
            if d_amount < 0:
                d_amount = None

        spread = None
        if q.bid is not None and q.ask is not None:
            spread = q.ask - q.bid

        depth_imb = None
        depth_total = None
        if q.bid_vol is not None and q.ask_vol is not None:
            depth_total = q.bid_vol + q.ask_vol
            if depth_total > 0:
                depth_imb = (q.bid_vol - q.ask_vol) / depth_total
            else:
                depth_imb = 0.0

        # Update rolling stats (only when we have deltas).
        if d_amount is not None:
            self._d_amount.push(d_amount)
        if d_volume is not None:
            self._d_volume.push(d_volume)
        if depth_total is not None:
            self._depth_total.push(depth_total)

        flow_direction = "unknown"
        score = 0.0
        if d_last is None or d_amount is None:
            flow_direction = "unknown"
            score = 0.0
        else:
            # Signed strength: price move * (scaled turnover).
            # log1p keeps it from exploding.
            score = math.copysign(math.log1p(abs(d_amount)), d_last)
            if d_last > 0 and d_amount > 0:
                flow_direction = "inflow"
            elif d_last < 0 and d_amount > 0:
                flow_direction = "outflow"
            else:
                flow_direction = "neutral"

        participant_hint = "unknown"
        if d_amount is not None and depth_total is not None:
            # Robust spike detection using median + k*MAD.
            # If we don't have enough history, avoid pretending to know.
            amt_med = self._d_amount.median()
            amt_mad = self._d_amount.mad()
            dep_med = self._depth_total.median()
            dep_mad = self._depth_total.mad()

            enough = len(self._d_amount.values()) >= self.min_history and len(self._depth_total.values()) >= self.min_history
            if enough and amt_med is not None and dep_med is not None:
                amt_thr = amt_med + self.spike_k * (amt_mad or 0.0)
                dep_thr = dep_med + self.spike_k * (dep_mad or 0.0)
                is_spike = (amt_mad is not None and d_amount >= amt_thr) or (dep_mad is not None and depth_total >= dep_thr)
                participant_hint = "institution_like" if is_spike else "retail_like"
            else:
                participant_hint = "unknown"

        self._prev = q
        return SignalSnapshot(
            quote=q,
            d_last=d_last,
            d_volume=d_volume,
            d_amount=d_amount,
            spread=spread,
            depth_imbalance=depth_imb,
            flow_direction=flow_direction,
            participant_hint=participant_hint,
            score=score,
        )


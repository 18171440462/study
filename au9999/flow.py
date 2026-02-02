from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .types import Side, Tick


@dataclass(frozen=True)
class FlowBucket:
    name: str
    min_amount: float  # inclusive
    max_amount: Optional[float] = None  # exclusive if set

    def contains(self, amount: float) -> bool:
        if amount < self.min_amount:
            return False
        if self.max_amount is None:
            return True
        return amount < self.max_amount


DEFAULT_BUCKETS: Tuple[FlowBucket, ...] = (
    # 金融行业常见口径：按成交额分层（阈值可按你的市场/单位调整）
    FlowBucket("retail_small", 0, 50_000),
    FlowBucket("retail_medium", 50_000, 200_000),
    FlowBucket("inst_large", 200_000, 1_000_000),
    FlowBucket("inst_super", 1_000_000, None),
)


@dataclass
class BucketFlow:
    bucket: FlowBucket
    buy_amount: float = 0.0
    sell_amount: float = 0.0

    @property
    def net_amount(self) -> float:
        return self.buy_amount - self.sell_amount


def _infer_sides_by_tick_rule(ticks: List[Tick]) -> List[Side]:
    """
    Tick rule:
    - price up => buy
    - price down => sell
    - price same => use last non-unknown direction, else unknown
    """
    inferred: List[Side] = []
    last_dir: Side = "unknown"
    last_price: Optional[float] = None
    for t in ticks:
        if last_price is None:
            inferred.append("unknown" if t.side == "unknown" else t.side)
            last_price = t.price
            if t.side != "unknown":
                last_dir = t.side
            continue

        if t.side != "unknown":
            d = t.side
        else:
            if t.price > last_price:
                d = "buy"
            elif t.price < last_price:
                d = "sell"
            else:
                d = last_dir
        inferred.append(d)
        if d != "unknown":
            last_dir = d
        last_price = t.price
    return inferred


def compute_bucket_flows(
    ticks: Iterable[Tick],
    *,
    buckets: Tuple[FlowBucket, ...] = DEFAULT_BUCKETS,
    infer_side_if_unknown: bool = True,
) -> List[BucketFlow]:
    tick_list = list(ticks)
    if infer_side_if_unknown:
        sides = _infer_sides_by_tick_rule(tick_list)
    else:
        sides = [t.side for t in tick_list]

    flows = [BucketFlow(bucket=b) for b in buckets]

    for t, s in zip(tick_list, sides):
        amt = t.amount if t.amount is not None else (t.price * t.size)
        bucket_idx = None
        for i, b in enumerate(buckets):
            if b.contains(amt):
                bucket_idx = i
                break
        if bucket_idx is None:
            continue

        if s == "buy":
            flows[bucket_idx].buy_amount += amt
        elif s == "sell":
            flows[bucket_idx].sell_amount += amt
        else:
            # unknown: ignore (or could split half/half; keep conservative)
            pass

    return flows


def summarize_total_net(flows: Iterable[BucketFlow]) -> float:
    return sum(f.net_amount for f in flows)


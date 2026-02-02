from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Quote:
    symbol: str
    name_cn: str | None
    name_en: str | None
    last: float | None
    bid: float | None
    ask: float | None
    bid_vol: float | None
    ask_vol: float | None
    volume: float | None
    amount: float | None
    ts: datetime | None
    pct_change: float | None
    raw_fields: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name_cn": self.name_cn,
            "name_en": self.name_en,
            "last": self.last,
            "bid": self.bid,
            "ask": self.ask,
            "bid_vol": self.bid_vol,
            "ask_vol": self.ask_vol,
            "volume": self.volume,
            "amount": self.amount,
            "ts": self.ts.isoformat(sep=" ") if self.ts else None,
            "pct_change": self.pct_change,
            "source": self.source,
        }


@dataclass(frozen=True)
class SignalSnapshot:
    quote: Quote
    d_last: float | None
    d_volume: float | None
    d_amount: float | None
    spread: float | None
    depth_imbalance: float | None
    flow_direction: str  # inflow/outflow/neutral/unknown
    participant_hint: str  # institution_like/retail_like/unknown
    score: float  # signed strength

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote": self.quote.to_dict(),
            "d_last": self.d_last,
            "d_volume": self.d_volume,
            "d_amount": self.d_amount,
            "spread": self.spread,
            "depth_imbalance": self.depth_imbalance,
            "flow_direction": self.flow_direction,
            "participant_hint": self.participant_hint,
            "score": self.score,
        }


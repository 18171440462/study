from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


Side = Literal["buy", "sell", "unknown"]


@dataclass(frozen=True)
class Quote:
    symbol: str
    ts: datetime
    last: float
    prev_close: Optional[float] = None
    volume: Optional[float] = None


@dataclass(frozen=True)
class Tick:
    """
    A single trade.

    - price:成交价
    - size: 成交数量（合约单位未知时可以直接用手数/克等原始单位）
    - amount: 成交额（若数据源给出，优先用；否则可用 price*size 近似）
    - side: 主动买/主动卖（若数据源给出），否则可以用 tick rule 推断
    """

    symbol: str
    ts: datetime
    price: float
    size: float
    amount: Optional[float] = None
    side: Side = "unknown"


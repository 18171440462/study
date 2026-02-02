from __future__ import annotations

import csv
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, List, Optional, Protocol

import requests

from .types import Quote, Tick


class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...


class TicksProvider(Protocol):
    def get_ticks(self, symbol: str, since: Optional[datetime] = None) -> List[Tick]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MockQuoteProvider:
    start_price: float = 480.0
    seed: int = 9999

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._last = self.start_price
        self._prev_close = self.start_price

    def get_quote(self, symbol: str) -> Quote:
        # small random walk
        step = self._rng.uniform(-0.25, 0.25)
        self._last = max(0.01, self._last + step)
        vol = abs(self._rng.gauss(0, 1)) * 1000
        return Quote(symbol=symbol, ts=_utcnow(), last=float(self._last), prev_close=float(self._prev_close), volume=float(vol))


@dataclass
class CsvTicksProvider:
    """
    CSV columns (header required):
      ts,price,size,amount,side
    - ts: ISO8601 (e.g. 2026-02-02T00:00:00Z) or epoch seconds
    - side: buy/sell/unknown (optional)
    """

    path: str

    def _parse_ts(self, raw: str) -> datetime:
        raw = raw.strip()
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        # allow trailing Z
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def get_ticks(self, symbol: str, since: Optional[datetime] = None) -> List[Tick]:
        out: List[Tick] = []
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = self._parse_ts(row["ts"])
                if since is not None and ts <= since:
                    continue
                price = float(row["price"])
                size = float(row.get("size") or 0)
                amount_raw = row.get("amount")
                amount = float(amount_raw) if amount_raw not in (None, "", "null", "None") else None
                side = (row.get("side") or "unknown").strip().lower()
                if side not in ("buy", "sell", "unknown"):
                    side = "unknown"
                out.append(Tick(symbol=symbol, ts=ts, price=price, size=size, amount=amount, side=side))  # type: ignore[arg-type]
        return out


@dataclass
class HttpQuoteProvider:
    """
    Placeholder HTTP quote provider.

    Different vendors have different endpoints & symbol codes for AU9999.
    Implement `parse` for your chosen data source.
    """

    url: str
    timeout_s: float = 5.0

    def get_quote(self, symbol: str) -> Quote:
        resp = requests.get(self.url, timeout=self.timeout_s)
        resp.raise_for_status()
        last, prev_close, vol = self.parse(resp.text, symbol=symbol)
        return Quote(symbol=symbol, ts=_utcnow(), last=last, prev_close=prev_close, volume=vol)

    def parse(self, text: str, *, symbol: str) -> tuple[float, Optional[float], Optional[float]]:
        raise NotImplementedError("Please implement parse() for your data source")


def iter_poll_quotes(provider: QuoteProvider, symbol: str, interval_s: float) -> Iterator[Quote]:
    while True:
        yield provider.get_quote(symbol)
        if interval_s <= 0:
            return
        time.sleep(interval_s)


from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from .types import Quote


_VAR_RE = re.compile(r'^var\s+hq_str_(?P<key>[A-Za-z0-9_]+)=\"(?P<body>.*)\";\s*$', re.S)


def _to_float(x: str) -> float | None:
    try:
        if x == "" or x.lower() == "null":
            return None
        return float(x)
    except Exception:
        return None


def _parse_pct(x: str) -> float | None:
    x = x.strip()
    if not x:
        return None
    if x.endswith("%"):
        x = x[:-1]
    return _to_float(x)


def _parse_dt(x: str) -> datetime | None:
    x = x.strip()
    if not x:
        return None
    # observed format: "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(x, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def parse_sina_sge_payload(payload_text: str, *, source: str) -> Quote:
    """
    Parse a single line like:
      var hq_str_SGE_AU9999="AU9999,沪金99,Au99.99,....";
    """
    m = _VAR_RE.match(payload_text.strip())
    if not m:
        raise ValueError("unexpected payload format")

    body = m.group("body")
    if body == "":
        return Quote(
            symbol="AU9999",
            name_cn=None,
            name_en=None,
            last=None,
            bid=None,
            ask=None,
            bid_vol=None,
            ask_vol=None,
            volume=None,
            amount=None,
            ts=None,
            pct_change=None,
            raw_fields=tuple(),
            source=source,
        )

    fields = body.split(",")
    # Known stable positions (from observed SGE_AU9999 response):
    # 0 symbol, 1 cn, 2 en, 7 last, 10 bid, 11 ask, 12 bid_vol, 13 ask_vol,
    # 14 volume, 15 amount, 16 datetime, 17 pct
    symbol = fields[0] if len(fields) > 0 else "AU9999"
    name_cn = fields[1] if len(fields) > 1 else None
    name_en = fields[2] if len(fields) > 2 else None
    last = _to_float(fields[7]) if len(fields) > 7 else None
    bid = _to_float(fields[10]) if len(fields) > 10 else None
    ask = _to_float(fields[11]) if len(fields) > 11 else None
    bid_vol = _to_float(fields[12]) if len(fields) > 12 else None
    ask_vol = _to_float(fields[13]) if len(fields) > 13 else None
    volume = _to_float(fields[14]) if len(fields) > 14 else None
    amount = _to_float(fields[15]) if len(fields) > 15 else None
    ts = _parse_dt(fields[16]) if len(fields) > 16 else None
    pct_change = _parse_pct(fields[17]) if len(fields) > 17 else None

    return Quote(
        symbol=symbol,
        name_cn=name_cn,
        name_en=name_en,
        last=last,
        bid=bid,
        ask=ask,
        bid_vol=bid_vol,
        ask_vol=ask_vol,
        volume=volume,
        amount=amount,
        ts=ts,
        pct_change=pct_change,
        raw_fields=tuple(fields),
        source=source,
    )


@dataclass(frozen=True)
class SinaSGEClient:
    symbol: str = "AU9999"
    timeout_s: float = 10.0

    def fetch_quote(self) -> Quote:
        key = f"SGE_{self.symbol}"
        url = f"https://hq.sinajs.cn/list={key}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read()
        # Sina hq is GBK encoded.
        text = raw.decode("gbk", errors="replace")
        return parse_sina_sge_payload(text, source="sina_sge")


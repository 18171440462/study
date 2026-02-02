#!/usr/bin/env python3
"""
AU9999 (SGE Au99.99) simple watchlist + proxy flow signals.

Important:
- Without Level2 order book / time&sales volume (and broker seat data), you cannot *truly*
  distinguish "retail vs institutional" in/out flows.
- This script provides:
  1) Real-time-ish price polling for SGE spot Au99.99 via akshare (SGE endpoint).
  2) A *price-action proxy* signal (trend efficiency / sign-changes) as a hint only.
  3) Optional trade-CSV net flow estimation by trade size buckets, if you have tick prints.

Usage examples:
  python3 au9999_watch.py
  python3 au9999_watch.py --symbol Au99.99 --interval 2 --window 180
  python3 au9999_watch.py --trades trades.csv --size-small 1000 --size-medium 5000
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import random
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


def _lazy_import_akshare():
    try:
        import akshare as ak  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: akshare. Install with: python3 -m pip install -r requirements.txt"
        ) from e
    return ak


def parse_sge_update_time(s: str) -> dt.datetime:
    """
    Example: '2026年02月02日 11:33:59'
    """
    return dt.datetime.strptime(s.strip(), "%Y年%m月%d日 %H:%M:%S")


@dataclass(frozen=True)
class QuotePoint:
    ts: dt.datetime
    price: float


def _requests_session_with_retries(*, trust_env: bool = False):
    """
    Best-effort requests Session with retry adapter.
    If requests isn't available for any reason, caller should fall back.
    """
    import requests  # type: ignore

    try:
        from urllib3.util.retry import Retry  # type: ignore
        from requests.adapters import HTTPAdapter  # type: ignore

        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s = requests.Session()
        # Avoid flaky/misconfigured corporate proxies from env vars.
        # If user explicitly wants proxies, they can set trust_env=True by editing code.
        s.trust_env = bool(trust_env)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s
    except Exception:
        s = requests.Session()
        s.trust_env = bool(trust_env)
        return s


def fetch_latest_quote(
    symbol: str = "Au99.99",
    timeout_seconds: float = 10.0,
    session=None,
) -> QuotePoint:
    """
    Fetch latest Au spot quote from SGE 'graph/quotations' endpoint.

    Notes:
    - The remote endpoint sometimes rate-limits or closes connections.
      We therefore use a bounded timeout and lightweight retries.
    """
    try:
        import requests  # type: ignore
    except Exception:
        # last resort: use akshare (may still fail similarly)
        ak = _lazy_import_akshare()
        df = ak.spot_quotations_sge(symbol=symbol)
        if df is None or getattr(df, "empty", True):
            raise RuntimeError(f"No data returned for symbol={symbol!r}")
        update_ts = parse_sge_update_time(str(df["更新时间"].iloc[0]))
        last = df.iloc[-1]
        t = last["时间"]
        if isinstance(t, str):
            parts = t.split(":")
            hh, mm = int(parts[0]), int(parts[1])
            ss = int(parts[2]) if len(parts) >= 3 else 0
            t = dt.time(hh, mm, ss)
        if not isinstance(t, dt.time):
            raise RuntimeError(f"Unexpected time value: {t!r}")
        ts = dt.datetime.combine(update_ts.date(), t)
        return QuotePoint(ts=ts, price=float(last["现价"]))

    url = "https://www.sge.com.cn/graph/quotations"
    params = {"instid": symbol}
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Host": "www.sge.com.cn",
        "Pragma": "no-cache",
        "Referer": "https://www.sge.com.cn/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    s = session or _requests_session_with_retries(trust_env=False)
    r = s.get(url, params=params, headers=headers, timeout=float(timeout_seconds))
    r.raise_for_status()
    data_json = r.json()

    # data_json contains: heyue, times, data, delaystr
    update_ts = parse_sge_update_time(str(data_json.get("delaystr", "")).strip())
    times = data_json.get("times") or []
    datas = data_json.get("data") or []
    if not times or not datas:
        raise RuntimeError(f"No quote points returned for symbol={symbol!r}")

    # pick the last element, parse time 'HH:MM'
    t_s = str(times[-1])
    parts = t_s.split(":")
    hh, mm = int(parts[0]), int(parts[1])
    ss = int(parts[2]) if len(parts) >= 3 else 0
    t = dt.time(hh, mm, ss)
    ts = dt.datetime.combine(update_ts.date(), t)
    price = float(datas[-1])
    return QuotePoint(ts=ts, price=price)


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


@dataclass(frozen=True)
class PriceProxySignal:
    direction: str
    net_change: float
    efficiency_ratio: float
    sign_change_rate: float
    volatility: float
    hint: str


def compute_price_proxy_signal(points: List[QuotePoint], min_move: float) -> Optional[PriceProxySignal]:
    if len(points) < 3:
        return None

    prices = [p.price for p in points]
    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    abs_sum = sum(abs(d) for d in diffs)
    net = prices[-1] - prices[0]

    # Trend efficiency (Kaufman ER-style): |net| / sum(|diffs|)
    er = abs(net) / abs_sum if abs_sum > 0 else 0.0

    # Volatility proxy: RMS of diffs
    vol = math.sqrt(sum(d * d for d in diffs) / max(1, len(diffs)))

    # Sign change rate: how often returns flip direction
    signs = [_sign(d) for d in diffs if d != 0]
    if len(signs) <= 1:
        scr = 0.0
    else:
        flips = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
        scr = flips / (len(signs) - 1)

    if net > 0:
        direction = "↑"
    elif net < 0:
        direction = "↓"
    else:
        direction = "→"

    # Proxy interpretation (NOT true fund flow)
    if abs(net) < min_move:
        hint = "中性/无明显方向（仅价格代理）"
    elif er >= 0.60 and scr <= 0.35:
        hint = "单边效率高（更像趋势资金/机构风格；仅价格代理）"
    elif er <= 0.30 and scr >= 0.55:
        hint = "来回拉锯（更像散户短线博弈；仅价格代理）"
    else:
        hint = "混合状态（仅价格代理）"

    return PriceProxySignal(
        direction=direction,
        net_change=net,
        efficiency_ratio=er,
        sign_change_rate=scr,
        volatility=vol,
        hint=hint,
    )


@dataclass
class TradePrint:
    ts: dt.datetime
    price: float
    volume: float
    side: int  # +1 buy, -1 sell, 0 unknown


def _parse_time_any(s: str) -> dt.datetime:
    s = s.strip()
    # Prefer full ISO
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            pass
    # If only HH:MM:SS assume today
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = dt.datetime.strptime(s, fmt).time()
            return dt.datetime.combine(dt.date.today(), t)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized time format: {s!r}")


def load_trades_csv(path: str) -> List[TradePrint]:
    """
    CSV columns (header names are flexible but recommended):
      - time (or ts)
      - price
      - volume
      - side (optional): B/S, buy/sell, 1/-1
    """
    trades: List[TradePrint] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        def pick(d: Dict[str, str], keys: Iterable[str]) -> Optional[str]:
            for k in keys:
                if k in d and d[k] not in (None, ""):
                    return d[k]
            return None

        last_price: Optional[float] = None
        last_side: int = 0
        for row in reader:
            ts_s = pick(row, ("time", "ts", "datetime", "timestamp"))
            px_s = pick(row, ("price", "px", "last"))
            vol_s = pick(row, ("volume", "vol", "qty", "size"))
            if ts_s is None or px_s is None or vol_s is None:
                raise ValueError(f"Missing required columns in row: {row}")

            ts = _parse_time_any(ts_s)
            price = float(px_s)
            volume = float(vol_s)

            side_s = pick(row, ("side", "bs", "direction"))
            side = 0
            if side_s:
                v = side_s.strip().lower()
                if v in ("b", "buy", "bid", "+1", "1"):
                    side = 1
                elif v in ("s", "sell", "ask", "-1", "-1.0"):
                    side = -1
                else:
                    # unknown; will infer
                    side = 0

            # Tick rule inference if side missing/unknown
            if side == 0 and last_price is not None:
                if price > last_price:
                    side = 1
                elif price < last_price:
                    side = -1
                else:
                    side = last_side

            last_price = price
            last_side = side
            trades.append(TradePrint(ts=ts, price=price, volume=volume, side=side))
    return trades


@dataclass(frozen=True)
class FlowSummary:
    net_total: float
    net_small: float
    net_medium: float
    net_large: float


@dataclass(frozen=True)
class FlowSignalState:
    retail: int  # -1 sell, 0 neutral, +1 buy
    institution: int  # -1 sell, 0 neutral, +1 buy


def summarize_flows_by_size(
    trades: List[TradePrint],
    size_small: float,
    size_medium: float,
    lookback_seconds: Optional[int] = None,
) -> FlowSummary:
    if not trades:
        return FlowSummary(0.0, 0.0, 0.0, 0.0)

    cutoff: Optional[dt.datetime] = None
    if lookback_seconds is not None:
        cutoff = trades[-1].ts - dt.timedelta(seconds=int(lookback_seconds))

    ns = nm = nl = 0.0
    for tr in trades:
        if cutoff is not None and tr.ts < cutoff:
            continue
        signed = float(tr.side) * float(tr.volume)
        if tr.volume <= size_small:
            ns += signed
        elif tr.volume <= size_medium:
            nm += signed
        else:
            nl += signed
    return FlowSummary(net_total=ns + nm + nl, net_small=ns, net_medium=nm, net_large=nl)


def flow_signal_state_from_summary(
    flow: FlowSummary,
    threshold_retail: float,
    threshold_institution: float,
) -> FlowSignalState:
    """
    Proxy rule:
    - retail side is inferred from small-trade net volume (net_small)
    - institution side is inferred from large-trade net volume (net_large)
    Thresholds avoid noisy flips.
    """

    def state(x: float, th: float) -> int:
        if x >= th:
            return 1
        if x <= -th:
            return -1
        return 0

    return FlowSignalState(
        retail=state(flow.net_small, float(threshold_retail)),
        institution=state(flow.net_large, float(threshold_institution)),
    )


def describe_flow_signal(state: int, who: str) -> Optional[str]:
    if state == 1:
        return f"{who}净买入"
    if state == -1:
        return f"{who}净卖出"
    return None


@dataclass(frozen=True)
class FuturesProxyMetrics:
    symbol: str
    ts: dt.datetime
    price_last: float
    price_change: float
    hold_last: float
    hold_change: float
    signed_volume: float


def fetch_futures_minute_bars(symbol: str, period: str = "1"):
    """
    Fetch minute bars with volume + hold (OI proxy) via akshare.

    Expected columns: datetime, open, high, low, close, volume, hold
    """
    ak = _lazy_import_akshare()
    return ak.futures_zh_minute_sina(symbol=symbol, period=period)


def compute_futures_proxy_metrics(
    df,
    symbol: str,
    lookback_seconds: int,
) -> Optional[FuturesProxyMetrics]:
    if df is None or getattr(df, "empty", True):
        return None
    if "datetime" not in df.columns or "close" not in df.columns:
        return None

    # Ensure types
    d = df.copy()
    d["datetime"] = dt.datetime.fromisoformat(str(d["datetime"].iloc[-1])) if False else d["datetime"]
    try:
        d["datetime"] = d["datetime"].astype("datetime64[ns]")
    except Exception:
        # fallback: try parse strings
        d["datetime"] = d["datetime"].apply(lambda x: _parse_time_any(str(x)))

    for col in ("close", "volume", "hold"):
        if col in d.columns:
            try:
                d[col] = d[col].astype(float)
            except Exception:
                pass

    last_row = d.iloc[-1]
    ts = last_row["datetime"]
    if isinstance(ts, dt.datetime):
        last_ts = ts
    else:
        # numpy datetime64 -> python datetime
        last_ts = dt.datetime.fromtimestamp(ts.astype("datetime64[s]").astype(int))

    cutoff = last_ts - dt.timedelta(seconds=int(lookback_seconds))
    d_lb = d[d["datetime"] >= cutoff]
    if d_lb.empty:
        d_lb = d.tail(min(len(d), 3))
    if len(d_lb) < 2:
        return None

    price_last = float(d_lb["close"].iloc[-1])
    price_first = float(d_lb["close"].iloc[0])
    price_change = price_last - price_first

    hold_last = float(d_lb["hold"].iloc[-1]) if "hold" in d_lb.columns else float("nan")
    hold_first = float(d_lb["hold"].iloc[0]) if "hold" in d_lb.columns else float("nan")
    hold_change = hold_last - hold_first if (not math.isnan(hold_last) and not math.isnan(hold_first)) else float("nan")

    # Signed volume proxy: sign(close - prev_close) * volume
    closes = d_lb["close"].tolist()
    vols = d_lb["volume"].tolist() if "volume" in d_lb.columns else [0.0] * len(closes)
    sv = 0.0
    for i in range(1, len(closes)):
        sv += float(_sign(float(closes[i]) - float(closes[i - 1]))) * float(vols[i])

    return FuturesProxyMetrics(
        symbol=symbol,
        ts=last_ts,
        price_last=price_last,
        price_change=price_change,
        hold_last=hold_last,
        hold_change=hold_change,
        signed_volume=sv,
    )


def futures_proxy_signal_lines(
    m: FuturesProxyMetrics,
    threshold_oi: float,
    threshold_price: float,
    threshold_signed_volume: float,
) -> Tuple[List[str], FlowSignalState]:
    """
    Produce proxy retail/institution buy/sell using futures minute bars:
    - Institution: uses hold(OI) change + price change.
    - Retail: uses signed volume, but only when |OI change| small (churn-like).
    """
    lines: List[str] = []
    inst = 0
    retail = 0

    oi = m.hold_change
    px = m.price_change
    sv = m.signed_volume

    # Institution proxy: new positions (OI up) aligned with price direction
    if not math.isnan(oi):
        if oi >= threshold_oi and px >= threshold_price:
            inst = 1
            lines.append("机构疑似开多（AU期货 OI↑ 且 价↑；代理）")
        elif oi >= threshold_oi and px <= -threshold_price:
            inst = -1
            lines.append("机构疑似开空（AU期货 OI↑ 且 价↓；代理）")
        elif oi <= -threshold_oi and px >= threshold_price:
            # short covering looks like buying
            inst = 1
            lines.append("机构疑似平空/回补（AU期货 OI↓ 且 价↑；代理）")
        elif oi <= -threshold_oi and px <= -threshold_price:
            # long liquidation looks like selling
            inst = -1
            lines.append("机构疑似平多/止损（AU期货 OI↓ 且 价↓；代理）")

    # Retail proxy: churny flow with small OI change, but clear signed volume pressure
    if abs(sv) >= threshold_signed_volume and (math.isnan(oi) or abs(oi) < threshold_oi * 0.5):
        retail = 1 if sv > 0 else -1
        lines.append("散户疑似净买入" if retail == 1 else "散户疑似净卖出")
        lines[-1] += "（AU期货 分时量价签名；代理）"

    state = FlowSignalState(retail=retail, institution=inst)
    return lines, state


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AU9999(Au99.99) watch + proxy flow signals")
    ap.add_argument("--symbol", default="Au99.99", help="SGE instrument id (default: Au99.99)")
    ap.add_argument("--interval", type=float, default=2.0, help="Polling interval seconds (default: 2)")
    ap.add_argument("--window", type=int, default=180, help="Rolling window seconds (default: 180)")
    ap.add_argument("--min-move", type=float, default=0.3, help="Min price move to call directional (default: 0.3)")
    ap.add_argument("--once", action="store_true", help="Fetch once and exit")
    ap.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds (default: 10)")
    ap.add_argument(
        "--max-fail-sleep",
        type=float,
        default=30.0,
        help="Max sleep seconds after consecutive failures (default: 30)",
    )
    ap.add_argument(
        "--quote-min-interval",
        type=float,
        default=20.0,
        help="Min seconds between spot quote requests (default: 20; reduce rate-limit risk)",
    )
    ap.add_argument(
        "--jitter",
        type=float,
        default=2.0,
        help="Random sleep jitter seconds added between loops (default: 2)",
    )
    ap.add_argument(
        "--align-minute",
        action="store_true",
        help="Align spot quote requests to next minute boundary (recommended for minute-level data)",
    )

    ap.add_argument("--trades", default=None, help="Optional trades CSV path for net flow estimation")
    ap.add_argument("--lookback", type=int, default=600, help="Trade flow lookback seconds (default: 600)")
    ap.add_argument("--size-small", type=float, default=1000.0, help="Small trade volume threshold (default: 1000)")
    ap.add_argument("--size-medium", type=float, default=5000.0, help="Medium trade volume threshold (default: 5000)")
    ap.add_argument(
        "--signal",
        action="store_true",
        help="Print retail/institution buy/sell signals (use --trades; or proxy via AU futures when no trades)",
    )
    ap.add_argument(
        "--signal-threshold-retail",
        type=float,
        default=3000.0,
        help="Min |small-net| to trigger retail signal (default: 3000)",
    )
    ap.add_argument(
        "--signal-threshold-institution",
        type=float,
        default=3000.0,
        help="Min |large-net| to trigger institution signal (default: 3000)",
    )
    ap.add_argument(
        "--futures-symbol",
        default="AU0",
        help="Futures symbol used as proxy when no --trades (default: AU0)",
    )
    ap.add_argument(
        "--futures-lookback",
        type=int,
        default=600,
        help="AU futures proxy lookback seconds (default: 600)",
    )
    ap.add_argument(
        "--futures-signal-interval",
        type=int,
        default=30,
        help="Minimum seconds between futures proxy refresh (default: 30)",
    )
    ap.add_argument(
        "--futures-threshold-oi",
        type=float,
        default=200.0,
        help="Min |OI(hold) change| to trigger institution proxy (default: 200)",
    )
    ap.add_argument(
        "--futures-threshold-price",
        type=float,
        default=0.5,
        help="Min |price change| over lookback to trigger OI proxy (default: 0.5)",
    )
    ap.add_argument(
        "--futures-threshold-signed-volume",
        type=float,
        default=8000.0,
        help="Min |signed volume| to trigger retail proxy (default: 8000)",
    )

    args = ap.parse_args(argv)

    points: List[QuotePoint] = []
    last_printed_ts: Optional[dt.datetime] = None
    last_flow_state: Optional[FlowSignalState] = None
    last_futures_state: Optional[FlowSignalState] = None
    last_futures_fetch_t: float = 0.0
    consecutive_quote_failures: int = 0
    last_quote_fetch_t: float = 0.0
    quote_session = None
    try:
        # Create one session (keep-alive), ignore env proxies by default.
        quote_session = _requests_session_with_retries(trust_env=False)
    except Exception:
        quote_session = None

    def emit_line(s: str) -> None:
        sys.stdout.write(s + "\n")
        sys.stdout.flush()

    if args.signal and not args.trades:
        emit_line(
            "注意：未提供逐笔数据（--trades）；将改用 AU期货(AU0) 的分时成交量+持仓(OI) 作为代理来提示散户/机构买卖。"
        )
    elif args.trades and args.signal:
        emit_line(
            "注意：散户/机构信号为“逐笔代理”（小单≈散户、大单≈机构，按净量阈值触发），"
            "并非真实身份识别；未使用Level2/席位数据。"
        )
    elif args.trades:
        emit_line("注意：逐笔净量为“代理估计”（基于tick rule/side列），并非真实资金流。")
    else:
        emit_line(
            "注意：散户/机构“流入流出”需要逐笔成交/盘口等数据；仅用价格只能做代理推断。"
        )

    while True:
        # 1) SGE spot quote (minute-level; avoid hammering)
        q = None
        now_t = time.time()
        should_fetch_quote = (now_t - last_quote_fetch_t) >= max(1.0, float(args.quote_min_interval))
        if args.align_minute and should_fetch_quote:
            # wait until next minute boundary + tiny jitter, then fetch
            now_dt = dt.datetime.now()
            next_min = (now_dt.replace(second=0, microsecond=0) + dt.timedelta(minutes=1))
            wait_s = (next_min - now_dt).total_seconds()
            # don't wait too long; only align when within a reasonable window
            if 0.0 < wait_s < max(1.0, float(args.quote_min_interval) * 1.2):
                time.sleep(wait_s + random.uniform(0.0, min(1.0, float(args.jitter))))
                now_t = time.time()
                should_fetch_quote = True

        if should_fetch_quote:
            last_quote_fetch_t = now_t
            try:
                q = fetch_latest_quote(
                    symbol=args.symbol,
                    timeout_seconds=float(args.timeout),
                    session=quote_session,
                )
                consecutive_quote_failures = 0
            except Exception as e:
                consecutive_quote_failures += 1
                emit_line(f"[{dt.datetime.now().isoformat(timespec='seconds')}] 获取行情失败: {e}")
                q = None

        # 2) Futures proxy signal can still run even if spot quote fails
        if args.signal and not args.trades:
            now = time.time()
            if (now - last_futures_fetch_t) >= max(1, int(args.futures_signal_interval)):
                last_futures_fetch_t = now
                try:
                    bars = fetch_futures_minute_bars(symbol=str(args.futures_symbol), period="1")
                    metrics = compute_futures_proxy_metrics(
                        df=bars,
                        symbol=str(args.futures_symbol),
                        lookback_seconds=int(args.futures_lookback),
                    )
                    if metrics is None:
                        emit_line("  SIGNAL(AU期货代理): 获取/解析失败（无数据）")
                    else:
                        lines, state = futures_proxy_signal_lines(
                            m=metrics,
                            threshold_oi=float(args.futures_threshold_oi),
                            threshold_price=float(args.futures_threshold_price),
                            threshold_signed_volume=float(args.futures_threshold_signed_volume),
                        )
                        emit_line(
                            f"  AU期货代理(近{args.futures_lookback}s): {metrics.symbol} "
                            f"ΔP={metrics.price_change:+.2f} ΔOI={metrics.hold_change:+.0f} "
                            f"SV={metrics.signed_volume:+.0f} @ {metrics.ts.strftime('%H:%M:%S')}"
                        )
                        if last_futures_state is None or state != last_futures_state:
                            last_futures_state = state
                            if lines:
                                for s in lines:
                                    emit_line(f"  SIGNAL(AU期货代理): {s}")
                            else:
                                emit_line("  SIGNAL(AU期货代理): 无（未达阈值/无明显结构）")
                except Exception as e:
                    emit_line(f"  SIGNAL(AU期货代理): 获取失败: {e}")

        if q is None:
            if args.once:
                return 2
            # Backoff on consecutive failures to reduce rate-limit risk
            backoff = min(float(args.max_fail_sleep), float(args.interval) * (2 ** max(0, consecutive_quote_failures - 1)))
            time.sleep(max(1.0, backoff) + random.uniform(0.0, max(0.0, float(args.jitter))))
            continue

        # De-dup: SGE endpoint may repeat last minute
        if last_printed_ts is not None and q.ts <= last_printed_ts and not args.once:
            time.sleep(max(0.5, args.interval))
            continue
        last_printed_ts = q.ts

        points.append(q)
        cutoff = q.ts - dt.timedelta(seconds=int(args.window))
        points = [p for p in points if p.ts >= cutoff]

        sig = compute_price_proxy_signal(points, min_move=float(args.min_move))

        line = f"[{q.ts.isoformat(sep=' ', timespec='seconds')}] {args.symbol} 现价={q.price:.2f}"
        if sig:
            line += (
                f" | 近{args.window}s {sig.direction} Δ={sig.net_change:+.2f}"
                f" ER={sig.efficiency_ratio:.2f} 翻转率={sig.sign_change_rate:.2f} 波动={sig.volatility:.3f}"
                f" | {sig.hint}"
            )
        emit_line(line)

        if args.trades:
            try:
                trades = load_trades_csv(args.trades)
                flow = summarize_flows_by_size(
                    trades=trades,
                    size_small=float(args.size_small),
                    size_medium=float(args.size_medium),
                    lookback_seconds=int(args.lookback),
                )
                emit_line(
                    f"  逐笔代理净量(近{args.lookback}s): 总={flow.net_total:+.0f}"
                    f" 小单={flow.net_small:+.0f} 中单={flow.net_medium:+.0f} 大单={flow.net_large:+.0f}"
                )

                if args.signal:
                    state = flow_signal_state_from_summary(
                        flow=flow,
                        threshold_retail=float(args.signal_threshold_retail),
                        threshold_institution=float(args.signal_threshold_institution),
                    )
                    # Only print when signal state changes to reduce spam
                    if last_flow_state is None or state != last_flow_state:
                        last_flow_state = state
                        retail_msg = describe_flow_signal(state.retail, "散户")
                        inst_msg = describe_flow_signal(state.institution, "机构")
                        if retail_msg or inst_msg:
                            parts = [p for p in [retail_msg, inst_msg] if p]
                            emit_line(
                                "  SIGNAL(逐笔代理): "
                                + "；".join(parts)
                                + f" | 阈值 小单={args.signal_threshold_retail:.0f} 大单={args.signal_threshold_institution:.0f}"
                            )
                        else:
                            emit_line("  SIGNAL(逐笔代理): 无（净量未达阈值）")
            except Exception as e:
                emit_line(f"  读取 trades CSV 失败: {e}")
        # futures proxy already handled above; no-op here

        if args.once:
            return 0

        time.sleep(max(0.5, args.interval) + random.uniform(0.0, max(0.0, float(args.jitter))))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


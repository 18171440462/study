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


def fetch_latest_quote(symbol: str = "Au99.99") -> QuotePoint:
    ak = _lazy_import_akshare()
    df = ak.spot_quotations_sge(symbol=symbol)
    if df is None or getattr(df, "empty", True):
        raise RuntimeError(f"No data returned for symbol={symbol!r}")

    # df columns: 品种, 时间 (datetime.time), 现价 (float), 更新时间 (string)
    update_ts = parse_sge_update_time(str(df["更新时间"].iloc[0]))
    last = df.iloc[-1]

    t = last["时间"]
    if isinstance(t, str):
        # fallback: 'HH:MM:SS' or 'HH:MM'
        parts = t.split(":")
        hh, mm = int(parts[0]), int(parts[1])
        ss = int(parts[2]) if len(parts) >= 3 else 0
        t = dt.time(hh, mm, ss)
    if not isinstance(t, dt.time):
        raise RuntimeError(f"Unexpected time value: {t!r}")

    ts = dt.datetime.combine(update_ts.date(), t)
    price = float(last["现价"])
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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AU9999(Au99.99) watch + proxy flow signals")
    ap.add_argument("--symbol", default="Au99.99", help="SGE instrument id (default: Au99.99)")
    ap.add_argument("--interval", type=float, default=2.0, help="Polling interval seconds (default: 2)")
    ap.add_argument("--window", type=int, default=180, help="Rolling window seconds (default: 180)")
    ap.add_argument("--min-move", type=float, default=0.3, help="Min price move to call directional (default: 0.3)")
    ap.add_argument("--once", action="store_true", help="Fetch once and exit")

    ap.add_argument("--trades", default=None, help="Optional trades CSV path for net flow estimation")
    ap.add_argument("--lookback", type=int, default=600, help="Trade flow lookback seconds (default: 600)")
    ap.add_argument("--size-small", type=float, default=1000.0, help="Small trade volume threshold (default: 1000)")
    ap.add_argument("--size-medium", type=float, default=5000.0, help="Medium trade volume threshold (default: 5000)")
    ap.add_argument(
        "--signal",
        action="store_true",
        help="Print retail/institution buy/sell signals (requires --trades)",
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

    args = ap.parse_args(argv)

    points: List[QuotePoint] = []
    last_printed_ts: Optional[dt.datetime] = None
    last_flow_state: Optional[FlowSignalState] = None

    def emit_line(s: str) -> None:
        sys.stdout.write(s + "\n")
        sys.stdout.flush()

    emit_line(
        "注意：散户/机构“流入流出”需要逐笔成交/盘口等数据；仅用价格只能做代理推断。"
    )

    while True:
        try:
            q = fetch_latest_quote(symbol=args.symbol)
        except Exception as e:
            emit_line(f"[{dt.datetime.now().isoformat(timespec='seconds')}] 获取行情失败: {e}")
            if args.once:
                return 2
            time.sleep(max(1.0, args.interval))
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
        elif args.signal:
            emit_line("  SIGNAL: 需要提供 --trades 才能判定散户/机构买卖（仅用价格不做交易流向判定）")

        if args.once:
            return 0

        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


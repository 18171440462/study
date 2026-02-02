#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AU9999 (SGE Au99.99) realtime watcher.

Important: public data sources typically do NOT provide order-level (Level-2) or trade-by-trade size data for AU9999.
So "retail vs institutional" and "inflow vs outflow" can only be *proxy inferences* based on price action, NOT ground truth.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import time
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from collections import deque

import pandas as pd
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True)
class Tick:
    ts: dt.datetime
    price: float
    source_updated_at: Optional[str] = None


class AkshareSgeAu9999Provider:
    """
    Fetches SGE Au99.99 minute quote series and returns the latest row.
    """

    def __init__(self) -> None:
        import akshare as ak  # type: ignore

        self._ak = ak

    def fetch_latest(self) -> Tick:
        df = self._ak.spot_quotations_sge()
        if df is None or df.empty:
            raise RuntimeError("Empty dataframe from akshare.spot_quotations_sge()")

        required_cols = {"时间", "现价", "更新时间"}
        missing = required_cols - set(df.columns)
        if missing:
            raise RuntimeError(f"Unexpected columns from data source, missing: {sorted(missing)}")

        # '时间' is HH:MM:SS, pick the latest time.
        # Note: sometimes the source keeps older rows; 更新时间 is the fetch timestamp in Chinese format.
        df2 = df.copy()
        def _time_to_seconds(v) -> Optional[int]:
            if isinstance(v, dt.time):
                return v.hour * 3600 + v.minute * 60 + v.second
            s = str(v).strip()
            if not s:
                return None
            try:
                hh, mm, ss = s.split(":")
                return int(hh) * 3600 + int(mm) * 60 + int(ss)
            except Exception:
                return None

        df2["_t"] = df2["时间"].map(_time_to_seconds)
        df2 = df2.dropna(subset=["_t"]).astype({"_t": "int64"})
        if df2.empty:
            raise RuntimeError("Could not parse time column from data source")

        row = df2.loc[df2["_t"].idxmax()]

        price = float(row["现价"])
        source_updated_at = str(row.get("更新时间")) if "更新时间" in row else None

        # Use local date + source HH:MM:SS for ts; if parsing fails, fall back to now().
        try:
            hh, mm, ss = str(row["时间"]).split(":")
            t = dt.time(int(hh), int(mm), int(ss))
            now = dt.datetime.now()
            ts = dt.datetime.combine(now.date(), t)
        except Exception:
            ts = dt.datetime.now()

        return Tick(ts=ts, price=price, source_updated_at=source_updated_at)


def _pct(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0


def compute_price_action(window: Deque[Tick]) -> dict:
    """
    Compute price-action proxy metrics and qualitative inference.
    """
    ticks = list(window)
    if len(ticks) < 5:
        return {
            "ready": False,
            "reason": f"need more points ({len(ticks)}/5)",
        }

    prices = [t.price for t in ticks]
    p0, p1 = prices[0], prices[-1]
    trend_pct = _pct(p0, p1)

    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    abs_path = sum(abs(d) for d in diffs)
    displacement = abs(p1 - p0) + 1e-9
    choppiness = abs_path / displacement  # >= 1.0; lower means smoother trend

    # Realized volatility proxy (percentage), based on 1-step returns.
    rets = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev == 0:
            continue
        rets.append((prices[i] - prev) / prev)
    if len(rets) >= 2:
        vol = float(pd.Series(rets).std(ddof=1))
    else:
        vol = 0.0
    vol_pct = vol * 100.0

    # Trend strength: bigger trend relative to volatility => more directional.
    # Add small epsilon so calm market doesn't explode.
    trend_strength = abs(trend_pct) / (vol_pct + 0.05)

    # Smoothness in [0,1]: higher is smoother.
    smoothness = 1.0 / choppiness

    # Institutional-like when movement is directional AND relatively smooth.
    # Retail-like when movement is choppy and trend is weak (noise-dominated).
    inst_raw = (trend_strength - 1.0) + (smoothness - 0.35) * 3.0
    inst_score = 1.0 / (1.0 + math.exp(-inst_raw))  # sigmoid to (0,1)
    retail_score = 1.0 - inst_score

    if trend_pct > 0.02:
        flow = "偏流入（价格上行代理）"
    elif trend_pct < -0.02:
        flow = "偏流出（价格下行代理）"
    else:
        flow = "中性/震荡（价格代理）"

    if inst_score >= 0.65:
        who = "更像机构主导（趋势更平滑/更具方向性）"
    elif inst_score <= 0.35:
        who = "更像散户主导（更碎片化/更噪声）"
    else:
        who = "机构/散户不明朗（混合状态）"

    confidence = int(round(100.0 * max(inst_score, retail_score) * min(1.0, trend_strength / 3.0)))

    return {
        "ready": True,
        "trend_pct": trend_pct,
        "vol_pct": vol_pct,
        "choppiness": choppiness,
        "trend_strength": trend_strength,
        "smoothness": smoothness,
        "inst_score": inst_score,
        "retail_score": retail_score,
        "flow": flow,
        "who": who,
        "confidence": confidence,
    }


def render_table(latest: Tick, window: Deque[Tick], metrics: dict) -> Table:
    table = Table(title="AU9999 盯盘（AU99.99 / SGE）", show_lines=False)
    table.add_column("字段", style="bold")
    table.add_column("数值")

    table.add_row("当前价", f"{latest.price:.2f}")
    table.add_row("时间", latest.ts.strftime("%Y-%m-%d %H:%M:%S"))
    if latest.source_updated_at:
        table.add_row("源更新时间", latest.source_updated_at)

    if metrics.get("ready"):
        table.add_row("窗口点数", str(len(window)))
        table.add_row("窗口趋势", f"{metrics['trend_pct']:+.3f}%")
        table.add_row("波动(1步std)", f"{metrics['vol_pct']:.3f}%")
        table.add_row("碎片度(越高越乱)", f"{metrics['choppiness']:.2f}")
        table.add_row("趋势强度", f"{metrics['trend_strength']:.2f}")
        table.add_row("结论-流向", metrics["flow"])
        table.add_row("结论-参与者", metrics["who"])
        table.add_row("置信度(0-100)", str(metrics["confidence"]))
    else:
        table.add_row("状态", "采集中…")
        table.add_row("原因", metrics.get("reason", "n/a"))

    note = Text(
        "提示：此脚本仅使用公开分钟价做“价格行为代理”判断，无法等价替代真实成交明细/盘口(Level-2)的资金流向。",
        style="dim",
    )
    table.caption = note
    return table


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch AU9999 (SGE Au99.99) realtime price and proxy flow inference.")
    p.add_argument("--interval", type=float, default=5.0, help="Polling interval seconds. Default: 5")
    p.add_argument("--window", type=int, default=60, help="Window size in points. Default: 60")
    p.add_argument("--once", action="store_true", help="Fetch once and exit")
    p.add_argument("--csv-out", type=str, default="", help="Optional CSV path to append ticks")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    console = Console()

    provider = AkshareSgeAu9999Provider()
    window: Deque[Tick] = deque(maxlen=max(10, int(args.window)))

    last_seen_key: Optional[Tuple[dt.datetime, float]] = None

    def append_csv(t: Tick) -> None:
        if not args.csv_out:
            return
        df = pd.DataFrame(
            [{"ts": t.ts.isoformat(sep=" "), "price": t.price, "source_updated_at": t.source_updated_at}]
        )
        try:
            import os

            header = not os.path.exists(args.csv_out)
            df.to_csv(args.csv_out, mode="a", header=header, index=False)
        except Exception as e:
            console.print(f"[yellow]CSV 写入失败：{e}[/yellow]")

    def one_shot() -> int:
        latest = provider.fetch_latest()
        window.append(latest)
        metrics = compute_price_action(window)
        console.print(render_table(latest, window, metrics))
        append_csv(latest)
        return 0

    if args.once:
        return one_shot()

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            try:
                latest = provider.fetch_latest()
                key = (latest.ts, latest.price)
                if key != last_seen_key:
                    window.append(latest)
                    last_seen_key = key
                    append_csv(latest)

                metrics = compute_price_action(window)
                live.update(render_table(latest, window, metrics))
            except KeyboardInterrupt:
                break
            except Exception as e:
                live.update(
                    Table(
                        title="AU9999 盯盘（错误）",
                        caption=Text("检查网络/数据源可用性，或稍后重试。", style="dim"),
                    )
                )
                console.print(f"[red]拉取失败：{type(e).__name__}: {e}[/red]")
                time.sleep(max(2.0, float(args.interval)))
                continue

            time.sleep(max(1.0, float(args.interval)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from au9999.flow import DEFAULT_BUCKETS, compute_bucket_flows, summarize_total_net
from au9999.providers import CsvTicksProvider, MockQuoteProvider, iter_poll_quotes


def _fmt_change(last: float, prev: Optional[float]) -> Text:
    if prev is None or prev == 0:
        return Text("n/a")
    chg = last - prev
    pct = chg / prev * 100
    s = f"{chg:+.2f} ({pct:+.2f}%)"
    style = "green" if chg >= 0 else "red"
    return Text(s, style=style)


def _render_flows(console: Console, flows) -> None:
    table = Table(title="分层净流入/流出（按成交额阈值估算）", show_lines=False)
    table.add_column("层级", justify="left")
    table.add_column("买入额", justify="right")
    table.add_column("卖出额", justify="right")
    table.add_column("净额(买-卖)", justify="right")

    for f in flows:
        net = f.net_amount
        style = "green" if net >= 0 else "red"
        table.add_row(
            f.bucket.name,
            f"{f.buy_amount:,.0f}",
            f"{f.sell_amount:,.0f}",
            Text(f"{net:,.0f}", style=style),
        )
    total = summarize_total_net(flows)
    table.add_section()
    table.add_row("TOTAL", "", "", Text(f"{total:,.0f}", style=("green" if total >= 0 else "red")))
    console.print(table)


def main() -> int:
    ap = argparse.ArgumentParser(description="AU9999 盯盘 + 分层净流入/流出（需要逐笔成交数据）")
    ap.add_argument("--symbol", default="AU9999", help="标的代码（展示用）")
    ap.add_argument("--provider", choices=["mock", "csv"], default="mock", help="数据源：mock(行情) 或 csv(逐笔回放)")
    ap.add_argument("--ticks-csv", default="sample_ticks.csv", help="逐笔 CSV 路径（provider=csv 时使用）")
    ap.add_argument("--interval", type=float, default=1.0, help="轮询间隔秒（mock 行情盯盘）")
    args = ap.parse_args()

    console = Console()

    if args.provider == "csv":
        tp = CsvTicksProvider(path=args.ticks_csv)
        ticks = tp.get_ticks(args.symbol)
        flows = compute_bucket_flows(ticks, buckets=DEFAULT_BUCKETS, infer_side_if_unknown=True)
        console.print(f"[bold]symbol[/bold]={args.symbol}  ticks={len(ticks)}  asof={datetime.now(timezone.utc).isoformat()}")
        _render_flows(console, flows)
        return 0

    # mock quote watch
    qp = MockQuoteProvider()
    console.print("提示：当前为 mock 行情盯盘；要判断散户/机构流入流出，需要接入逐笔成交/盘口数据源。")
    for q in iter_poll_quotes(qp, args.symbol, args.interval):
        chg = _fmt_change(q.last, q.prev_close)
        vol = f"{q.volume:,.0f}" if q.volume is not None else "n/a"
        console.print(f"{q.ts.isoformat()}  {q.symbol}  last={q.last:.2f}  chg={chg}  vol={vol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


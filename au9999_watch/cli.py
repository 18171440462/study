from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from .signals import SignalEngine
from .sina_sge import SinaSGEClient


def _fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        # local time, ISO-ish
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_num(x: float | None, nd: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:.{nd}f}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="au9999-watch", description="AU9999 real-time watch with heuristic flow signals.")
    p.add_argument("--symbol", default="AU9999", help="SGE symbol (default: AU9999)")
    p.add_argument("--interval", type=float, default=2.0, help="Polling interval seconds (default: 2)")
    p.add_argument("--window", type=int, default=60, help="Rolling window length (default: 60)")
    p.add_argument("--once", action="store_true", help="Fetch once and exit")
    p.add_argument("--json", action="store_true", help="Output JSON lines")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    client = SinaSGEClient(symbol=args.symbol)
    engine = SignalEngine(window=args.window)

    while True:
        try:
            q = client.fetch_quote()
            snap = engine.step(q)
        except KeyboardInterrupt:
            return 130
        except Exception as e:
            if args.json:
                sys.stdout.write(json.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
            else:
                sys.stdout.write(f"[{_fmt_ts(None)}] ERROR: {e}\n")
            sys.stdout.flush()
            if args.once:
                return 1
            time.sleep(max(0.5, args.interval))
            continue

        if args.json:
            sys.stdout.write(json.dumps(snap.to_dict(), ensure_ascii=False) + "\n")
        else:
            qd = snap.quote
            sys.stdout.write(
                f"[{_fmt_ts(qd.ts)}] {qd.symbol} last={_fmt_num(qd.last)} "
                f"dLast={_fmt_num(snap.d_last)} spread={_fmt_num(snap.spread)} "
                f"dAmt={_fmt_num(snap.d_amount, 0)} dVol={_fmt_num(snap.d_volume, 0)} "
                f"imb={_fmt_num(snap.depth_imbalance, 3)} "
                f"flow={snap.flow_direction} hint={snap.participant_hint} score={_fmt_num(snap.score, 3)}\n"
            )
        sys.stdout.flush()

        if args.once:
            return 0
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())


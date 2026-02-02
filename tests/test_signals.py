import unittest
from datetime import datetime

from au9999_watch.signals import SignalEngine
from au9999_watch.types import Quote


class TestSignals(unittest.TestCase):
    def _q(self, last: float, volume: float, amount: float, ts: str) -> Quote:
        return Quote(
            symbol="AU9999",
            name_cn="沪金99",
            name_en="Au99.99",
            last=last,
            bid=last - 0.1,
            ask=last + 0.1,
            bid_vol=100.0,
            ask_vol=100.0,
            volume=volume,
            amount=amount,
            ts=datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"),
            pct_change=None,
            raw_fields=(),
            source="test",
        )

    def test_flow_direction(self) -> None:
        eng = SignalEngine(window=20, min_history=3)
        s1 = eng.step(self._q(500.0, 1000.0, 1_000_000.0, "2026-02-02 11:00:00"))
        self.assertEqual(s1.flow_direction, "unknown")
        s2 = eng.step(self._q(501.0, 1100.0, 1_200_000.0, "2026-02-02 11:00:02"))
        self.assertEqual(s2.flow_direction, "inflow")
        s3 = eng.step(self._q(499.0, 1200.0, 1_400_000.0, "2026-02-02 11:00:04"))
        self.assertEqual(s3.flow_direction, "outflow")


if __name__ == "__main__":
    unittest.main()


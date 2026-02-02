import unittest

from au9999_watch.sina_sge import parse_sina_sge_payload


class TestSinaSgeParse(unittest.TestCase):
    def test_parse_nonempty(self) -> None:
        payload = (
            'var hq_str_SGE_AU9999="AU9999,沪金99,Au99.99,1053.00,1065.02,1163.95,'
            '1153.00,1157.00,1025.00,1164.00,1052.50,1055.00,600.00,19.00,'
            '1316700.00,1402243328000.00,2026-02-02 11:32:54,-9.54%";'
        )
        q = parse_sina_sge_payload(payload, source="test")
        self.assertEqual(q.symbol, "AU9999")
        self.assertEqual(q.name_en, "Au99.99")
        self.assertAlmostEqual(q.last or 0, 1157.00, places=2)
        self.assertAlmostEqual(q.bid or 0, 1052.50, places=2)
        self.assertAlmostEqual(q.ask or 0, 1055.00, places=2)
        self.assertAlmostEqual(q.bid_vol or 0, 600.0, places=2)
        self.assertAlmostEqual(q.ask_vol or 0, 19.0, places=2)
        self.assertEqual(q.ts.strftime("%Y-%m-%d %H:%M:%S"), "2026-02-02 11:32:54")
        self.assertAlmostEqual(q.pct_change or 0, -9.54, places=2)

    def test_parse_empty(self) -> None:
        payload = 'var hq_str_SGE_AU9999="";'
        q = parse_sina_sge_payload(payload, source="test")
        self.assertIsNone(q.last)
        self.assertEqual(q.symbol, "AU9999")


if __name__ == "__main__":
    unittest.main()


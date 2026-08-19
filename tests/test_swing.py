#!/usr/bin/env python3
"""Uji mesin swing & manajemen watchlist. Jalankan: python tests/test_swing.py"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import swing  # noqa: E402


def series(n=260, drift=0.006, tail=None):
    """Bangun deret harga sintetis: naik konstan, lalu opsional pullback."""
    closes, p = [], 100.0
    for _ in range(n):
        p *= 1 + drift
        closes.append(p)
    for step in (tail or []):
        p *= 1 + step
        closes.append(p)
    highs = [c * 1.008 for c in closes]
    lows = [c * 0.992 for c in closes]
    vols = [6_000_000.0] * len(closes)
    return highs, lows, closes, vols


HEALTHY_FUND = {
    "rev_growth": 0.30, "eps_growth": 0.45, "roe": 0.25, "op_margin": 0.30,
    "fcf": 4e9, "d2e": 40, "current_ratio": 2.0, "peg": 0.9, "fwd_pe": 25,
}


class TestSwingEngine(unittest.TestCase):

    def test_pullback_dalam_tren_naik_menghasilkan_buy(self):
        h, l, c, v = series(tail=[-0.009] * 6)
        r = swing.evaluate(swing.technicals(h, l, c, v), HEALTHY_FUND,
                           days_to_earnings=25)
        self.assertEqual(r["verdict"], "BUY")
        self.assertGreaterEqual(r["score"], swing.BUY_SCORE)
        self.assertGreater(r["rr"], swing.MIN_RR)
        self.assertLess(r["stop"], r["entry"])
        self.assertGreater(r["target"], r["entry"])

    def test_tren_turun_selalu_avoid(self):
        h, l, c, v = series(drift=-0.004)
        r = swing.evaluate(swing.technicals(h, l, c, v), HEALTHY_FUND)
        self.assertEqual(r["verdict"], "AVOID")
        self.assertTrue(r["blockers"])

    def test_overbought_ditahan_jadi_wait(self):
        # Naik tajam tanpa jeda -> RSI menembus 72.
        h, l, c, v = series(drift=0.012)
        t = swing.technicals(h, l, c, v)
        self.assertGreater(t["rsi"], 72)
        r = swing.evaluate(t, HEALTHY_FUND, days_to_earnings=30)
        self.assertEqual(r["verdict"], "WAIT")
        self.assertFalse(next(c for c in r["checks"] if c["label"] == "RSI sehat")["pass"])

    def test_earnings_dekat_menahan_buy(self):
        h, l, c, v = series(tail=[-0.009] * 6)
        t = swing.technicals(h, l, c, v)
        self.assertEqual(swing.evaluate(t, HEALTHY_FUND, days_to_earnings=30)["verdict"], "BUY")
        r = swing.evaluate(t, HEALTHY_FUND, days_to_earnings=2)
        self.assertEqual(r["verdict"], "WAIT")

    def test_likuiditas_tipis_diblokir(self):
        h, l, c, v = series(tail=[-0.009] * 6)
        v = [1000.0] * len(v)          # ~USD 100 ribu/hari
        r = swing.evaluate(swing.technicals(h, l, c, v), HEALTHY_FUND)
        self.assertEqual(r["verdict"], "AVOID")
        self.assertTrue(any("Likuiditas" in b for b in r["blockers"]))

    def test_riwayat_pendek_tidak_dipercaya(self):
        h, l, c, v = series(n=40)
        r = swing.evaluate(swing.technicals(h, l, c, v), HEALTHY_FUND)
        self.assertEqual(r["verdict"], "AVOID")

    def test_etf_tidak_dihukum_karena_tanpa_fundamental(self):
        h, l, c, v = series(tail=[-0.009] * 6)
        t = swing.technicals(h, l, c, v)
        self.assertGreaterEqual(swing.evaluate(t, {}, is_etf=True)["fund_score"], 55)

    def test_skor_selalu_dalam_rentang(self):
        for drift in (-0.01, -0.002, 0.001, 0.006, 0.02):
            h, l, c, v = series(drift=drift)
            r = swing.evaluate(swing.technicals(h, l, c, v), HEALTHY_FUND)
            self.assertGreaterEqual(r["score"], 0)
            self.assertLessEqual(r["score"], 100)
            for k, s in r["parts"].items():
                self.assertGreaterEqual(s, 0, k)
                self.assertLessEqual(s, 100, k)

    def test_data_kosong_tidak_meledak(self):
        t = swing.technicals([], [], [], [])
        r = swing.evaluate(t, {})
        self.assertEqual(r["verdict"], "AVOID")


class TestWatchlist(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        import watchlist as wl
        self.wl = wl
        self._orig = (wl.CORE_FILE, wl.AUTO_FILE, wl.EXCLUDE_FILE)
        wl.CORE_FILE = os.path.join(self.dir, "core.txt")
        wl.AUTO_FILE = os.path.join(self.dir, "auto.json")
        wl.EXCLUDE_FILE = os.path.join(self.dir, "exclude.txt")
        with open(wl.CORE_FILE, "w") as f:
            f.write("# komentar\nNVDA\nAMD\n")

    def tearDown(self):
        self.wl.CORE_FILE, self.wl.AUTO_FILE, self.wl.EXCLUDE_FILE = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_tambah_dan_hapus(self):
        wl = self.wl
        self.assertEqual(wl.read_pinned(), ["NVDA", "AMD"])
        self.assertTrue(wl.add_pinned("tsla")[0])
        self.assertIn("TSLA", wl.read_pinned())
        self.assertFalse(wl.add_pinned("TSLA")[0])          # duplikat ditolak
        self.assertFalse(wl.add_pinned("bukan ticker")[0])  # format ditolak
        self.assertTrue(wl.remove_pinned("TSLA")[0])
        self.assertNotIn("TSLA", wl.read_pinned())
        self.assertFalse(wl.remove_pinned("TSLA")[0])

    def test_komentar_tetap_terjaga(self):
        wl = self.wl
        wl.add_pinned("MSFT")
        wl.remove_pinned("AMD")
        with open(wl.CORE_FILE) as f:
            text = f.read()
        self.assertIn("# komentar", text)
        self.assertNotIn("AMD", text)
        self.assertIn("MSFT", text)

    def test_hapus_kandidat_auto_ikut_dicekal(self):
        wl = self.wl
        wl.write_auto([{"ticker": "COST", "score": 70}])
        self.assertIn("COST", wl.resolve())
        self.assertTrue(wl.remove_pinned("COST")[0])
        self.assertIn("COST", wl.read_excluded())
        self.assertNotIn("COST", wl.resolve())

    def test_cekal_menang_atas_pinned(self):
        wl = self.wl
        wl.exclude_ticker("AMD")
        self.assertNotIn("AMD", wl.resolve())

    def test_promosi_dari_auto_ke_pinned(self):
        wl = self.wl
        wl.write_auto([{"ticker": "SHOP", "score": 72}])
        self.assertTrue(wl.add_pinned("SHOP")[0])
        self.assertIn("SHOP", wl.read_pinned())
        self.assertEqual(wl.auto_tickers(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

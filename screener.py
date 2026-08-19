#!/usr/bin/env python3
"""
screener.py
===========
Mencari emiten baru yang potensial untuk swing trading, lalu memperbarui
`watchlist_auto.json`. Dijalankan tiap 5 hari oleh GitHub Actions.

Alur — sengaja dua tahap supaya hemat request ke Yahoo:
  1. Unduh riwayat harga seluruh universe secara BATCH (satu request per 40
     ticker), hitung skor teknikal murni. Murah dan cepat.
  2. Hanya untuk ~15 teratas, ambil fundamental satu per satu dan hitung skor
     penuh. Mahal, jadi dibatasi.

Kandidat yang lolos harus:
  * bukan emiten pinned (pilihanmu) dan bukan yang dicekal,
  * verdict bukan AVOID,
  * skor >= MIN_SCORE,
  * likuiditas & volatilitas layak untuk swing.

Emiten pinned TIDAK PERNAH disentuh screener. Yang dirotasi hanya slot auto.
"""

import sys
import time
from datetime import datetime, timezone

import swing
import market_data as md
import watchlist as wl

MIN_SCORE = 60
DEEP_DIVE = 15          # berapa kandidat teratas yang diambil fundamentalnya
CHUNK = 40              # ticker per request batch

# Universe: saham & ETF US paling likuid lintas sektor.
# Sengaja statis supaya screener tidak bergantung pada satu sumber daftar
# yang bisa berubah/mati. Perluas sendiri kalau mau cakupan lebih lebar.
UNIVERSE = [
    # mega cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "ORCL",
    "AMD", "CRM", "ADBE", "NFLX", "CSCO", "INTC", "QCOM", "TXN", "MU", "AMAT",
    "LRCX", "KLAC", "SNPS", "CDNS", "PANW", "CRWD", "NOW", "INTU", "IBM",
    "UBER", "SHOP", "SQ", "PLTR", "SNOW", "DDOG", "NET", "MDB", "ZS", "TEAM",
    "ARM", "SMCI", "DELL", "HPQ", "WDC", "STX", "ON", "MRVL", "NXPI", "ADI",
    # konsumen & ritel
    "COST", "WMT", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG", "LULU",
    "TJX", "ROST", "DG", "YUM", "DPZ", "ABNB", "BKNG", "MAR", "RCL", "DAL",
    # kesehatan
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "AMGN",
    "ISRG", "VRTX", "REGN", "GILD", "BSX", "SYK", "MDT", "CI", "ELV", "HCA",
    # keuangan
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "SPGI", "AXP",
    "V", "MA", "PYPL", "COF", "USB", "PNC", "CB", "PGR", "MMC", "ICE",
    # industri & energi
    "CAT", "DE", "HON", "GE", "BA", "LMT", "RTX", "UNP", "UPS", "FDX",
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY", "WMB",
    # lain-lain likuid
    "MELI", "SE", "COIN", "HOOD", "SOFI", "DASH", "RBLX", "SPOT", "TTD", "PINS",
    "LIN", "APD", "SHW", "NEM", "FCX", "NUE", "DOW", "PPG",
    # ETF sebagai pembanding regime
    "SPY", "QQQ", "VOO", "IWM", "XLK", "XLF", "XLE", "XLV", "SMH", "IBIT",
]


def _batch_history(tickers):
    """Unduh riwayat 1 tahun untuk banyak ticker sekaligus.

    Return dict {ticker: swing.technicals(...)}. Ticker yang gagal dilewati.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance tidak terpasang", file=sys.stderr)
        return {}

    out = {}
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        print(f"  batch {i // CHUNK + 1}: {len(chunk)} ticker ...", flush=True)
        data = None
        for attempt in range(3):
            try:
                data = yf.download(chunk, period="1y", interval="1d",
                                   group_by="ticker", auto_adjust=True,
                                   progress=False, threads=True)
                if data is not None and not data.empty:
                    break
            except Exception as e:  # noqa: BLE001
                print(f"    percobaan {attempt + 1} gagal: {e}")
            time.sleep(md.BACKOFF * (attempt + 1))

        if data is None or data.empty:
            continue

        for t in chunk:
            try:
                df = data[t] if len(chunk) > 1 else data
                df = df.dropna(subset=["Close"])
                if len(df) < 120:
                    continue
                tech = swing.technicals(
                    df["High"].tolist(), df["Low"].tolist(),
                    df["Close"].tolist(),
                    df["Volume"].fillna(0).tolist() if "Volume" in df else [],
                )
                out[t] = tech
            except Exception:  # noqa: BLE001
                continue
        time.sleep(1.5)
    return out


def run(max_slots=None) -> list:
    max_slots = max_slots or wl.MAX_AUTO
    pinned = set(wl.read_pinned())
    excluded = set(wl.read_excluded())

    universe = [t for t in dict.fromkeys(UNIVERSE)
                if t not in pinned and t not in excluded]
    print(f"Screening {len(universe)} emiten (pinned & cekal dikecualikan)...")

    techs = _batch_history(universe)
    print(f"Riwayat harga berhasil untuk {len(techs)} emiten.")
    if not techs:
        print("Tidak ada data — watchlist_auto.json tidak diubah.")
        return []

    # Tahap 1: skor teknikal murni (fundamental netral).
    prelim = []
    for t, tech in techs.items():
        sw = swing.evaluate(tech, {}, is_etf=t in ("SPY", "QQQ", "VOO", "IWM",
                                                   "XLK", "XLF", "XLE", "XLV",
                                                   "SMH", "IBIT"))
        if sw["verdict"] == "AVOID":
            continue
        prelim.append((sw["tech_score"], t, tech))
    prelim.sort(reverse=True, key=lambda x: x[0])
    shortlist = prelim[:DEEP_DIVE]
    print(f"Shortlist teknikal: {', '.join(t for _, t, _ in shortlist) or '—'}")

    # Tahap 2: fundamental untuk shortlist saja.
    scored = []
    for tech_score, t, tech in shortlist:
        fund = md.get_fundamentals(t) or {}
        quote_type = (fund.get("quote_type") or "").upper()
        is_etf = quote_type in ("ETF", "MUTUALFUND", "INDEX")
        sw = swing.evaluate(tech, fund, is_etf=is_etf)
        if sw["verdict"] == "AVOID" or sw["score"] < MIN_SCORE:
            continue
        scored.append({
            "ticker": t,
            "name": fund.get("name") or t,
            "score": sw["score"],
            "verdict": sw["verdict"],
            "tech_score": sw["tech_score"],
            "fund_score": sw["fund_score"],
            "why": sw["headline"],
            "added": datetime.now(timezone.utc).date().isoformat(),
        })
        time.sleep(md.PAUSE_BETWEEN)

    scored.sort(key=lambda e: e["score"], reverse=True)
    chosen = scored[:max_slots]

    # Pertahankan tanggal "added" kandidat yang bertahan dari siklus sebelumnya.
    prev = {e["ticker"]: e for e in wl.read_auto().get("tickers", [])}
    for e in chosen:
        if e["ticker"] in prev and prev[e["ticker"]].get("added"):
            e["added"] = prev[e["ticker"]]["added"]

    wl.write_auto(chosen)

    dropped = [t for t in prev if t not in {e["ticker"] for e in chosen}]
    print(f"\nKandidat baru ({len(chosen)}):")
    for e in chosen:
        print(f"  {e['ticker']:6s} skor {e['score']:3d}  {e['verdict']:5s}  {e['why']}")
    if dropped:
        print(f"Dikeluarkan dari slot auto: {', '.join(dropped)}")
    return chosen


if __name__ == "__main__":
    run()

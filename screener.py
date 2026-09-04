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

import json
import os
import sys
import time
from datetime import date, datetime, timezone

import swing
import market_data as md
import watchlist as wl

MIN_SCORE = 60
DEEP_DIVE = 15          # berapa kandidat teratas yang diambil fundamentalnya
CHUNK = 40              # ticker per request batch
ROTATION_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rotation_state.json")
ROTATION_SESSIONS = 5   # lima tanggal bursa BERBEDA, bukan kalender */5
# File yang boleh ditulis mesin rotasi: watchlist_auto.json (output screener)
# dan rotation_state.json. watchlist_core.txt (pinned) TIDAK PERNAH di sini.
ROTATION_WRITES = ("watchlist_auto.json", "rotation_state.json")

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


def _session_dates_of(bars):
    """Tanggal bursa (hari kalender, UTC) dari bar harian terbaru.

    Kembalikan list `date` naik; sesi diambil dari timestamp bar yang
    SUDAH TERJADI di data pasar (exchange-verified), bukan disimpulkan
    dari kalender. Bar intraday/hari ini dibuang: hanya sesi lengkap.
    Tidak ada data -> [] (fail closed: rotasi dilewati).
    """
    if not bars or not bars.asofs:
        return []
    out = []
    seen = set()
    for stamp in bars.asofs:
        try:
            parsed = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        day = parsed.date()
        if day.weekday() >= 5:
            continue
        if day not in seen:
            seen.add(day)
            out.append(day)
    return out


def _only_completed(dates):
    """Sesi lengkap saja: 09:30-15:50 ET = hari ini belum tutup -> buang."""
    if not dates:
        return []
    out = []
    for d in dates:
        if d.weekday() >= 5:
            continue
        out.append(d)
    # bar terakhir jam 09:30 ET = sesi hari ini yang belum selesai
    return out[:-1] if out and len(out) > 1 else out


def _new_sessions(anchor, observed):
    """Sesi unik naik yang terjadi SETELAH anchor (anchor tidak dihitung)."""
    if not observed:
        return []
    uniq = []
    seen = set()
    for d in sorted(observed):
        if d.weekday() >= 5 or d in seen:
            continue
        seen.add(d)
        if anchor is None or d > anchor:
            uniq.append(d)
    return uniq


def new_sessions(anchor, observed):
    """Public helper: sesi bursa baru sejak rotasi terakhir (test-friendly)."""
    return _new_sessions(anchor, observed)


def rotation_due(anchor, observed, min_gap=ROTATION_SESSIONS):
    """True bila >= min_gap sesi bursa BERBEDA telah lewat sejak anchor.

    Fail closed: observed None/kosong (kalender pasar tak bisa diverifikasi)
    -> False. Hari libur tidak pernah diasumsikan: sesi datang dari daftar
    tanggal yang diamati, anchor selalu sesi lengkap.
    """
    if not observed:
        return False
    return len(_new_sessions(anchor, observed)) >= min_gap


def due_on(anchor, observed, min_gap=ROTATION_SESSIONS):
    """Sesi ke-min_gap sejak anchor (tanggal rotasi berikutnya), atau None."""
    if not observed:
        return None
    new = _new_sessions(anchor, observed)
    if len(new) < min_gap:
        return None
    return new[min_gap - 1]


def auto_candidates(candidates, pinned):
    """Kandidat auto bersih: pinned TIDAK PERNAH masuk slot auto."""
    pinned = set(pinned or ())
    return [c for c in (candidates or []) if c.get("ticker") not in pinned]


class RotationState:
    """rotation_state.json: anchor (rotasi terakhir) + sesi sejak anchor.

    Sesi = tanggal bursa lengkap yang sudah diamati (exchange-verified).
    File korup/absent -> state kosong (anchor None); rotasi berikutnya
    butuh data sesi nyata, jadi aman fail-closed.
    """

    def __init__(self, path=ROTATION_STATE_FILE):
        self.path = path
        self.last_rotation = None
        self.sessions = []
        self._load()

    # Task 9 review: test API memakai `anchor`; implementasi menyimpan
    # `last_rotation`. Alias ini menyatukan keduanya.
    @property
    def anchor(self):
        return self.last_rotation

    @anchor.setter
    def anchor(self, value):
        self.last_rotation = value


    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        try:
            self.last_rotation = (date.fromisoformat(raw["last_rotation"])
                                  if raw.get("last_rotation") else None)
        except (TypeError, ValueError):
            self.last_rotation = None
        self.sessions = []
        for s in raw.get("sessions") or []:
            try:
                d = date.fromisoformat(str(s))
            except ValueError:
                continue
            if d.weekday() < 5:
                self.sessions.append(d)
        self.sessions = sorted(set(self.sessions))

    def save(self):
        payload = {
            "last_rotation": (self.last_rotation.isoformat()
                              if self.last_rotation else None),
            "sessions": [d.isoformat() for d in self.sessions],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
            f.write("\n")

    def reset(self, rotated_on):
        self.last_rotation = rotated_on
        self.sessions = []


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


def rotate_if_due(observed_sessions, pinned, state=None, now=None,
                  max_slots=None):
    """Jalankan rotasi hanya setelah 5 sesi bursa BERBEDA sejak rotasi lalu.

    observed_sessions: tanggal bursa lengkap dari data pasar (naik, unik).
    pinned: ticker inti — TIDAK PERNAH disentuh. Fail closed: kalender tak
    bisa diverifikasi (tidak ada sesi yang diamati) -> dilewati, tidak ada
    yang ditulis. Return (ok, pesan).
    """
    state = state or RotationState()
    if not observed_sessions:
        return False, "SKIP kalender pasar tak bisa diverifikasi " \
                      "(tidak ada sesi lengkap yang diamati)."
    if not rotation_due(state.last_rotation, observed_sessions):
        return False, "SKIP belum 5 sesi bursa sejak rotasi terakhir " \
                      "(%s)." % (state.last_rotation or "belum pernah")
    due = due_on(state.last_rotation, observed_sessions)
    if due is None:  # tidak mungkin setelah rotation_due True, jaga-jaga
        return False, "SKIP sesi rotasi tidak bisa ditentukan."
    pinned = pinned or wl.read_pinned()
    chosen = auto_candidates(run(max_slots=max_slots), pinned)
    state.reset(due)
    state.save()
    return True, "ROTASI selesai pada sesi bursa %s — %d kandidat auto " \
                 "baru (pinned utuh)." % (due.isoformat(), len(chosen))


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


def main(argv=None):
    """CLI: `python screener.py [--rotation]` (--rotation tambahan untuk task 9)."""
    args = list(argv) if argv is not None else sys.argv[1:]
    if "--rotation" not in args:
        run()
        return 0
    # Rotasi berbasis state: sesi bursa diamati dari data harian SPY yang
    # sudah lengkap; kalender tidak bisa diverifikasi -> fail closed.
    bars = md.get_daily_bars("SPY")
    _, msg = rotate_if_due(_only_completed(_session_dates_of(bars)),
                           wl.read_pinned())
    print(msg)
    return 0  # skip bukan error: workflow sukses tanpa rotasi

if __name__ == "__main__":
    sys.exit(main())

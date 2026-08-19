#!/usr/bin/env python3
"""
swing.py
========
Mesin penilaian SWING TRADING (horizon 1-4 minggu).

Berbeda dengan `stock_digest.py` yang dirancang untuk investor DCA jangka
panjang, modul ini menjawab satu pertanyaan sempit:

    "Untuk swing trade beberapa minggu ke depan, emiten ini
     layak BUY, tunggu (WAIT), atau hindari (AVOID)?"

Prinsip desain:
  1. Teknikal memimpin (65%), fundamental menyaring (35%).
     Untuk horizon minggu-an, harga & tren lebih menentukan daripada valuasi,
     tapi fundamental tetap dipakai untuk membuang perusahaan rapuh.
  2. Semua komponen transparan. Setiap sub-skor 0-100 dan bisa diaudit.
  3. Level entry/stop/target berbasis ATR, bukan angka bulat asal.
  4. Hard filter mendahului skor. Sebagus apa pun skornya, saham di bawah
     MA200 dengan MA50 menurun tetap AVOID untuk swing long.

BUKAN nasihat keuangan. Ini alat bantu penyaringan; keputusan tetap di tangan
kamu, dan risiko kerugian selalu ada.
"""

from typing import Optional

# --------------------------------------------------------------------------
# BOBOT
# --------------------------------------------------------------------------
# Teknikal = 0.65, Fundamental = 0.35
WEIGHTS = {
    # teknikal
    "trend": 0.22,        # arah & susunan moving average
    "momentum": 0.16,     # kecepatan gerak + posisi RSI
    "pullback": 0.12,     # kualitas titik masuk (koreksi sehat vs kejar harga)
    "volatility": 0.08,   # ATR% — cukup bergerak tapi tidak liar
    "liquidity": 0.07,    # volume dolar; slippage & kemudahan keluar
    # fundamental
    "growth": 0.12,       # pertumbuhan pendapatan & laba
    "quality": 0.11,      # ROE, margin, arus kas bebas
    "balance": 0.07,      # leverage / neraca
    "valuation": 0.05,    # PEG & fwd P/E — hanya penyaring ekstrem
}

TECH_KEYS = ("trend", "momentum", "pullback", "volatility", "liquidity")
FUND_KEYS = ("growth", "quality", "balance", "valuation")

# Ambang keputusan
BUY_SCORE = 68
AVOID_SCORE = 45
MIN_DOLLAR_VOLUME = 20_000_000     # USD rata-rata 20 hari
MIN_RR = 1.8                       # reward : risk minimum untuk BUY

# Parameter level harga
ATR_STOP_MULT = 1.6
ATR_TARGET_MULT = 3.2


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _num(v, default=None):
    """Ambil angka yang valid; tolak None/NaN/inf."""
    try:
        if v is None:
            return default
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# INDIKATOR TEKNIKAL
# --------------------------------------------------------------------------

def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rsi(closes, period=14):
    """RSI Wilder dengan smoothing — lebih stabil dari simple average."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _atr(highs, lows, closes, period=14):
    """Average True Range — ukuran rentang gerak harian yang wajar."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs = []
    for i in range(n - period, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def technicals(highs, lows, closes, volumes) -> dict:
    """Hitung seluruh indikator teknikal dari deret OHLCV harian.

    Semua argumen adalah list angka, urut lama -> baru.
    Mengembalikan dict; nilai yang tidak bisa dihitung diisi None.
    """
    out = {
        "price": None, "ma20": None, "ma50": None, "ma200": None,
        "rsi": None, "atr": None, "atr_pct": None,
        "vs_ma20": None, "vs_ma50": None, "vs_ma200": None,
        "chg_1d": None, "chg_1w": None, "chg_1m": None, "chg_3m": None,
        "swing_high_20": None, "swing_low_20": None,
        "from_swing_high": None, "dollar_volume": None, "vol_ratio": None,
        "ma50_slope": None, "ma200_slope": None, "bars": len(closes),
    }
    if not closes:
        return out

    last = closes[-1]
    out["price"] = round(last, 2)
    out["ma20"] = _sma(closes, 20)
    out["ma50"] = _sma(closes, 50)
    out["ma200"] = _sma(closes, 200)
    out["rsi"] = _rsi(closes)

    atr = _atr(highs, lows, closes)
    out["atr"] = atr
    if atr and last:
        out["atr_pct"] = round(atr / last * 100, 2)

    for key, ma in (("vs_ma20", out["ma20"]), ("vs_ma50", out["ma50"]),
                    ("vs_ma200", out["ma200"])):
        if ma:
            out[key] = round((last - ma) / ma * 100, 2)

    def chg(bars_back):
        if len(closes) > bars_back and closes[-1 - bars_back]:
            base = closes[-1 - bars_back]
            return round((last - base) / base * 100, 2)
        return None

    out["chg_1d"] = chg(1)
    out["chg_1w"] = chg(5)
    out["chg_1m"] = chg(21)
    out["chg_3m"] = chg(63)

    window = closes[-20:]
    if window:
        out["swing_high_20"] = max(window)
        out["swing_low_20"] = min(window)
        if out["swing_high_20"]:
            out["from_swing_high"] = round(
                (last - out["swing_high_20"]) / out["swing_high_20"] * 100, 2)

    if volumes:
        v20 = volumes[-20:]
        avg_v = sum(v20) / len(v20)
        out["dollar_volume"] = avg_v * last
        v60 = volumes[-60:]
        base_v = (sum(v60) / len(v60)) if v60 else 0
        if base_v:
            out["vol_ratio"] = round(avg_v / base_v, 2)

    # Kemiringan MA: bandingkan MA sekarang vs 10 bar lalu.
    if len(closes) >= 60:
        prev50 = _sma(closes[:-10], 50)
        if prev50 and out["ma50"]:
            out["ma50_slope"] = round((out["ma50"] - prev50) / prev50 * 100, 2)
    if len(closes) >= 210:
        prev200 = _sma(closes[:-10], 200)
        if prev200 and out["ma200"]:
            out["ma200_slope"] = round((out["ma200"] - prev200) / prev200 * 100, 2)

    return out


# --------------------------------------------------------------------------
# SUB-SKOR TEKNIKAL (0-100)
# --------------------------------------------------------------------------

def _score_trend(t) -> float:
    """Susunan MA & kemiringannya. Swing long ingin tren naik yang rapi."""
    price, ma20, ma50, ma200 = t["price"], t["ma20"], t["ma50"], t["ma200"]
    s = 50.0
    if price and ma50:
        s += 14 if price > ma50 else -16
    if price and ma200:
        s += 12 if price > ma200 else -20
    if ma50 and ma200:
        s += 10 if ma50 > ma200 else -12          # golden vs death cross
    if price and ma20:
        s += 6 if price > ma20 else -6
    slope = _num(t["ma50_slope"])
    if slope is not None:
        s += _clamp(slope * 2.5, -12, 12)
    return _clamp(s)


def _score_momentum(t) -> float:
    """Kecepatan gerak + posisi RSI. Terlalu panas justru dikurangi."""
    s = 50.0
    w = _num(t["chg_1w"], 0)
    m = _num(t["chg_1m"], 0)
    q = _num(t["chg_3m"], 0)
    s += _clamp(w * 1.6, -14, 14)
    s += _clamp(m * 0.8, -16, 16)
    s += _clamp(q * 0.25, -10, 10)
    rsi = _num(t["rsi"])
    if rsi is not None:
        # Zona ideal untuk masuk swing: 45-65 (tren hidup, belum kehabisan napas).
        if 45 <= rsi <= 65:
            s += 10
        elif 65 < rsi <= 72:
            s += 2
        elif rsi > 72:
            s -= (rsi - 72) * 1.8          # sudah overbought, entry buruk
        elif 35 <= rsi < 45:
            s -= 4
        else:
            s -= 12                        # momentum benar-benar rusak
    return _clamp(s)


def _score_pullback(t) -> float:
    """Kualitas titik masuk: koreksi sehat > mengejar harga yang lari."""
    s = 55.0
    fsh = _num(t["from_swing_high"])
    if fsh is not None:
        d = abs(fsh)
        if 2 <= d <= 8:
            s += 22                 # pullback ideal ke area support
        elif d < 2:
            s += 4                  # tepat di high: breakout, tapi entry mepet
        elif 8 < d <= 15:
            s += 6
        else:
            s -= min(25, (d - 15) * 1.2)   # sudah rusak, bukan pullback lagi
    ext = _num(t["vs_ma20"])
    if ext is not None:
        if ext > 12:
            s -= (ext - 12) * 1.6   # parabolik, jauh dari MA20
        elif -6 <= ext <= 6:
            s += 8                  # menempel MA20 = risiko terukur
    return _clamp(s)


def _score_volatility(t) -> float:
    """ATR% — swing butuh gerak, tapi bukan roller coaster."""
    a = _num(t["atr_pct"])
    if a is None:
        return 55.0
    if a < 1.0:
        return 40.0                 # terlalu adem, target sulit tercapai
    if a <= 2.0:
        return 78.0
    if a <= 3.5:
        return 88.0                 # sweet spot swing
    if a <= 5.0:
        return 68.0
    if a <= 7.0:
        return 48.0
    return max(15.0, 48.0 - (a - 7.0) * 6)


def _score_liquidity(t) -> float:
    dv = _num(t["dollar_volume"])
    if dv is None:
        return 50.0
    if dv >= 1_000_000_000:
        s = 95.0
    elif dv >= 300_000_000:
        s = 88.0
    elif dv >= 100_000_000:
        s = 78.0
    elif dv >= MIN_DOLLAR_VOLUME:
        s = 62.0
    else:
        s = 25.0
    vr = _num(t["vol_ratio"])
    if vr is not None and vr > 1.2:
        s += 5                      # minat naik: konfirmasi pergerakan
    return _clamp(s)


# --------------------------------------------------------------------------
# SUB-SKOR FUNDAMENTAL (0-100)
# --------------------------------------------------------------------------

def _score_growth(f, is_etf) -> float:
    if is_etf:
        return 60.0
    rg = _num(f.get("rev_growth"))
    eg = _num(f.get("eps_growth"))
    if rg is None and eg is None:
        return 50.0
    s = 50.0
    if rg is not None:
        s += _clamp(rg * 100 * 1.2, -25, 28)
    if eg is not None:
        s += _clamp(eg * 100 * 0.5, -20, 22)
    return _clamp(s)


def _score_quality(f, is_etf) -> float:
    if is_etf:
        return 62.0
    s = 50.0
    roe = _num(f.get("roe"))
    if roe is not None:
        s += _clamp(roe * 100 * 1.0, -25, 25)
    om = _num(f.get("op_margin"))
    if om is not None:
        s += _clamp((om - 0.10) * 100 * 0.8, -18, 18)
    fcf = _num(f.get("fcf"))
    if fcf is not None:
        s += 10 if fcf > 0 else -18
    return _clamp(s)


def _score_balance(f, is_etf) -> float:
    if is_etf:
        return 70.0
    d2e = _num(f.get("d2e"))
    cr = _num(f.get("current_ratio"))
    if d2e is None and cr is None:
        return 55.0
    s = 60.0
    if d2e is not None:
        if d2e < 30:
            s += 22
        elif d2e < 80:
            s += 12
        elif d2e < 150:
            s -= 5
        elif d2e < 250:
            s -= 20
        else:
            s -= 35
    if cr is not None:
        if cr >= 1.5:
            s += 10
        elif cr < 1.0:
            s -= 12
    return _clamp(s)


def _score_valuation(f, is_etf) -> float:
    """Untuk swing, valuasi hanya penyaring ekstrem — bukan mesin utama."""
    if is_etf:
        return 60.0
    peg = _num(f.get("peg"))
    fwd = _num(f.get("fwd_pe"))
    if peg is None and fwd is None:
        return 55.0
    s = 60.0
    if peg is not None and peg > 0:
        if peg < 1:
            s += 20
        elif peg < 2:
            s += 8
        elif peg < 3:
            s -= 8
        else:
            s -= 22
    if fwd is not None and fwd > 0:
        if fwd > 60:
            s -= 15
        elif fwd > 40:
            s -= 7
        elif fwd < 20:
            s += 8
    return _clamp(s)


# --------------------------------------------------------------------------
# PENILAIAN UTAMA
# --------------------------------------------------------------------------

def evaluate(tech: dict, fund: dict = None, is_etf: bool = False,
             days_to_earnings: Optional[int] = None) -> dict:
    """Gabungkan seluruh sub-skor jadi satu keputusan swing.

    Return dict siap-serialisasi untuk data.json.
    """
    fund = fund or {}
    parts = {
        "trend": round(_score_trend(tech)),
        "momentum": round(_score_momentum(tech)),
        "pullback": round(_score_pullback(tech)),
        "volatility": round(_score_volatility(tech)),
        "liquidity": round(_score_liquidity(tech)),
        "growth": round(_score_growth(fund, is_etf)),
        "quality": round(_score_quality(fund, is_etf)),
        "balance": round(_score_balance(fund, is_etf)),
        "valuation": round(_score_valuation(fund, is_etf)),
    }
    score = round(sum(parts[k] * WEIGHTS[k] for k in parts))
    tech_w = sum(WEIGHTS[k] for k in TECH_KEYS)
    fund_w = sum(WEIGHTS[k] for k in FUND_KEYS)
    tech_score = round(sum(parts[k] * WEIGHTS[k] for k in TECH_KEYS) / tech_w)
    fund_score = round(sum(parts[k] * WEIGHTS[k] for k in FUND_KEYS) / fund_w)

    # ---------------- level harga berbasis ATR ----------------
    price = _num(tech.get("price")) or 0.0
    atr = _num(tech.get("atr")) or (price * 0.02 if price else 0.0)
    ma20 = _num(tech.get("ma20"))
    rsi = _num(tech.get("rsi"))

    # Entry: kalau sudah overbought, tunggu koreksi ke MA20 / -0.5 ATR.
    if price:
        if rsi is not None and rsi > 70 and ma20:
            entry = max(ma20, price - atr * 0.8)
        elif ma20 and price > ma20 * 1.08:
            entry = price - atr * 0.5
        else:
            entry = price
    else:
        entry = 0.0

    swing_low = _num(tech.get("swing_low_20"))
    stop = entry - atr * ATR_STOP_MULT
    if swing_low and swing_low < entry:
        # Pakai stop yang lebih ketat di antara ATR-stop dan bawah swing low.
        stop = max(stop, swing_low * 0.985)
    target = entry + atr * ATR_TARGET_MULT

    risk_pct = round((entry - stop) / entry * 100, 2) if entry else None
    reward_pct = round((target - entry) / entry * 100, 2) if entry else None
    rr = round((target - entry) / (entry - stop), 2) if entry and entry > stop else None

    # ---------------- hard filter ----------------
    blockers = []
    vs200 = _num(tech.get("vs_ma200"))
    ma50, ma200 = _num(tech.get("ma50")), _num(tech.get("ma200"))
    slope50 = _num(tech.get("ma50_slope"))
    dv = _num(tech.get("dollar_volume"))
    atr_pct = _num(tech.get("atr_pct"))
    d2e = _num(fund.get("d2e"))
    fcf = _num(fund.get("fcf"))

    if vs200 is not None and vs200 < 0 and (ma50 and ma200 and ma50 < ma200):
        blockers.append("Harga di bawah MA200 dan MA50 masih di bawah MA200 — tren turun.")
    if slope50 is not None and slope50 < -3 and (vs200 is not None and vs200 < 0):
        blockers.append("MA50 menurun tajam sementara harga di bawah MA200.")
    if dv is not None and dv < MIN_DOLLAR_VOLUME:
        blockers.append(f"Likuiditas tipis (~${dv/1e6:.0f} jt/hari) — sulit keluar tanpa slippage.")
    if atr_pct is not None and atr_pct > 9:
        blockers.append(f"ATR {atr_pct}% terlalu liar untuk ukuran posisi yang wajar.")
    if not is_etf and d2e is not None and d2e > 300 and (fcf is not None and fcf < 0):
        blockers.append("Utang sangat tinggi sekaligus arus kas bebas negatif.")
    if tech.get("bars", 0) < 60:
        blockers.append("Riwayat harga kurang dari 60 hari — indikator belum bisa dipercaya.")

    # ---------------- syarat BUY ----------------
    checks = []

    def check(label, ok, detail):
        checks.append({"label": label, "pass": bool(ok), "detail": detail})
        return bool(ok)

    c_score = check("Skor komposit", score >= BUY_SCORE, f"{score}/100 (min {BUY_SCORE})")
    c_trend = check(
        "Tren naik",
        bool(price and ma50 and price > ma50 and ma200 and ma50 > ma200),
        "harga > MA50 > MA200" if price and ma50 and ma200 else "MA belum lengkap",
    )
    c_rsi = check("RSI sehat", rsi is not None and 40 <= rsi <= 72,
                  f"RSI {rsi}" if rsi is not None else "RSI tidak tersedia")
    c_rr = check("Risk/reward", rr is not None and rr >= MIN_RR,
                 f"{rr}:1 (min {MIN_RR}:1)" if rr else "tidak terhitung")
    ext = _num(tech.get("vs_ma20"))
    c_ext = check("Tidak parabolik", ext is None or ext <= 12,
                  f"{ext}% dari MA20" if ext is not None else "—")
    c_earn = check(
        "Jauh dari earnings",
        days_to_earnings is None or days_to_earnings > 3,
        f"{days_to_earnings} hari lagi" if days_to_earnings is not None else "tidak ada jadwal",
    )

    if blockers or score < AVOID_SCORE:
        verdict = "AVOID"
    elif all([c_score, c_trend, c_rsi, c_rr, c_ext, c_earn]):
        verdict = "BUY"
    else:
        verdict = "WAIT"

    # ---------------- narasi ----------------
    reasons, risks = [], []
    if parts["trend"] >= 65:
        reasons.append("Susunan moving average mendukung tren naik.")
    elif parts["trend"] <= 40:
        risks.append("Struktur tren masih lemah; MA belum tersusun naik.")

    if rsi is not None and 45 <= rsi <= 65:
        reasons.append(f"RSI {rsi} di zona sehat — tren hidup, belum kehabisan napas.")
    elif rsi is not None and rsi > 72:
        risks.append(f"RSI {rsi} overbought; entry sekarang mengejar harga.")

    fsh = _num(tech.get("from_swing_high"))
    if fsh is not None and 2 <= abs(fsh) <= 8:
        reasons.append(f"Koreksi {abs(fsh):.1f}% dari puncak 20 hari — area entry yang wajar.")
    elif fsh is not None and abs(fsh) > 15:
        risks.append(f"Sudah {abs(fsh):.1f}% di bawah puncak 20 hari — bukan pullback biasa.")

    if atr_pct is not None:
        if 1.5 <= atr_pct <= 4:
            reasons.append(f"ATR {atr_pct}% memberi ruang gerak yang pas untuk swing.")
        elif atr_pct > 6:
            risks.append(f"ATR {atr_pct}% tinggi — perkecil ukuran posisi.")

    if not is_etf:
        rg = _num(fund.get("rev_growth"))
        if rg is not None and rg > 0.15:
            reasons.append(f"Pendapatan tumbuh {rg*100:.0f}% YoY.")
        elif rg is not None and rg < 0:
            risks.append(f"Pendapatan menyusut {rg*100:.0f}% YoY.")
        if d2e is not None and d2e > 150:
            risks.append(f"Utang/ekuitas {d2e:.0f}% — rapuh kalau pasar berbalik.")
        if fcf is not None and fcf < 0:
            risks.append("Arus kas bebas negatif.")
        peg = _num(fund.get("peg"))
        if peg is not None and 0 < peg < 1:
            reasons.append(f"PEG {peg:.2f} — harga masih masuk akal terhadap pertumbuhan.")
    else:
        reasons.append("ETF: penilaian bersandar pada teknikal, bukan neraca.")

    if days_to_earnings is not None and days_to_earnings <= 7:
        risks.append(f"Earnings {days_to_earnings} hari lagi — gap harga bisa melompati stop.")

    if dv is not None and dv >= 300_000_000:
        vol_txt = (f"${dv/1e9:.1f} miliar" if dv >= 1e9 else f"${dv/1e6:.0f} juta")
        reasons.append(f"Likuid (~{vol_txt}/hari), mudah masuk-keluar.")

    for b in blockers:
        risks.append(b)

    if not reasons:
        reasons.append("Tidak ada faktor pendukung yang menonjol.")
    if not risks:
        risks.append("Tidak ada risiko struktural yang menonjol — tetap pakai stop loss.")

    horizon = "1-4 minggu"
    if verdict == "BUY":
        headline = "Setup swing memenuhi seluruh syarat."
    elif verdict == "AVOID":
        headline = blockers[0] if blockers else "Skor komposit di bawah ambang minimum."
    else:
        failed = [c["label"] for c in checks if not c["pass"]]
        headline = ("Belum memenuhi: " + ", ".join(failed[:3])) if failed else "Menunggu konfirmasi."

    return {
        "score": score,
        "verdict": verdict,
        "headline": headline,
        "tech_score": tech_score,
        "fund_score": fund_score,
        "parts": parts,
        "entry": round(entry, 2) if entry else None,
        "stop": round(stop, 2) if entry else None,
        "target": round(target, 2) if entry else None,
        "rr": rr,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "horizon": horizon,
        "checks": checks,
        "reasons": reasons[:5],
        "risks": risks[:5],
        "blockers": blockers,
        "days_to_earnings": days_to_earnings,
    }

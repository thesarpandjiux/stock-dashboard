#!/usr/bin/env python3
"""
stock_digest.py
================
Digest harian (atau mingguan) untuk watchlist saham US.

Alur:
  1. Ambil data harga & volume via yfinance (gratis, no API key).
  2. Hitung sinyal teknikal OBJEKTIF (MA50/MA200, RSI, jarak dari 52w high/low,
     perubahan harian & mingguan). Ini fakta terhitung, BUKAN rekomendasi beli/jual.
  3. (Opsional) Rangkum jadi narasi Bahasa Indonesia lewat 9router (OpenAI-compatible).
  4. Kirim ke Telegram via Bot API.

CATATAN PENTING (baca sekali):
  - Ini alat MONITORING & BELAJAR, bukan sinyal beli/jual.
  - Kamu investor DCA horizon 3 tahun. Inti DCA = beli jumlah tetap tiap bulan,
    ABAIKAN timing harian. Jangan pakai digest ini untuk menunda/mempercepat beli.
  - "worth to buy" tidak bisa dijawab oleh indikator teknikal harian. Yang di bawah
    hanya menggambarkan KONDISI teknikal (momentum, tren, overbought/oversold).

Dependencies:
    pip install yfinance requests

Konfigurasi: lewat environment variable (lihat bagian CONFIG) atau edit langsung.
"""

import os
import sys
import json
import datetime
from typing import Optional

import requests

try:
    import yfinance as yf
except ImportError:
    sys.exit("yfinance belum terpasang. Jalankan: pip install yfinance requests")


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

import os

import watchlist as wl

# Menentukan root direktori dari script itu sendiri
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Fallback kalau semua file watchlist kosong/hilang.
FALLBACK_TICKERS = ["SPY", "AAPL", "MSFT", "AMZN", "NVDA",
                    "GOOGL", "TSLA", "META", "BRK-B", "JPM"]


def get_watchlist(top_n=None) -> list:
    """Watchlist final = pinned (pilihanmu) + kandidat auto, dikurangi cekal.

    Isinya dikelola lewat `watchlist.py` supaya dashboard, digest Telegram,
    dan GitHub Actions selalu membaca daftar yang sama persis.
    """
    tickers = wl.resolve(limit=top_n)
    return tickers or list(FALLBACK_TICKERS)


# Diisi saat runtime, BUKAN saat import — supaya modul ini aman di-import
# oleh build_dashboard.py / screener.py tanpa memicu panggilan jaringan.
TICKERS = []


# Telegram — buat bot lewat @BotFather, lalu ambil token & chat_id kamu.
# Cara dapat chat_id: kirim pesan ke bot kamu, lalu buka
#   https://api.telegram.org/bot<TOKEN>/getUpdates  dan lihat field "chat":{"id":...}
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 9router (opsional). Kalau ROUTER_ENABLED=False, digest dikirim apa adanya (tanpa LLM).
ROUTER_ENABLED = os.getenv("ROUTER_ENABLED", "false").lower() == "true"
ROUTER_BASE_URL = os.getenv("ROUTER_BASE_URL", "http://localhost:20128/v1")
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude")          # primary route kamu
ROUTER_PROMPT_BUDGET = os.getenv("ROUTER_PROMPT_BUDGET", "low")  # tier-based routing header
ROUTER_API_KEY = os.getenv("ROUTER_API_KEY", "sk-noauth")   # placeholder kalau proxy tak butuh

# RSI period standar.
RSI_PERIOD = 14


# -----------------------------------------------------------------------------
# INDIKATOR TEKNIKAL (murni terhitung, tanpa judgment)
# -----------------------------------------------------------------------------

def compute_rsi(closes, period: int = RSI_PERIOD) -> Optional[float]:
    """RSI klasik (Wilder-ish, simple average). Butuh minimal period+1 data."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        (gains if delta >= 0 else losses).append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def pct(a: float, b: float) -> Optional[float]:
    """Persentase perubahan a terhadap b."""
    if b == 0:
        return None
    return round((a - b) / b * 100, 2)


def analyze_ticker(ticker: str) -> dict:
    """Tarik ~1 tahun data harian dan hitung sinyal objektif untuk satu ticker."""
    result = {"ticker": ticker, "ok": False, "error": None}
    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1d")
        if hist.empty or len(hist) < 60:
            result["error"] = "data tidak cukup"
            return result

        closes = hist["Close"].tolist()
        last = closes[-1]
        prev = closes[-2]
        week_ago = closes[-6] if len(closes) >= 6 else closes[0]

        ma50 = sum(closes[-50:]) / 50
        ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

        year_high = max(closes)
        year_low = min(closes)
        result.update({
            "ok": True,
            "price": round(last, 2),
            # ~3 bulan terakhir untuk sparkline di dashboard (diabaikan digest teks).
            "spark": [round(c, 2) for c in closes[-63:]],
            "chg_1d": pct(last, prev),
            "chg_1w": pct(last, week_ago),
            "rsi": compute_rsi(closes),
            "vs_ma50": pct(last, ma50),          # >0 = di atas MA50
            "vs_ma200": pct(last, ma200) if ma200 else None,
            "from_52w_high": pct(last, year_high),  # biasanya negatif
            "from_52w_low": pct(last, year_low),    # biasanya positif
        })
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result


def condition(row: dict) -> tuple:
    """Skor kondisi teknikal berbasis aturan. BUKAN rekomendasi beli/jual.

    Skor negatif = secara teknikal tertekan/murah. Positif = extended/panas.
    Tiap komponen transparan supaya bisa diaudit sendiri.
    """
    if not row["ok"]:
        return None, "—", []

    score = 0
    why = []

    rsi = row["rsi"]
    if rsi is not None:
        if rsi <= 30:
            score -= 2
            why.append(f"RSI {rsi} oversold")
        elif rsi >= 70:
            score += 2
            why.append(f"RSI {rsi} overbought")

    ma50 = row["vs_ma50"]
    if ma50 is not None:
        if ma50 < -5:
            score -= 1
            why.append(f"{ma50}% di bawah MA50")
        elif ma50 > 10:
            score += 1
            why.append(f"+{ma50}% di atas MA50")

    dd = row["from_52w_high"]
    if dd is not None:
        if dd <= -20:
            score -= 2
            why.append(f"{dd}% dari 52w high")
        elif dd >= -2:
            score += 1
            why.append("dekat 52w high")

    if score <= -2:
        label = "🟢 Tertekan (murah secara teknikal)"
    elif score >= 2:
        label = "🔴 Extended (panas secara teknikal)"
    else:
        label = "⚪ Netral"
    return score, label, why


def fetch_fundamentals(ticker: str) -> dict:
    """Ambil data fundamental. Semua field opsional — yfinance sering kosong (ETF, dll)."""
    keys = {
        "pe": "trailingPE", "fwd_pe": "forwardPE", "target": "targetMeanPrice",
        "analysts": "numberOfAnalystOpinions", "rec": "recommendationKey",
        "rev_growth": "revenueGrowth", "margin": "profitMargins",
        # --- lapis dalam ---
        "roe": "returnOnEquity", "fcf": "freeCashflow", "ocf": "operatingCashflow",
        "debt": "totalDebt", "cash": "totalCash", "current_ratio": "currentRatio",
        "d2e": "debtToEquity", "gross_margin": "grossMargins",
        "op_margin": "operatingMargins", "eps_growth": "earningsGrowth",
        "peg": "pegRatio", "pb": "priceToBook", "ev_ebitda": "enterpriseToEbitda",
        "beta": "beta",
    }
    out = {k: None for k in keys}
    out["earnings_date"] = None
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        for k, src in keys.items():
            out[k] = info.get(src)
        cal = tk.calendar or {}
        dates = cal.get("Earnings Date") or []
        if dates:
            out["earnings_date"] = dates[0]
    except Exception:  # noqa: BLE001
        pass
    return out


def health_score(fund: dict) -> tuple:
    """Kesehatan fundamental berbasis aturan. Bukan valuasi, bukan rekomendasi.

    Mengukur: profitabilitas, kualitas laba (FCF), leverage, likuiditas, pertumbuhan.
    Return (skor, label, catatan). Skor tinggi = neraca & profitabilitas kuat.
    """
    if not fund or fund.get("roe") is None and fund.get("op_margin") is None:
        return None, "—", []

    score, notes = 0, []

    roe = fund.get("roe")
    if roe is not None:
        if roe >= 0.20:
            score += 2
            notes.append(f"ROE {roe*100:.0f}% kuat")
        elif roe < 0:
            score -= 2
            notes.append(f"ROE {roe*100:.0f}% negatif")

    om = fund.get("op_margin")
    if om is not None:
        if om >= 0.20:
            score += 1
            notes.append(f"margin operasi {om*100:.0f}%")
        elif om < 0.05:
            score -= 1
            notes.append(f"margin operasi tipis {om*100:.0f}%")

    # FCF vs OCF — laba yang tidak jadi kas itu tanda tanya.
    fcf, ocf = fund.get("fcf"), fund.get("ocf")
    if fcf is not None and fcf < 0:
        score -= 2
        notes.append("FCF negatif")
    elif fcf and ocf and ocf > 0:
        ratio = fcf / ocf
        if ratio < 0.2:
            score -= 1
            notes.append(f"FCF cuma {ratio*100:.0f}% dari kas operasi (capex berat)")

    d2e = fund.get("d2e")
    if d2e is not None:
        if d2e > 150:
            score -= 2
            notes.append(f"utang/ekuitas {d2e:.0f}% tinggi")
        elif d2e < 50:
            score += 1
            notes.append(f"utang/ekuitas {d2e:.0f}% rendah")

    cr = fund.get("current_ratio")
    if cr is not None and cr < 1.0:
        score -= 1
        notes.append(f"current ratio {cr:.2f} (<1, likuiditas ketat)")

    eg = fund.get("eps_growth")
    if eg is not None:
        if eg >= 0.20:
            score += 1
            notes.append(f"laba tumbuh {eg*100:.0f}%")
        elif eg < 0:
            score -= 1
            notes.append(f"laba turun {eg*100:.0f}%")

    if score >= 3:
        label = "💪 Kuat"
    elif score <= -3:
        label = "⚠️ Rapuh"
    else:
        label = "😐 Campuran"
    return score, label, notes


def valuation_note(row: dict, fund: dict) -> Optional[str]:
    """Konteks valuasi. PEG & EV/EBITDA lebih jujur dari P/E telanjang."""
    if not fund:
        return None
    bits = []
    peg = fund.get("peg")
    if peg is not None and peg > 0:
        tag = "murah relatif pertumbuhan" if peg < 1 else (
            "wajar" if peg < 2 else "mahal relatif pertumbuhan")
        bits.append(f"PEG {peg:.2f} ({tag})")
    ev = fund.get("ev_ebitda")
    if ev is not None and ev > 0:
        bits.append(f"EV/EBITDA {ev:.1f}")
    pb = fund.get("pb")
    if pb is not None and pb > 0:
        bits.append(f"P/B {pb:.1f}")
    beta = fund.get("beta")
    if beta is not None:
        vol = "lebih liar dari pasar" if beta > 1.2 else (
            "lebih tenang dari pasar" if beta < 0.8 else "seiring pasar")
        bits.append(f"beta {beta:.2f} ({vol})")
    return " · ".join(bits) if bits else None


# -----------------------------------------------------------------------------
# SENTIMEN BERITA (headline saja — tanpa NLP, tanpa klaim akurasi)
# -----------------------------------------------------------------------------

# ponytail: keyword matching, bukan NLP. Cukup untuk menandai headline
# yang layak dibaca; TIDAK bisa menangkap sarkasme/konteks. Upgrade ke
# model sentimen kalau sudah terasa kurang.
_NEG_WORDS = ("plunge", "fall", "drop", "slump", "miss", "cut", "lawsuit",
              "probe", "downgrade", "warn", "loss", "decline", "sink", "weak",
              "layoff", "recall", "fraud", "sell-off", "selloff", "crash")
_POS_WORDS = ("surge", "jump", "beat", "record", "upgrade", "rally", "soar",
              "growth", "win", "expand", "raise", "strong", "profit", "gain",
              "breakthrough", "partnership", "approval")


def fetch_news(ticker: str, limit: int = 4) -> list:
    """Headline terbaru + label sentimen kasar berbasis kata kunci."""
    items = []
    try:
        for art in (yf.Ticker(ticker).news or [])[:limit]:
            c = art.get("content") or {}
            title = (c.get("title") or "").strip()
            if not title:
                continue
            low = title.lower()
            neg = sum(w in low for w in _NEG_WORDS)
            pos = sum(w in low for w in _POS_WORDS)
            tone = "🔻" if neg > pos else ("🔺" if pos > neg else "▫️")
            items.append({
                "title": title,
                "tone": tone,
                "date": (c.get("pubDate") or "")[:10],
                "publisher": ((c.get("provider") or {}).get("displayName") or ""),
            })
    except Exception:  # noqa: BLE001
        pass
    return items


def news_mood(items: list) -> str:
    """Ringkas nada headline. Sengaja kasar — ini pemicu baca, bukan kesimpulan."""
    if not items:
        return "—"
    neg = sum(i["tone"] == "🔻" for i in items)
    pos = sum(i["tone"] == "🔺" for i in items)
    if neg > pos:
        return f"cenderung negatif ({neg}/{len(items)} headline)"
    if pos > neg:
        return f"cenderung positif ({pos}/{len(items)} headline)"
    return "netral/campuran"


# -----------------------------------------------------------------------------
# MAKRO (konteks pasar, bukan sinyal)
# -----------------------------------------------------------------------------

MACRO = [
    ("^VIX", "VIX (indeks ketakutan)"),
    ("^TNX", "US 10Y yield"),
    ("DX-Y.NYB", "Dollar Index"),
    ("^GSPC", "S&P 500"),
    ("CL=F", "Minyak WTI"),
]


def fetch_macro() -> list:
    """Snapshot indikator makro. Konteks 'cuaca pasar', bukan sinyal beli/jual."""
    out = []
    for sym, name in MACRO:
        try:
            closes = yf.Ticker(sym).history(period="3mo")["Close"].tolist()
            if len(closes) < 2:
                continue
            out.append({
                "name": name, "last": round(closes[-1], 2),
                "chg_1d": pct(closes[-1], closes[-2]),
                "chg_3mo": pct(closes[-1], closes[0]),
            })
        except Exception:  # noqa: BLE001
            continue
    return out


def macro_read(macro: list) -> str:
    """Terjemahkan VIX jadi kalimat. Hanya VIX — sisanya biar kamu baca sendiri."""
    vix = next((m for m in macro if "VIX" in m["name"]), None)
    if not vix:
        return ""
    v = vix["last"]
    if v < 15:
        return "Pasar tenang (VIX rendah). Justru saat begini orang lupa risiko."
    if v < 25:
        return "Volatilitas normal."
    return "Pasar gelisah (VIX tinggi). Buat DCA, ini biasanya jendela harga bagus."


def action_items(row: dict, fund: dict) -> list:
    """CTA konkret: hal yang KAMU kendalikan, bukan prediksi harga.

    Sengaja tidak pernah bilang 'beli' atau 'jual'. Yang dikeluarkan adalah
    tugas riset & pengingat jadwal — itu yang benar-benar bisa ditindaklanjuti.
    """
    if not row["ok"]:
        return []

    todos = []
    score, _, _ = condition(row)
    dd = row["from_52w_high"]

    # Drawdown besar = wajib cek penyebabnya sebelum menambah posisi.
    if dd is not None and dd <= -20:
        todos.append(
            f"Baca alasan {row['ticker']} turun {dd}% dari high — "
            "masalah sementara atau thesis rusak?"
        )

    # Earnings dekat = volatilitas naik. Bukan alasan menunda DCA,
    # tapi alasan tidak kaget kalau harga lompat.
    ed = fund.get("earnings_date")
    if ed:
        try:
            days = (ed - datetime.date.today()).days
            if 0 <= days <= 14:
                todos.append(
                    f"Earnings {row['ticker']} dalam {days} hari ({ed}) — "
                    "harga bisa bergerak tajam, ini normal."
                )
        except Exception:  # noqa: BLE001
            pass

    # Valuasi tinggi + teknikal panas = kombinasi yang layak dicatat,
    # bukan dihindari. Tetap bukan sinyal jual.
    pe = fund.get("fwd_pe") or fund.get("pe")
    if pe and pe > 40 and score is not None and score >= 2:
        todos.append(
            f"{row['ticker']} fwd P/E {pe:.0f} + teknikal panas — "
            "catat, jangan panik. DCA tetap jalan."
        )

    # Pertumbuhan melambat = sinyal fundamental yang layak diselidiki.
    rg = fund.get("rev_growth")
    if rg is not None and rg < 0:
        todos.append(
            f"Revenue {row['ticker']} turun {rg*100:.0f}% YoY — "
            "cek apakah ini tren atau satu kuartal."
        )

    # --- lapis fundamental dalam ---
    hs, _, _ = health_score(fund)
    if hs is not None and hs <= -3:
        todos.append(
            f"Neraca {row['ticker']} rapuh (skor {hs}) — "
            "baca 10-Q bagian utang & arus kas sebelum menambah bobot."
        )

    if fund.get("fcf") is not None and fund["fcf"] < 0:
        todos.append(
            f"FCF {row['ticker']} negatif — perusahaan bakar kas. "
            "Wajar kalau lagi ekspansi, bahaya kalau berlarut."
        )

    peg = fund.get("peg")
    if peg is not None and peg > 3:
        todos.append(
            f"PEG {row['ticker']} {peg:.1f} — harga jauh di depan pertumbuhan. "
            "Pahami ekspektasi apa yang sudah dihargai pasar."
        )

    return todos


def describe(row: dict, fund: dict = None) -> str:
    """Terjemahkan angka jadi label netral (bukan sinyal beli/jual)."""
    if not row["ok"]:
        return f"⚠️ {row['ticker']}: gagal ({row['error']})"

    # Label tren berdasarkan posisi vs MA — deskriptif, bukan rekomendasi.
    trend = "—"
    if row["vs_ma50"] is not None and row["vs_ma200"] is not None:
        if row["vs_ma50"] > 0 and row["vs_ma200"] > 0:
            trend = "di atas MA50 & MA200"
        elif row["vs_ma50"] < 0 and row["vs_ma200"] < 0:
            trend = "di bawah MA50 & MA200"
        else:
            trend = "campuran (di antara MA50/MA200)"

    rsi = row["rsi"]
    rsi_note = ""
    if rsi is not None:
        if rsi >= 70:
            rsi_note = " · RSI tinggi (overbought secara teknikal)"
        elif rsi <= 30:
            rsi_note = " · RSI rendah (oversold secara teknikal)"

    arrow = "▲" if (row["chg_1d"] or 0) >= 0 else "▼"
    _, label, why = condition(row)
    why_txt = f" ({', '.join(why)})" if why else ""

    # Baris fundamental — hanya tampil kalau datanya ada.
    fund_line = ""
    if fund:
        bits = []
        pe = fund.get("fwd_pe") or fund.get("pe")
        if pe:
            tag = "fwd P/E" if fund.get("fwd_pe") else "P/E"
            bits.append(f"{tag} {pe:.1f}")
        rg = fund.get("rev_growth")
        if rg is not None:
            bits.append(f"rev {rg*100:+.0f}% YoY")
        tgt = fund.get("target")
        if tgt and row["price"]:
            upside = pct(tgt, row["price"])
            n = fund.get("analysts") or "?"
            bits.append(f"target analis ${tgt:.0f} ({upside:+}%, n={n})")
        if bits:
            fund_line = f"\n    Fundamental: {' · '.join(bits)}"

        # Kesehatan neraca & profitabilitas.
        hs, hlabel, hnotes = health_score(fund)
        if hs is not None:
            note = f" ({', '.join(hnotes)})" if hnotes else ""
            fund_line += f"\n    Kesehatan: {hlabel}{note}"

        # Valuasi relatif.
        val = valuation_note(row, fund)
        if val:
            fund_line += f"\n    Valuasi: {val}"

    # Berita terbaru + nada headline.
    news_line = ""
    news = fund.get("_news") if fund else None
    if news:
        news_line = f"\n    Berita: {news_mood(news)}"
        for n in news[:2]:
            news_line += f"\n      {n['tone']} {n['title'][:78]}"

    return (
        f"{arrow} <b>{row['ticker']}</b>  ${row['price']}  "
        f"({row['chg_1d']:+}% hari ini, {row['chg_1w']:+}% 1mgg)\n"
        f"    Tren: {trend} · RSI {rsi}{rsi_note}\n"
        f"    Dari 52w high: {row['from_52w_high']}% · dari 52w low: +{row['from_52w_low']}%\n"
        f"    Kondisi: {label}{why_txt}{fund_line}{news_line}"
    )


# -----------------------------------------------------------------------------
# LLM SUMMARY (opsional, via 9router)
# -----------------------------------------------------------------------------

def llm_summary(rows: list) -> Optional[str]:
    """Kirim data mentah ke 9router untuk dirangkum. Return None kalau gagal/disabled."""
    if not ROUTER_ENABLED:
        return None

    facts = [r for r in rows if r["ok"]]
    payload = {
        "model": ROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Kamu asisten monitoring saham. Tugasmu HANYA merangkum kondisi "
                    "teknikal secara netral dalam Bahasa Indonesia yang natural dan singkat. "
                    "JANGAN memberi rekomendasi beli/jual. JANGAN klaim sesuatu 'worth to buy'. "
                    "Ingatkan sekali di akhir bahwa ini untuk monitoring, bukan sinyal, dan "
                    "pembaca adalah investor DCA horizon panjang. Maksimal 6 kalimat."
                ),
            },
            {
                "role": "user",
                "content": "Rangkum kondisi teknikal berikut:\n" + json.dumps(facts, ensure_ascii=False),
            },
        ],
        "max_tokens": 400,
        "temperature": 0.4,
    }
    try:
        resp = requests.post(
            f"{ROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {ROUTER_API_KEY}",
                "Content-Type": "application/json",
                "prompt_budget": ROUTER_PROMPT_BUDGET,  # tier-based routing
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001
        print(f"[llm_summary] gagal, lanjut tanpa ringkasan: {e}", file=sys.stderr)
        return None


# -----------------------------------------------------------------------------
# TELEGRAM
# -----------------------------------------------------------------------------
def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] TELEGRAM_TOKEN / TELEGRAM_CHAT_ID belum di-set.", file=sys.stderr)
        print("\n--- PREVIEW DIGEST ---\n" + text)  # fallback: cetak ke stdout
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] gagal kirim: {e}", file=sys.stderr)
        return False


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def build_digest() -> str:
    global TICKERS
    TICKERS = get_watchlist()
    today = datetime.date.today().strftime("%A, %d %B %Y")
    rows = [analyze_ticker(t) for t in TICKERS]
    funds = {r["ticker"]: fetch_fundamentals(r["ticker"]) if r["ok"] else {} for r in rows}
    for r in rows:
        if r["ok"]:
            funds[r["ticker"]]["_news"] = fetch_news(r["ticker"])

    macro = fetch_macro()

    lines = [f"📊 <b>Digest Watchlist</b> — {today}", ""]

    # Makro dulu — konteks cuaca pasar sebelum lihat emiten satu-satu.
    if macro:
        lines.append("🌍 <b>Kondisi pasar:</b>")
        for m in macro:
            lines.append(
                f"    {m['name']}: {m['last']} "
                f"({m['chg_1d']:+}% 1h, {m['chg_3mo']:+}% 3bln)"
            )
        read = macro_read(macro)
        if read:
            lines.append(f"    <i>{read}</i>")
        lines.append("")

    lines += [describe(r, funds.get(r["ticker"])) for r in rows]

    # Ringkasan kondisi teknikal, diurut dari paling tertekan ke paling extended.
    scored = [(condition(r)[0], condition(r)[1], r["ticker"]) for r in rows if r["ok"]]
    tertekan = [t for s, _, t in scored if s <= -2]
    extended = [t for s, _, t in scored if s >= 2]
    if tertekan or extended:
        lines += ["", "🔍 <b>Kondisi teknikal:</b>"]
        lines.append(f"    🟢 Tertekan: {', '.join(tertekan) if tertekan else '—'}")
        lines.append(f"    🔴 Extended: {', '.join(extended) if extended else '—'}")
        lines.append(
            "    <i>Label kondisi teknikal, BUKAN 'worth to buy'. "
            "Valuasi & fundamental tidak dihitung di sini.</i>"
        )

    # Ringkasan kesehatan fundamental — terpisah dari teknikal, sengaja.
    hscored = [(health_score(funds.get(r["ticker"], {}))[0], r["ticker"])
               for r in rows if r["ok"]]
    kuat = [t for s, t in hscored if s is not None and s >= 3]
    rapuh = [t for s, t in hscored if s is not None and s <= -3]
    if kuat or rapuh:
        lines += ["", "🏦 <b>Kesehatan fundamental:</b>"]
        lines.append(f"    💪 Kuat: {', '.join(kuat) if kuat else '—'}")
        lines.append(f"    ⚠️ Rapuh: {', '.join(rapuh) if rapuh else '—'}")
        lines.append(
            "    <i>Neraca & profitabilitas, bukan harga. Perusahaan kuat "
            "bisa mahal; yang rapuh bisa murah karena alasan bagus.</i>"
        )

    # CTA — tugas riset, bukan instruksi beli/jual.
    todos = []
    for r in rows:
        todos += action_items(r, funds.get(r["ticker"], {}))
    lines += ["", "✅ <b>Yang perlu kamu lakukan:</b>"]
    if todos:
        lines += [f"    ▫️ {t}" for t in todos]
    else:
        lines.append("    ▫️ Tidak ada yang perlu ditindaklanjuti minggu ini.")
    lines.append("    ▫️ Beli sesuai jadwal DCA. Digest ini tidak mengubah tanggal/jumlah.")

    summary = llm_summary(rows)
    if summary:
        lines += ["", "📝 <b>Ringkasan:</b>", summary]

    lines += [
        "",
        "📖 <b>Cara baca:</b>",
        "    <b>1mgg</b> lebih berarti dari <b>hari ini</b> — angka harian itu noise.",
        "    <b>MA50/MA200</b> = arah tren. \"Campuran\" = lagi transisi, arah belum jelas.",
        "    <b>RSI</b> = kecepatan gerak, bukan mahal/murah. Overbought bisa lanjut naik lama.",
        "    <b>Dari 52w high</b> = konteks setahun. Turun 20% ≠ diskon 20% — "
        "bisa jadi bisnisnya memang memburuk. Cek berita emitennya.",
        "    <b>fwd P/E</b> = harga ÷ laba proyeksi. Tinggi = pasar menaruh ekspektasi besar.",
        "    <b>Target analis</b> = konsensus, sering terlalu optimis. Konteks, bukan janji.",
        "    <b>PEG</b> = P/E ÷ pertumbuhan. <1 murah relatif tumbuhnya, >2 mahal.",
        "    <b>ROE</b> = seberapa efisien modal jadi laba. >20% umumnya kuat.",
        "    <b>FCF</b> = kas sisa setelah capex. Negatif = bakar kas.",
        "    <b>Utang/ekuitas</b> >150% = leverage tinggi, rapuh saat bunga naik.",
        "    <b>Beta</b> >1 = lebih liar dari pasar. Bukan buruk, cuma perlu perut kuat.",
        "    <b>VIX</b> tinggi = pasar takut. Buat DCA itu justru jendela harga.",
        "    <b>US 10Y naik</b> = tekanan ke saham pertumbuhan (diskonto naik).",
        "    <b>Sentimen berita</b> = hitung kata kunci judul, KASAR. Pemicu baca, bukan kesimpulan.",
        "",
        "📚 <b>Sumber belajar:</b>",
        "    Investopedia — kamus istilah (RSI, P/E, PEG, ROE, FCF)",
        "    investor.vanguard.com — riset DCA & indexing",
        "    SEC EDGAR — 10-K/10-Q resmi; baca bagian MD&A dulu",
        "    Bogleheads wiki — filosofi investor jangka panjang",
        "    fred.stlouisfed.org — data makro resmi (inflasi, suku bunga, tenaga kerja)",
        "    Aswath Damodaran (NYU, gratis) — valuasi dari sumbernya",
        "",
        "<i>Untuk monitoring & belajar, bukan sinyal beli/jual. "
        "Kamu investor DCA — tetap beli terjadwal, abaikan noise harian.</i>",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    digest = build_digest()
    send_telegram(digest)

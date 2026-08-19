#!/usr/bin/env python3
"""
market_data.py
==============
Lapisan pengambilan data pasar yang tahan banting.

Kenapa ada file ini: GitHub Actions berjalan dari IP data center, dan Yahoo
Finance rutin melempar rate-limit ke sana. Jadi setiap permintaan:

  1. dicoba ulang dengan jeda bertambah (exponential backoff),
  2. kalau tetap gagal, jatuh ke Stooq (CSV gratis, tanpa API key) untuk
     data harga — cukup untuk seluruh indikator teknikal,
  3. kalau semuanya gagal, mengembalikan None supaya pemanggil bisa memakai
     data lama dan tidak menimpa dashboard dengan nilai kosong.

Fundamental hanya tersedia dari Yahoo; kalau gagal, dict kosong yang dikembalikan
dan skor fundamental jatuh ke nilai netral.
"""

import io
import time
import urllib.request

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

RETRIES = 3
BACKOFF = 2.5           # detik, dikalikan percobaan ke-n
PAUSE_BETWEEN = 0.6     # jeda sopan antar ticker


class Bars:
    """Deret OHLCV harian, urut lama -> baru."""

    __slots__ = ("highs", "lows", "closes", "volumes", "source")

    def __init__(self, highs, lows, closes, volumes, source):
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.volumes = volumes
        self.source = source

    def __len__(self):
        return len(self.closes)


# --------------------------------------------------------------------------
# YAHOO
# --------------------------------------------------------------------------

def _yahoo_bars(ticker: str, period="1y"):
    if yf is None:
        return None
    hist = yf.Ticker(ticker).history(period=period, interval="1d",
                                     auto_adjust=True)
    if hist is None or hist.empty:
        return None
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return None
    vol = hist["Volume"].fillna(0).tolist() if "Volume" in hist else []
    return Bars(
        highs=hist["High"].tolist(),
        lows=hist["Low"].tolist(),
        closes=hist["Close"].tolist(),
        volumes=vol,
        source="yahoo",
    )


# --------------------------------------------------------------------------
# STOOQ (cadangan)
# --------------------------------------------------------------------------

def _stooq_symbol(ticker: str) -> str:
    """NVDA -> nvda.us ; BRK-B -> brk-b.us ; ^GSPC -> ^spx (indeks tidak dipetakan)."""
    t = ticker.lower()
    if t.startswith("^") or "=" in t:
        return ""          # indeks & futures tidak dipetakan; biarkan gagal
    return f"{t}.us"


def _stooq_bars(ticker: str):
    sym = _stooq_symbol(ticker)
    if not sym:
        return None
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
    lines = [ln for ln in io.StringIO(raw).read().splitlines() if ln.strip()]
    if len(lines) < 2 or not lines[0].lower().startswith("date"):
        return None

    highs, lows, closes, vols = [], [], [], []
    for ln in lines[-400:]:                      # ~1.5 tahun terakhir
        parts = ln.split(",")
        if len(parts) < 5 or parts[0].lower() == "date":
            continue
        try:
            highs.append(float(parts[2]))
            lows.append(float(parts[3]))
            closes.append(float(parts[4]))
            vols.append(float(parts[5]) if len(parts) > 5 and parts[5] else 0.0)
        except ValueError:
            continue
    if len(closes) < 60:
        return None
    return Bars(highs, lows, closes, vols, "stooq")


# --------------------------------------------------------------------------
# API PUBLIK
# --------------------------------------------------------------------------

def get_bars(ticker: str, period="1y", allow_fallback=True):
    """Ambil deret harga. Return Bars atau None kalau semua sumber gagal."""
    last_err = None
    for attempt in range(RETRIES):
        try:
            bars = _yahoo_bars(ticker, period)
            if bars and len(bars) >= 60:
                return bars
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(BACKOFF * (attempt + 1))

    if allow_fallback:
        try:
            bars = _stooq_bars(ticker)
            if bars:
                return bars
        except Exception as e:  # noqa: BLE001
            last_err = last_err or e
    return None


def get_fundamentals(ticker: str) -> dict:
    """Ambil fundamental dari Yahoo. Dict kosong kalau tidak tersedia."""
    if yf is None:
        return {}
    keys = {
        "pe": "trailingPE", "fwd_pe": "forwardPE", "target": "targetMeanPrice",
        "analysts": "numberOfAnalystOpinions", "rec": "recommendationKey",
        "rev_growth": "revenueGrowth", "margin": "profitMargins",
        "roe": "returnOnEquity", "fcf": "freeCashflow", "ocf": "operatingCashflow",
        "debt": "totalDebt", "cash": "totalCash", "current_ratio": "currentRatio",
        "d2e": "debtToEquity", "gross_margin": "grossMargins",
        "op_margin": "operatingMargins", "eps_growth": "earningsGrowth",
        "peg": "pegRatio", "pb": "priceToBook", "ev_ebitda": "enterpriseToEbitda",
        "beta": "beta", "quote_type": "quoteType", "name": "shortName",
    }
    for attempt in range(RETRIES):
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            if not info:
                raise ValueError("info kosong")
            out = {k: info.get(src) for k, src in keys.items()}
            out["earnings_date"] = None
            try:
                cal = tk.calendar or {}
                dates = cal.get("Earnings Date") or []
                if dates:
                    out["earnings_date"] = dates[0]
            except Exception:  # noqa: BLE001
                pass
            return out
        except Exception:  # noqa: BLE001
            time.sleep(BACKOFF * (attempt + 1))
    return {}


def get_news(ticker: str, limit=4) -> list:
    """Headline terbaru + label nada kasar berbasis kata kunci."""
    if yf is None:
        return []
    neg_words = ("plunge", "fall", "drop", "slump", "miss", "cut", "lawsuit",
                 "probe", "recall", "downgrade", "warn", "loss", "weak",
                 "delay", "halt", "sell-off", "selloff", "bear")
    pos_words = ("surge", "jump", "beat", "record", "upgrade", "rally", "soar",
                 "growth", "profit", "expand", "win", "strong", "launch",
                 "partnership", "buyback", "bull")
    items = []
    try:
        for art in (yf.Ticker(ticker).news or [])[:limit]:
            c = art.get("content") or art
            title = (c.get("title") or "").strip()
            if not title:
                continue
            low = title.lower()
            neg = sum(w in low for w in neg_words)
            pos = sum(w in low for w in pos_words)
            items.append({
                "title": title,
                "tone": "🔻" if neg > pos else ("🔺" if pos > neg else "▫️"),
                "date": str(c.get("pubDate") or "")[:10],
                "publisher": ((c.get("provider") or {}).get("displayName") or ""),
            })
    except Exception:  # noqa: BLE001
        pass
    return items

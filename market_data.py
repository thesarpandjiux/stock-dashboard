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
import math
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

RETRIES = 3
BACKOFF = 2.5           # detik, dikalikan percobaan ke-n
PAUSE_BETWEEN = 0.6     # jeda sopan antar ticker

# 1-3 day swing mode. Sesinya dianggap "lengkap" bila sekarang sudah lewat
# 15:50 ET (data harian tersedia) pada hari bursa; sebaliknya kemarin.
# ponytail: weekday-only calendar; real exchange holidays shift the true
# last-completed session. Replace with a holiday calendar before production.
ET = timezone.utc  # fallback bila tzdata absen; _now_et memperbaiki di bawah
try:
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 -- pragma: no cover
    pass
SESSION_CLOSE = (15, 50)


def _now_et():
    """Current time in America/New_York, timezone-aware.

    ponytail: weekday-only mapping, no holiday calendar; flags full-session
    absence for freshness gating until a real exchange calendar lands.
    """
    return datetime.now(ET)


class Bars:
    """Deret OHLCV harian/intraday, urut lama -> baru.

    Asumsi tambahan untuk mode 1-3 hari: bar terakhir adalah sesi penuh yang
    sudah selesai, dan `asofs`/`opens` len-nya sama dengan bar lain. Bar
    intraday yang belum selesai TIDAK boleh masuk; caller menyaringnya dulu.
    """

    __slots__ = ("opens", "highs", "lows", "closes", "volumes", "asofs",
                 "interval", "source")

    def __init__(self, highs, lows, closes, volumes, source,
                 opens=None, asofs=None, interval="1d"):
        self.opens = list(opens) if opens else []
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.volumes = volumes
        self.asofs = list(asofs) if asofs else []
        self.interval = interval
        self.source = source

    def __len__(self):
        return len(self.closes)


# --------------------------------------------------------------------------
# YAHOO
# --------------------------------------------------------------------------

def _yahoo_bars(ticker: str, period="1y", interval="1d"):
    if yf is None:
        return None
    hist = yf.Ticker(ticker).history(period=period, interval=interval,
                                     auto_adjust=True)
    if hist is None or hist.empty:
        return None
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return None
    opens = hist["Open"].tolist() if "Open" in hist else []
    vol = hist["Volume"].fillna(0).tolist() if "Volume" in hist else []
    asofs = [ts.isoformat() for ts in hist.index]
    return Bars(
        opens=opens,
        highs=hist["High"].tolist(),
        lows=hist["Low"].tolist(),
        closes=hist["Close"].tolist(),
        volumes=vol,
        asofs=asofs,
        interval=interval,
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


# --------------------------------------------------------------------------
# MODE 1-3 HARI: data bertimestamp + freshness fail-closed
# --------------------------------------------------------------------------

def get_daily_bars(ticker: str, period: str = "1y") -> "Bars | None":
    """Bars harian bertimestamp (interval "1d"), tanpa fallback Stooq."""
    for attempt in range(RETRIES):
        try:
            bars = _yahoo_bars(ticker, period, "1d")
            if bars:
                return bars
        except Exception:  # noqa: BLE001
            pass
        time.sleep(BACKOFF * (attempt + 1))
    return None


def get_h1_bars(ticker: str, period: str = "60d") -> "Bars | None":
    """Bars H1 (interval "60m", 09:30-10:30 ET) untuk hari yang lalu.

    Batasi sampai bar 10:30 kemarin: bar pertama hari ini belum selesai,
    bar selanjutnya bukan bagian sesi pertama. Tidak pernah fallback ke
    data harian; kegagalan apa pun -> None.
    """
    bars = None
    for attempt in range(RETRIES):
        try:
            bars = _yahoo_bars(ticker, period, "60m")
            if bars:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(BACKOFF * (attempt + 1))
    if bars is None or len(bars) < 2 or len(bars.asofs) != len(bars.closes):
        return None
    now = _now_et()
    cut = []
    for i, stamp in enumerate(bars.asofs):
        parsed = datetime.fromisoformat(stamp)
        et = parsed.astimezone(ET)
        if et.weekday() < 5 and et.hour == 9 and et.minute == 30:
            cut.append(i)          # bar 09:30 ET = jam pertama sesi
    while cut:                     # buang bar 09:30 yang sesinya belum tutup
        ts = datetime.fromisoformat(bars.asofs[cut[-1]])
        if now < datetime(ts.year, ts.month, ts.day, *SESSION_CLOSE, tzinfo=ET):
            cut.pop()
        else:
            break
    if not cut:
        return None               # bar pertama hari ini = belum selesai
    return Bars(
        opens=[bars.opens[i] for i in cut],
        highs=[bars.highs[i] for i in cut],
        lows=[bars.lows[i] for i in cut],
        closes=[bars.closes[i] for i in cut],
        volumes=[bars.volumes[i] for i in cut],
        asofs=[bars.asofs[i] for i in cut],
        interval="60m",
        source=bars.source,
    )


def is_session_complete(when):
    """True bila `when` jatuh setelah close 15:50 ET pada hari bursa.

    ponytail: weekday-only; real exchange holidays misclassified as
    complete sessions. Swap in a holiday calendar before production.
    """
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return False
    try:
        when = when.astimezone(ET)
    except (AttributeError, ValueError, OverflowError):
        return False
    if when.weekday() >= 5:
        return False
    return (when.hour, when.minute) >= SESSION_CLOSE


def _coerce_ts(value):
    """ISO string/aware datetime/date -> aware datetime UTC; else None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def is_fresh(last_asof, max_age, now=None):
    """True bila data terakhir berumur <= max_age dari `now` (default UTC).

    max_age: timedelta atau detik (float). last_asof/now: ISO string,
    datetime aware/naif (naif dianggap UTC), atau date. Apa pun yang
    tidak bisa diparse -> False (fail-closed).
    """
    last = _coerce_ts(last_asof)
    if last is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now = _coerce_ts(now)
    try:
        age = now - last
    except (TypeError, OverflowError):
        return False
    if isinstance(max_age, timedelta):
        return timedelta(0) <= age <= max_age
    try:
        max_seconds = float(max_age)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(max_seconds) or max_seconds < 0:
        return False
    return timedelta(0) <= age <= timedelta(seconds=max_seconds)


def normalize_earnings_status(raw_date, source):
    """Normalisasi tanggal earnings jadi kontrak {verified, date, source}.

    Hanya tanggal kalender yang bisa diverifikasi; nilai None/naif/bukan
    tanggal selalu menghasilkan verified=False (fail-closed).
    """
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        return {
            "verified": True,
            "date": raw_date.isoformat(),
            "source": source,
        }
    if isinstance(raw_date, str):
        try:
            parsed = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            parsed = None
        if parsed is not None:
            return {"verified": True, "date": parsed.isoformat(), "source": source}
    return {"verified": False, "date": None, "source": None}


def get_earnings_status(ticker: str) -> dict:
    """Tanggal earnings terverifikasi dari kalender Yahoo.

    Setiap kegagalan fetch/parse -> {"verified": False, ...}; tidak ada
    fallback diam-diam ke sumber lain.
    """
    if yf is None:
        return {"verified": False, "date": None, "source": None}
    for attempt in range(RETRIES):
        try:
            cal = yf.Ticker(ticker).calendar or {}
            dates = cal.get("Earnings Date") or []
            for raw in dates:
                parsed = _coerce_ts(raw)
                if parsed is not None:
                    return {
                        "verified": True,
                        "date": parsed.date().isoformat(),
                        "source": "yahoo-calendar",
                    }
            return {"verified": False, "date": None, "source": None}
        except Exception:  # noqa: BLE001
            time.sleep(BACKOFF * (attempt + 1))
    return {"verified": False, "date": None, "source": None}

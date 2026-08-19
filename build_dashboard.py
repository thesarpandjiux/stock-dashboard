#!/usr/bin/env python3
"""
build_dashboard.py
==================
Menghasilkan `data.json` yang dibaca `index.html`.

Dijalankan oleh GitHub Actions:
  * tiap 5 hari  -> refresh penuh + rotasi kandidat swing
  * saat watchlist berubah (tambah/hapus emiten) -> refresh cepat

Prinsip penting: **merge-safe**. Kalau Yahoo/Stooq gagal untuk sebagian ticker,
entri lama dari `data.json` sebelumnya dipertahankan dan ditandai basi, bukan
ditimpa nilai kosong. Dashboard yang menampilkan data kemarin jauh lebih berguna
daripada dashboard yang kosong.

Pemakaian:
    python build_dashboard.py                # refresh semua ticker
    python build_dashboard.py --only NVDA    # refresh satu ticker saja (cepat)
"""

import argparse
import datetime
import json
import os
import sys
import time

import market_data as md
import swing
import watchlist as wl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE_DIR, "data.json")

# Data lebih tua dari ini ditandai basi di UI.
STALE_AFTER_HOURS = 30

MACRO = [
    ("^VIX", "VIX (indeks ketakutan)"),
    ("^TNX", "US 10Y yield"),
    ("DX-Y.NYB", "Dollar Index"),
    ("^GSPC", "S&P 500"),
    ("CL=F", "Minyak WTI"),
]

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
             "Agustus", "September", "Oktober", "November", "Desember"]


# --------------------------------------------------------------------------
# UTIL
# --------------------------------------------------------------------------

def wib_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=7)


def wib_stamp(dt=None):
    dt = dt or wib_now()
    return f"{dt.day} {MONTHS_ID[dt.month - 1]} {dt.year}, {dt:%H:%M} WIB"


def num(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def pct(a, b):
    a, b = num(a), num(b)
    if a is None or not b:
        return None
    return round((a - b) / b * 100, 2)


def load_previous():
    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def days_until(earnings_date):
    if not earnings_date:
        return None
    try:
        if isinstance(earnings_date, str):
            d = datetime.date.fromisoformat(earnings_date[:10])
        elif isinstance(earnings_date, datetime.datetime):
            d = earnings_date.date()
        elif isinstance(earnings_date, datetime.date):
            d = earnings_date
        else:
            return None
    except (ValueError, TypeError):
        return None
    delta = (d - datetime.date.today()).days
    return delta if delta >= 0 else None


# --------------------------------------------------------------------------
# SATU TICKER
# --------------------------------------------------------------------------

def build_item(ticker: str, source: str) -> dict:
    """Analisa lengkap satu emiten. Return dict; `ok=False` kalau data gagal."""
    item = {"ticker": ticker, "source": source, "ok": False, "error": None}

    bars = md.get_bars(ticker)
    if not bars or len(bars) < 60:
        item["error"] = "data harga tidak tersedia"
        return item

    closes = bars.closes
    tech = swing.technicals(bars.highs, bars.lows, closes, bars.volumes)
    fund = md.get_fundamentals(ticker) or {}

    quote_type = (fund.get("quote_type") or "").upper()
    is_etf = quote_type in ("ETF", "MUTUALFUND", "INDEX")

    year_high, year_low = max(closes), min(closes)
    dte = days_until(fund.get("earnings_date"))
    sw = swing.evaluate(tech, fund, is_etf=is_etf, days_to_earnings=dte)

    price = tech["price"]
    target = num(fund.get("target"))

    item.update({
        "ok": True,
        "name": fund.get("name") or ticker,
        "price": price,
        "spark": [round(c, 2) for c in closes[-63:]],
        "chg_1d": tech["chg_1d"],
        "chg_1w": tech["chg_1w"],
        "chg_1m": tech["chg_1m"],
        "chg_3m": tech["chg_3m"],
        "rsi": tech["rsi"],
        "vs_ma20": tech["vs_ma20"],
        "vs_ma50": tech["vs_ma50"],
        "vs_ma200": tech["vs_ma200"],
        "from_52w_high": pct(price, year_high),
        "from_52w_low": pct(price, year_low),
        "atr_pct": tech["atr_pct"],
        "dollar_volume": round(tech["dollar_volume"]) if tech["dollar_volume"] else None,
        "is_etf": is_etf,
        # fundamental mentah — tetap dipakai kartu "Additional information"
        "fwd_pe": num(fund.get("fwd_pe")),
        "peg": num(fund.get("peg")),
        "roe": num(fund.get("roe")),
        "beta": num(fund.get("beta")),
        "d2e": num(fund.get("d2e")),
        "fcf": num(fund.get("fcf")),
        "rev_growth": num(fund.get("rev_growth")),
        "op_margin": num(fund.get("op_margin")),
        "target": target,
        "upside": pct(target, price) if target and price else None,
        "analysts": fund.get("analysts"),
        "earnings_in_days": dte,
        # hasil penilaian swing
        "swing": sw,
        "data_source": bars.source,
        "fetched_iso": datetime.datetime.utcnow().isoformat(timespec="seconds"),
    })

    news = md.get_news(ticker)
    item["news"] = news
    if news:
        neg = sum(n["tone"] == "🔻" for n in news)
        pos = sum(n["tone"] == "🔺" for n in news)
        item["news_mood"] = (f"cenderung negatif ({neg}/{len(news)} headline)" if neg > pos
                             else f"cenderung positif ({pos}/{len(news)} headline)" if pos > neg
                             else "netral/campuran")
    else:
        item["news_mood"] = "—"

    item["valuation"] = valuation_note(item)
    item["todos"] = todos_for(item)
    item["has_todo"] = bool(item["todos"])
    return item


def valuation_note(it: dict):
    bits = []
    peg = num(it.get("peg"))
    if peg and peg > 0:
        tag = ("murah relatif pertumbuhan" if peg < 1
               else "wajar" if peg < 2 else "mahal relatif pertumbuhan")
        bits.append(f"PEG {peg:.2f} ({tag})")
    fwd = num(it.get("fwd_pe"))
    if fwd and fwd > 0:
        bits.append(f"fwd P/E {fwd:.1f}")
    beta = num(it.get("beta"))
    if beta is not None:
        vol = ("lebih liar dari pasar" if beta > 1.2
               else "lebih tenang dari pasar" if beta < 0.8 else "seiring pasar")
        bits.append(f"beta {beta:.2f} ({vol})")
    atr = num(it.get("atr_pct"))
    if atr:
        bits.append(f"ATR {atr:.1f}%/hari")
    return " · ".join(bits) if bits else None


def todos_for(it: dict) -> list:
    """Tugas riset konkret — bukan perintah beli/jual."""
    out = []
    t = it["ticker"]
    dte = it.get("earnings_in_days")
    if dte is not None and dte <= 10:
        out.append(f"Earnings {t} dalam {dte} hari — pertimbangkan menunda entry atau perkecil posisi.")
    dd = num(it.get("from_52w_high"))
    if dd is not None and dd <= -20:
        out.append(f"Cek penyebab {t} turun {dd:.1f}% dari puncak 52 minggu — sementara atau tesis rusak?")
    sw = it.get("swing") or {}
    if sw.get("verdict") == "BUY":
        out.append(f"{t} lolos syarat swing — tentukan ukuran posisi dari jarak stop {sw.get('risk_pct')}%.")
    if num(it.get("fcf")) is not None and num(it.get("fcf")) < 0:
        out.append(f"FCF {t} negatif — perusahaan bakar kas. Wajar saat ekspansi, bahaya kalau berlarut.")
    return out


# --------------------------------------------------------------------------
# MAKRO & RINGKASAN
# --------------------------------------------------------------------------

def build_macro() -> list:
    out = []
    for sym, name in MACRO:
        bars = md.get_bars(sym, period="3mo", allow_fallback=False)
        if not bars or len(bars) < 2:
            continue
        c = bars.closes
        out.append({
            "name": name,
            "last": round(c[-1], 2),
            "chg_1d": pct(c[-1], c[-2]),
            "chg_3mo": pct(c[-1], c[0]),
        })
    return out


def macro_read(macro: list) -> str:
    vix = next((m for m in macro if "VIX" in m["name"]), None)
    if not vix:
        return ""
    v = vix["last"]
    if v < 15:
        return "Pasar tenang (VIX rendah). Justru saat begini orang lupa risiko."
    if v < 25:
        return "Volatilitas normal — kondisi wajar untuk swing trading."
    return "Pasar gelisah (VIX tinggi). Perkecil ukuran posisi dan perlebar stop."


def build_insights(items: list, macro: list) -> list:
    live = [i for i in items if i.get("ok")]
    out = []

    vix = next((m for m in macro if "VIX" in m["name"]), None)
    if vix:
        v = vix["last"]
        out.append({
            "k": "Risiko pasar",
            "v": "Rendah" if v < 15 else "Sedang" if v < 25 else "Tinggi",
            "cls": "green" if v < 15 else "grey" if v < 25 else "amber",
            "why": f"VIX {v} — {macro_read(macro)}",
        })

    buys = [i for i in live if (i.get("swing") or {}).get("verdict") == "BUY"]
    out.append({
        "k": "Setup siap",
        "v": f"{len(buys)} emiten" if buys else "Tidak ada",
        "cls": "green" if buys else "grey",
        "why": (", ".join(i["ticker"] for i in buys[:4]) if buys
                else "Tidak ada yang lolos seluruh syarat swing. Menunggu adalah posisi."),
    })

    ranked = sorted(live, key=lambda i: (i.get("swing") or {}).get("score") or 0,
                    reverse=True)
    if ranked:
        top = ranked[0]
        out.append({
            "k": "Skor tertinggi",
            "v": top["ticker"],
            "cls": "green",
            "why": f"Skor swing {(top.get('swing') or {}).get('score')}/100 · "
                   f"{(top.get('swing') or {}).get('headline')}",
        })
        worst = ranked[-1]
        out.append({
            "k": "Paling lemah",
            "v": worst["ticker"],
            "cls": "amber",
            "why": f"Skor swing {(worst.get('swing') or {}).get('score')}/100 · "
                   f"{(worst.get('swing') or {}).get('headline')}",
        })
    return out


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="hanya refresh ticker ini (sisanya pakai data lama)")
    ap.add_argument("--skip-macro", action="store_true")
    args = ap.parse_args()

    prev = load_previous()
    prev_items = {i["ticker"]: i for i in prev.get("items", []) if i.get("ticker")}

    tickers = wl.resolve()
    if not tickers:
        print("Watchlist kosong — tidak ada yang dianalisa.", file=sys.stderr)
        tickers = []

    targets = [args.only.strip().upper()] if args.only else tickers
    print(f"Watchlist ({len(tickers)}): {', '.join(tickers) or '—'}")
    print(f"Akan di-refresh: {', '.join(targets) or '—'}")

    items, failures = [], []
    for t in tickers:
        source = wl.source_of(t)
        if t not in targets and t in prev_items:
            stale = dict(prev_items[t])
            stale["source"] = source
            items.append(stale)
            continue

        print(f"  → {t} ...", flush=True)
        item = build_item(t, source)
        if not item.get("ok"):
            failures.append(t)
            if t in prev_items:
                # Pertahankan data lama, tandai gagal-refresh.
                kept = dict(prev_items[t])
                kept["source"] = source
                kept["refresh_failed"] = True
                kept["error"] = item.get("error")
                items.append(kept)
                print(f"    gagal ({item.get('error')}) — pakai data sebelumnya")
                continue
            print(f"    gagal ({item.get('error')}) — tidak ada data lama")
            items.append(item)
            continue
        item["refresh_failed"] = False
        items.append(item)
        time.sleep(md.PAUSE_BETWEEN)

    macro = prev.get("macro", []) if args.skip_macro else build_macro()
    if not macro:
        macro = prev.get("macro", [])

    now = wib_now()
    auto = wl.read_auto()
    todos = []
    for i in items:
        todos.extend(i.get("todos") or [])

    payload = {
        "updated": wib_stamp(now),
        "updated_iso": datetime.datetime.utcnow().isoformat(timespec="seconds"),
        "stale_after_hours": STALE_AFTER_HOURS,
        "macro": macro,
        "macro_read": macro_read(macro),
        "insights": build_insights(items, macro),
        "items": items,
        "todos": todos[:12],
        "watchlist": {
            "pinned": wl.read_pinned(),
            "auto": [e.get("ticker") for e in auto.get("tickers", [])],
            "auto_detail": auto.get("tickers", []),
            "auto_updated": auto.get("updated"),
            "max_pinned": wl.MAX_PINNED,
            "max_auto": wl.MAX_AUTO,
        },
        "failures": failures,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")

    ok = sum(1 for i in items if i.get("ok"))
    print(f"\ndata.json ditulis — {ok}/{len(items)} emiten segar"
          f"{', gagal: ' + ', '.join(failures) if failures else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

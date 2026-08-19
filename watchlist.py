#!/usr/bin/env python3
"""
watchlist.py
============
Satu-satunya sumber kebenaran untuk isi watchlist.

Tiga lapis:
  1. `watchlist_core.txt`   — emiten yang KAMU pilih sendiri (pinned).
                              File ini yang diedit tombol "Tambah/Hapus" di dashboard.
  2. `watchlist_auto.json`  — kandidat swing hasil screening otomatis tiap 5 hari.
                              Dirotasi mesin; tidak pernah menyentuh daftar pinned.
  3. `watchlist_exclude.txt`— daftar cekal. Selalu menang atas keduanya.

Semua fungsi tulis menjaga komentar & urutan file supaya tetap enak dibaca manusia.
"""

import json
import os
import re
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CORE_FILE = os.path.join(BASE_DIR, "watchlist_core.txt")
AUTO_FILE = os.path.join(BASE_DIR, "watchlist_auto.json")
EXCLUDE_FILE = os.path.join(BASE_DIR, "watchlist_exclude.txt")

MAX_PINNED = 30
MAX_AUTO = 6
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z]{1,2})?$")


def normalize(ticker: str) -> str:
    """Rapikan input user jadi bentuk ticker Yahoo Finance."""
    return (ticker or "").strip().upper().replace(" ", "")


def is_valid_ticker(ticker: str) -> bool:
    return bool(TICKER_RE.match(normalize(ticker)))


# --------------------------------------------------------------------------
# BACA
# --------------------------------------------------------------------------

def _read_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _tickers_from(path):
    out = []
    for line in _read_lines(path):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        t = normalize(s)
        if t and t not in out:
            out.append(t)
    return out


def read_pinned() -> list:
    return _tickers_from(CORE_FILE)


def read_excluded() -> list:
    return _tickers_from(EXCLUDE_FILE)


def read_auto() -> dict:
    try:
        with open(AUTO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {"updated": None, "tickers": []}
    if not isinstance(data, dict):
        return {"updated": None, "tickers": []}
    data.setdefault("tickers", [])
    data.setdefault("updated", None)
    return data


def auto_tickers() -> list:
    return [e["ticker"] for e in read_auto()["tickers"] if e.get("ticker")]


def resolve(limit: int = None) -> list:
    """Daftar final yang dianalisa: pinned dulu, lalu kandidat auto, minus cekal."""
    excluded = set(read_excluded())
    out = []
    for t in read_pinned():
        if t not in excluded and t not in out:
            out.append(t)
    for t in auto_tickers():
        if t not in excluded and t not in out:
            out.append(t)
    return out[:limit] if limit else out


def source_of(ticker: str) -> str:
    t = normalize(ticker)
    if t in read_pinned():
        return "pinned"
    if t in auto_tickers():
        return "auto"
    return "unknown"


# --------------------------------------------------------------------------
# TULIS
# --------------------------------------------------------------------------

def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


def add_pinned(ticker: str) -> tuple:
    """Tambahkan ticker ke daftar pinned. Return (ok, pesan)."""
    t = normalize(ticker)
    if not is_valid_ticker(t):
        return False, f"'{ticker}' bukan format ticker yang valid."
    if t in read_excluded():
        return False, f"{t} ada di daftar cekal (watchlist_exclude.txt)."
    if t in read_pinned():
        return False, f"{t} sudah ada di watchlist."
    if len(read_pinned()) >= MAX_PINNED:
        return False, f"Watchlist penuh (maks {MAX_PINNED} emiten). Hapus satu dulu."

    lines = _read_lines(CORE_FILE) or [
        "# Daftar emiten inti yang selalu ingin kamu pantau.",
        "# Satu ticker per baris.",
    ]
    lines.append(t)
    _write_lines(CORE_FILE, lines)

    # Kalau ticker ini sebelumnya kandidat auto, promosikan (buang dari auto).
    auto = read_auto()
    kept = [e for e in auto["tickers"] if normalize(e.get("ticker", "")) != t]
    if len(kept) != len(auto["tickers"]):
        auto["tickers"] = kept
        write_auto(auto["tickers"], auto.get("updated"))

    return True, f"{t} ditambahkan ke watchlist."


def remove_pinned(ticker: str) -> tuple:
    """Hapus ticker dari daftar pinned (dan dari kandidat auto). Return (ok, pesan)."""
    t = normalize(ticker)
    pinned = read_pinned()
    auto = read_auto()
    in_auto = t in [normalize(e.get("ticker", "")) for e in auto["tickers"]]

    if t not in pinned and not in_auto:
        return False, f"{t} tidak ada di watchlist."

    if t in pinned:
        lines = _read_lines(CORE_FILE)
        kept = [ln for ln in lines
                if ln.strip().startswith("#") or not ln.strip()
                or normalize(ln) != t]
        _write_lines(CORE_FILE, kept)

    if in_auto:
        # Buang dari kandidat auto DAN cekal, supaya screener tidak
        # memasukkannya lagi 5 hari kemudian.
        auto["tickers"] = [e for e in auto["tickers"]
                           if normalize(e.get("ticker", "")) != t]
        write_auto(auto["tickers"], auto.get("updated"))
        exclude_ticker(t)

    return True, f"{t} dihapus dari watchlist."


def exclude_ticker(ticker: str) -> None:
    """Tambahkan ke daftar cekal agar screener tidak memilihnya lagi."""
    t = normalize(ticker)
    if t in read_excluded():
        return
    lines = _read_lines(EXCLUDE_FILE) or [
        "# Daftar emiten yang tidak akan pernah ditampilkan, walau aktif.",
        "# Satu ticker per baris.",
    ]
    lines.append(t)
    _write_lines(EXCLUDE_FILE, lines)


def write_auto(entries: list, updated: str = None) -> None:
    """Simpan kandidat hasil screening. `entries` = list dict {ticker, score, ...}."""
    payload = {
        "updated": updated or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickers": entries[:MAX_AUTO],
    }
    with open(AUTO_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")


# --------------------------------------------------------------------------
# CLI — dipakai oleh GitHub Actions
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: watchlist.py <list|add|remove> [TICKER]")
        raise SystemExit(2)

    cmd = sys.argv[1].lower()
    if cmd == "list":
        for t in resolve():
            print(f"{t}\t{source_of(t)}")
    elif cmd in ("add", "remove"):
        if len(sys.argv) < 3:
            print("ticker wajib diisi")
            raise SystemExit(2)
        fn = add_pinned if cmd == "add" else remove_pinned
        ok, msg = fn(sys.argv[2])
        print(msg)
        raise SystemExit(0 if ok else 1)
    else:
        print(f"perintah tidak dikenal: {cmd}")
        raise SystemExit(2)

#!/usr/bin/env python3
"""
manage.py
=========
Satu perintah untuk seluruh alur "tambah/hapus emiten" yang dipakai
GitHub Actions:

    python manage.py add NVDA
    python manage.py remove NVDA

Untuk `add`, prosesnya:
  1. validasi format ticker,
  2. tambahkan ke watchlist,
  3. langsung analisa emiten itu dan tulis ulang data.json,
  4. kalau datanya tidak ada (ticker salah/delisted), batalkan penambahan
     supaya watchlist tidak terisi entri mati.

Skrip menulis ringkasan hasil ke stdout dan — kalau berjalan di GitHub Actions —
ke `$GITHUB_OUTPUT` sebagai `message`, `status`, dan `verdict`.
"""

import os
import subprocess
import sys

import watchlist as wl


def emit(status: str, message: str, verdict: str = ""):
    print(message)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"status={status}\n")
            f.write(f"verdict={verdict}\n")
            f.write("message<<__EOF__\n")
            f.write(message + "\n")
            f.write("__EOF__\n")
    return 0 if status == "ok" else 1


def rebuild(only=None) -> int:
    cmd = [sys.executable, "build_dashboard.py"]
    if only:
        cmd += ["--only", only]
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))
    return proc.returncode


def read_item(ticker):
    import json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return next((i for i in data.get("items", [])
                 if i.get("ticker") == ticker), None)


def do_add(raw: str) -> int:
    t = wl.normalize(raw)
    ok, msg = wl.add_pinned(t)
    if not ok:
        return emit("error", f"❌ {msg}")

    if rebuild(only=t) != 0:
        wl.remove_pinned(t)
        return emit("error", f"❌ Gagal menganalisa {t} — penambahan dibatalkan.")

    item = read_item(t)
    if not item or not item.get("ok"):
        wl.remove_pinned(t)
        rebuild()
        reason = (item or {}).get("error") or "data tidak ditemukan"
        return emit("error",
                    f"❌ {t} tidak bisa dianalisa ({reason}). "
                    "Pastikan kode emitennya benar sesuai Yahoo Finance "
                    "(contoh: NVDA, BRK-B, ASML).")

    sw = item.get("swing") or {}
    lines = [
        f"✅ **{t}** ({item.get('name')}) ditambahkan ke watchlist.",
        "",
        f"**Status swing: {sw.get('verdict')}** — skor {sw.get('score')}/100",
        f"> {sw.get('headline')}",
        "",
        f"- Harga: ${item.get('price')}",
        f"- Teknikal {sw.get('tech_score')}/100 · Fundamental {sw.get('fund_score')}/100",
        f"- Entry {sw.get('entry')} · Stop {sw.get('stop')} · Target {sw.get('target')}"
        f" · R/R {sw.get('rr')}:1",
        "",
        "**Mendukung:**",
    ]
    lines += [f"- {r}" for r in (sw.get("reasons") or [])]
    lines += ["", "**Risiko:**"]
    lines += [f"- {r}" for r in (sw.get("risks") or [])]
    lines += ["", "_Bukan nasihat keuangan. Selalu pakai stop loss._"]
    return emit("ok", "\n".join(lines), sw.get("verdict", ""))


def do_remove(raw: str) -> int:
    t = wl.normalize(raw)
    ok, msg = wl.remove_pinned(t)
    if not ok:
        return emit("error", f"❌ {msg}")
    rebuild()
    return emit("ok", f"🗑️ **{t}** dihapus dari watchlist.")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: manage.py <add|remove> TICKER", file=sys.stderr)
        return 2
    action = sys.argv[1].lower()
    ticker = sys.argv[2]
    if not wl.is_valid_ticker(ticker):
        return emit("error", f"❌ '{ticker}' bukan format ticker yang valid.")
    if action == "add":
        return do_add(ticker)
    if action == "remove":
        return do_remove(ticker)
    return emit("error", f"❌ Perintah tidak dikenal: {action}")


if __name__ == "__main__":
    raise SystemExit(main())

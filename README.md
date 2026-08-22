# Stock dashboard — swing cockpit

Dashboard statis untuk menyaring saham AS jadi **BUY / WAIT / AVOID** dengan
horizon swing 1–4 minggu. Halaman dilayani GitHub Pages, seluruh analisa
dikerjakan GitHub Actions, dan tidak ada server yang perlu kamu urus.

👉 <https://thesarpandjiux.github.io/stock-dashboard/>

---

## Cara kerjanya

```
watchlist_core.txt  ──┐
watchlist_auto.json ──┼──►  build_dashboard.py  ──►  data.json  ──►  index.html
watchlist_exclude.txt ┘         (GitHub Actions)                    (GitHub Pages)
```

| Berkas | Isi |
|---|---|
| `watchlist_core.txt` | Emiten yang **kamu** pilih. Diedit tombol Tambah/Hapus di dashboard. |
| `watchlist_auto.json` | Kandidat swing hasil screening otomatis tiap 5 hari. |
| `watchlist_exclude.txt` | Daftar cekal. Selalu menang atas dua file di atas. |
| `swing.py` | Mesin penilaian: skor 0–100, verdict, level entry/stop/target. |
| `build_dashboard.py` | Menghasilkan `data.json`. Aman kalau sebagian data gagal ditarik. |
| `screener.py` | Mencari kandidat baru dari ~150 emiten paling likuid. |
| `market_data.py` | Pengambilan data + retry + fallback Stooq saat Yahoo menolak. |
| `manage.py` | Alur tambah/hapus yang dipakai GitHub Actions. |
| `stock_digest.py` | Digest Telegram (terpisah, untuk investor DCA jangka panjang). |

## Menambah & menghapus emiten

Klik **＋ Tambah emiten** di panel Watchlist, ketik kodenya (sesuai Yahoo
Finance, misal `NVDA`, `BRK-B`, `ASML`), lalu tunggu 1–3 menit. Analisa jalan
otomatis dan hasilnya muncul di halaman. Tombol `×` di tiap baris menghapus
emiten.

Karena halaman ini statis, perubahan dikirim ke GitHub lewat salah satu dari dua
jalur — keduanya gratis:

**1. Lewat issue (default, tanpa token).**
Tombol membuka issue GitHub berjudul `add: NVDA`. Kamu klik *Create*, workflow
`watchlist-issue.yml` memproses, membalas dengan hasil analisanya, lalu menutup
issue. Tidak ada rahasia apa pun yang tersimpan di browser.

**2. Lewat token (1 klik, opsional).**
Buka ⚙ di panel Watchlist dan tempel *fine-grained token*:

* GitHub → Settings → Developer settings → Fine-grained tokens
* Repository access: **hanya** `stock-dashboard`
* Permissions: **Contents → Read and write**

Token disimpan di `localStorage` browser itu saja dan hanya dikirim ke
`api.github.com`. Token tidak pernah masuk ke repo. Siapa pun yang memakai
browser itu bisa membacanya, jadi jangan pakai di perangkat bersama. Kalau
ragu, tetap pakai mode issue.

## Dua jadwal otomatis

| Workflow | Kapan | Yang dikerjakan |
|---|---|---|
| **Refresh harga harian** | Senin–Jumat, 22:00 UTC (05:00 WIB) | Menghitung ulang harga, indikator, dan verdict BUY/WAIT/AVOID untuk emiten yang sudah ada di watchlist. |
| **Cari emiten baru (5 hari)** | Tanggal 1, 6, 11, 16, 21, 26, 31 — 23:00 UTC (06:00 WIB) | Memindai ~150 emiten paling likuid, memberi skor swing, mengisi slot kandidat otomatis, lalu menghitung ulang semuanya. |

Keduanya berjalan setelah bursa AS tutup, dan berbagi satu antrean
(`concurrency: dashboard-data`) supaya tidak pernah menulis `data.json`
bersamaan.

Emiten pinned **tidak pernah** dikeluarkan mesin — hanya slot auto (maks. 6)
yang dirotasi. Menghapus kandidat auto lewat dashboard sekaligus mencekalnya
supaya tidak muncul lagi di siklus berikutnya.

Mau menjalankan sekarang juga? Actions → pilih workflow-nya → *Run workflow*.

### Kenapa `data.json` tidak boleh punya dua penulis

`data.json` dihasilkan mesin. Kalau ada proses lain (mis. cron di laptop) ikut
menulis dan mem-push berkas yang sama, git bisa menyisipkan penanda konflik
(`<<<<<<<`) ke dalamnya — dan berkas itu berhenti jadi JSON yang sah, sehingga
dashboard mati total. Tiga lapis pengaman sekarang mencegahnya:

1. `.gitattributes` menandai `data.json` dan `watchlist_auto.json` dengan
   `-merge`, jadi git tidak pernah menyisipkan penanda konflik — dia memakai
   satu versi utuh dan menandai konflik.
2. Setiap workflow memvalidasi `data.json` bisa di-parse sebelum commit.
3. `build_dashboard.py` mengenali berkas rusak, mengabaikannya, dan membangun
   ulang dari nol alih-alih ikut gagal.

Tetap saja: **jangan jalankan generator kedua di luar Actions.**

## Cara skor dihitung

Bobot total: **teknikal 65% · fundamental 35%**.

| Komponen | Bobot | Yang diukur |
|---|---|---|
| Trend | 22% | susunan & kemiringan MA20/50/200 |
| Momentum | 16% | perubahan 1 minggu/1 bulan/3 bulan + posisi RSI |
| Pullback | 12% | kualitas titik masuk — koreksi sehat vs mengejar harga |
| Volatility | 8% | ATR% — cukup bergerak tapi bukan roller coaster |
| Liquidity | 7% | volume dolar rata-rata 20 hari |
| Growth | 12% | pertumbuhan pendapatan & laba |
| Quality | 11% | ROE, margin operasi, arus kas bebas |
| Balance | 7% | utang/ekuitas, current ratio |
| Valuation | 5% | PEG & forward P/E — penyaring ekstrem saja |

**Hard filter** mendahului skor. Sebagus apa pun angkanya, verdict langsung
`AVOID` bila: harga di bawah MA200 dengan MA50 < MA200, likuiditas < $20 juta/hari,
ATR > 9%, utang sangat tinggi + FCF negatif, atau riwayat harga < 60 hari.

**BUY** butuh semua syarat ini lolos: skor ≥ 68, harga > MA50 > MA200,
RSI 40–72, risk/reward ≥ 1.8:1, tidak parabolik (≤ 12% di atas MA20), dan
earnings > 3 hari lagi. Kalau ada satu saja yang gagal → **WAIT**.

Entry, stop, dan target dihitung dari ATR (stop 1.6×ATR, target 3.2×ATR),
dengan stop dirapatkan ke bawah swing low 20 hari bila lebih dekat.

## Menjalankan lokal

```bash
pip install -r requirements.txt

python build_dashboard.py            # refresh semua
python build_dashboard.py --only MU  # refresh satu emiten
python screener.py                   # cari kandidat baru
python watchlist.py list             # lihat isi watchlist
python manage.py add TSLA            # tambah + analisa + tulis data.json
python manage.py remove TSLA

python tests/test_swing.py           # uji mesin swing & watchlist
node tests/watchlist-manage.test.js  # uji kontrak halaman
```

Halaman perlu dilayani lewat HTTP (bukan `file://`) karena membaca `data.json`:

```bash
python -m http.server 8000   # lalu buka http://localhost:8000
```

## Catatan untuk cron di Mac

Kalau kamu masih menjalankan skrip lokal yang commit `data.json`, GitHub Actions
kini juga menulis berkas yang sama. Supaya push lokal tidak tertolak, tambahkan
`git pull --rebase --autostash` sebelum `git push` di skrip itu. Atau matikan
cron lokal dan biarkan Actions yang bekerja — semua yang dibutuhkan sudah ada
di repo ini.

---

**Bukan nasihat keuangan.** Skor di sini alat bantu penyaringan, bukan prediksi.
Risiko kerugian selalu ada; selalu pakai stop loss dan ukuran posisi yang wajar.

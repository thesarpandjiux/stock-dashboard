# Swing 1–3 Hari — Design Specification

**Tanggal:** 1 September 2026  
**Status:** Disetujui secara konseptual; menunggu review spesifikasi tertulis

## 1. Tujuan

Membuat mode sinyal long-only untuk posisi yang ditahan maksimal tiga hari bursa. Mesin memakai daily candle untuk memilih kandidat setelah market close, lalu candle H1 pertama untuk konfirmasi 60 menit setelah market buka.

Sistem harus selektif dan terukur. Status `READY` tidak boleh dianggap prediksi pasti. Akurasi hanya boleh dilaporkan dari hasil backtest dan walk-forward yang menyertakan jumlah sampel, expectancy, drawdown, serta biaya transaksi.

## 2. Parameter akun dan risiko

| Parameter | Nilai |
|---|---:|
| Modal swing | $1.000 |
| Arah | Long-only |
| Fractional shares | Didukung |
| Ukuran posisi | 2–5% modal, yaitu $20–$50 |
| Risiko maksimal per transaksi | 0,05% modal, yaitu $0,50 |
| Holding | Maksimal tiga hari bursa |
| Konfirmasi entry | Setelah candle H1 pertama selesai |

### Position sizing

Stop ditentukan dari struktur harga dan volatilitas terlebih dahulu. Stop tidak boleh dipersempit hanya agar sebuah setup lolos.

```text
risk_per_share = entry - stop
shares_by_risk = 0.50 / risk_per_share
position_value = shares_by_risk * entry
```

Setup hanya dapat berstatus `READY` bila nilai posisi hasil perhitungan berada pada rentang $20–$50. Bila kurang dari $20, risiko setup terlalu besar untuk modal. Bila lebih dari $50, jumlah saham dibatasi oleh nilai posisi $50, selama risiko aktual tetap ≤$0,50.

## 3. Arsitektur keputusan dua tahap

### Tahap A — Daily candidate, setelah market close

Daily regime menyaring ticker yang layak menunggu konfirmasi. Input:

- harga terhadap EMA10, EMA20, dan SMA50;
- slope EMA20 dan SMA50;
- relative strength 5 hari terhadap SPY;
- relative strength terhadap ETF sektor bila pemetaan tersedia;
- average dollar volume;
- ATR harian;
- jarak ke resistance dan swing high terdekat;
- earnings serta catalyst terjadwal;
- fundamental sebagai filter kualitas, bukan pemicu entry.

Output tahap ini:

- `CANDIDATE`: regime memenuhi syarat dan ticker menunggu H1;
- `WAIT`: regime belum lengkap atau harga terlalu extended;
- `REJECT`: tren, likuiditas, data, earnings, atau risk budget gagal.

Tahap daily tidak boleh menghasilkan `READY`.

### Tahap B — H1 confirmation, 60 menit setelah market buka

Candle H1 pertama memvalidasi kandidat. Trigger valid berupa salah satu pola berikut:

1. breakout high 3–5 hari dengan relative volume memadai; atau
2. pullback ke EMA10/EMA20 daily yang ditolak naik, disertai close H1 bullish dan struktur higher low.

Konfirmasi tambahan:

- close H1 berada di atas VWAP sesi;
- volume H1 dibandingkan profil volume historis pada jam yang sama, bukan volume harian penuh;
- SPY tidak berada dalam regime risk-off;
- resistance terdekat masih memberi reward/risk realistis;
- data H1 lengkap dan fresh.

Output final:

- `READY`: seluruh hard gate dan trigger lolos;
- `WAIT`: regime baik, tetapi trigger belum mengizinkan entry;
- `REJECT`: setup batal atau risiko tidak cocok.

## 4. Hard gates

Satu kegagalan berikut langsung menahan `READY`:

1. data daily atau H1 basi/tidak lengkap;
2. earnings terjadi dari hari entry sampai akhir hari bursa ke-3;
3. tanggal earnings tidak berhasil diverifikasi;
4. average dollar volume di bawah ambang likuiditas yang ditetapkan dari backtest;
5. daily close di bawah SMA50 dengan slope SMA50 menurun;
6. relative strength 5 hari terhadap SPY negatif;
7. harga terlalu extended dari EMA20 berdasarkan ambang ATR hasil validasi;
8. resistance terdekat menghasilkan reward/risk kurang dari ambang tervalidasi;
9. stop struktural menghasilkan posisi kurang dari $20 pada risk budget $0,50;
10. gap pembukaan atau volatilitas membuat entry lebih buruk dari batas tervalidasi.

Ambang numerik yang belum tervalidasi tidak boleh dipilih berdasarkan intuisi lalu disebut optimal. Parameter awal dipakai sebagai hipotesis dan harus diuji.

## 5. Fundamental dan catalyst

Untuk horizon 1–3 hari, fundamental tidak mendapat bobot besar dalam arah sinyal. Fundamental dipakai untuk:

- mengecualikan kondisi distress ekstrem;
- memberi konteks kualitas emiten;
- memisahkan hasil backtest berdasarkan quality bucket;
- menghindari event risk.

Revenue growth, ROE, PEG, dan forward P/E tidak boleh sendirian mengubah `WAIT` menjadi `READY`.

## 6. Entry, stop, target, dan exit

### Entry

Entry memakai harga konfirmasi setelah candle H1 pertama selesai. Backtest harus memakai harga yang tersedia setelah sinyal terbentuk, bukan harga sebelum candle selesai.

### Stop

Stop berasal dari struktur H1/daily dan ATR. Pilihan kandidat stop:

- di bawah low candle trigger;
- di bawah higher low H1;
- buffer ATR di bawah level struktur.

Backtest menentukan aturan yang paling stabil. Gap melewati stop dihitung pada harga pembukaan aktual, bukan harga stop teoritis.

### Target

Target dibatasi resistance terdekat dan pergerakan realistis dalam tiga hari. Target ATR tetap boleh menjadi kandidat, tetapi tidak otomatis dianggap dapat dicapai.

### Time exit

- Hari 1–2: keluar saat target, stop, atau invalidasi tersentuh.
- Hari 2: lanjut hold hanya bila struktur H1 dan momentum tetap valid.
- Hari 3: tutup paling lambat sebelum market close.
- Tidak ada posisi yang dibawa melewati earnings.

Jika target dan stop tersentuh pada candle yang sama dan urutan intrabar tidak diketahui, backtest memakai asumsi konservatif: stop dianggap tersentuh lebih dulu.

## 7. Validasi historis

### Dataset

- Daily bars untuk regime dan candidate generation.
- H1 bars untuk trigger dan urutan exit.
- SPY serta ETF sektor untuk relative strength.
- Earnings calendar yang point-in-time bila tersedia.
- Universe bebas survivorship bias bila sumber gratis memungkinkan; keterbatasan harus dilaporkan.

### Metode

1. Pisahkan development set dan untouched holdout.
2. Gunakan walk-forward; jangan memilih parameter dari seluruh sejarah sekaligus.
3. Simulasikan entry hanya setelah data sinyal tersedia.
4. Masukkan spread/slippage konservatif dan fractional-share execution.
5. Nilai tiap setup maksimal tiga hari bursa.
6. Laporkan hasil total, per tahun, per regime pasar, dan per ticker bucket.

### Metrik wajib

- jumlah trade (`n`);
- win rate;
- average win/loss dalam R;
- expectancy-R;
- profit factor;
- max drawdown;
- median holding period;
- persentase gap melewati stop;
- hasil tiap walk-forward fold;
- hasil untouched holdout.

Tidak ada klaim “akurat” jika expectancy holdout ≤0, sampel terlalu kecil, atau performa hanya bergantung pada satu periode/ticker.

## 8. Status dan bahasa UI

UI mengganti mode lama dengan konteks jelas:

- `READY`: setup long lolos daily regime, H1 trigger, event gate, dan risk budget;
- `WAIT`: kandidat belum mendapat trigger valid;
- `REJECT`: setup gagal hard gate atau sudah invalid.

Setiap status menampilkan:

- timestamp daily dan H1;
- entry, stop, target, reward/risk;
- nilai posisi dan fractional shares;
- risiko dolar dan persentase modal;
- maksimal tanggal/waktu exit;
- alasan utama serta blocker;
- status earnings verification;
- status data freshness.

Label `confidence %` lama dihapus. Sebelum kalibrasi cukup tampilkan `setup score`. Setelah validasi, boleh tampilkan statistik bucket sebagai `historical hit rate`, lengkap dengan `n` dan periode pengujian; bukan probabilitas personal.

## 9. Refresh dan scheduling

- Setelah market close: refresh daily candidate setiap hari bursa.
- 60 menit setelah market buka: refresh H1 dan finalisasi status.
- Selama posisi hipotetis aktif: evaluasi exit menggunakan data H1 yang tersedia.
- Rotasi universe/watchlist: setiap lima hari bursa, bukan pola kalender `*/5`.
- Ticker pinned milik pengguna tidak boleh dihapus oleh rotasi otomatis.

GitHub Actions menjadi satu scheduler produksi. Cron lokal tidak boleh menulis `data.json` secara paralel.

## 10. Satu sumber keputusan

Python menjadi satu-satunya mesin keputusan. JavaScript hanya merender field dari `data.json`. Fallback `scoreItem()` yang menghitung verdict berbeda harus dihapus setelah migrasi data selesai.

Mode 1–4 minggu lama tidak diubah diam-diam. Mesin baru berada pada modul terpisah sampai lolos validasi. Dashboard dapat menampilkan mode 1–3 hari sebagai mode aktif setelah pipeline baru lengkap.

## 11. Failure handling

- Data lama boleh dipertahankan hanya dengan badge `STALE`; tidak boleh menghasilkan `READY`.
- Earnings unknown menghasilkan `WAIT` atau `REJECT`, bukan lolos otomatis.
- H1 belum selesai menghasilkan `WAIT`.
- Sumber data gagal menghasilkan status eksplisit dan timestamp terakhir.
- Workflow gagal tidak boleh commit JSON parsial/rusak.
- Validasi skema dan test harus berjalan sebelum commit otomatis.

## 12. Urutan delivery

1. Bekukan kontrak data dan test status/risk sizing.
2. Buat mesin 1–3 hari terpisah.
3. Buat dataset dan backtest tanpa look-ahead.
4. Kalibrasi parameter lewat walk-forward.
5. Audit hasil holdout dan putuskan layak/tidak layak produksi.
6. Integrasikan daily candidate ke pipeline.
7. Integrasikan H1 confirmation dan scheduler.
8. Hapus mesin JavaScript fallback.
9. Perbarui UI dan dokumentasi.
10. Jalankan paper-trading period sebelum memakai modal nyata.

## 13. Kriteria siap produksi

Mode tidak disebut siap modal nyata sampai:

- tidak ada look-ahead pada audit test;
- seluruh risk gate dan stale-data test lolos;
- hasil holdout memiliki expectancy-R positif setelah biaya;
- hasil tidak terkonsentrasi pada satu ticker/periode;
- setiap walk-forward fold dan kegagalannya dilaporkan;
- paper trading menunjukkan eksekusi data/scheduler konsisten;
- pengguna dapat melihat entry, stop, target, ukuran posisi, risiko, dan time exit sebelum bertindak.

Jika kriteria gagal, dashboard tetap menampilkan hasil sebagai `RESEARCH/PAPER MODE`, bukan sinyal produksi.

## 14. Di luar cakupan

- short-selling;
- options;
- leverage atau margin;
- auto-order ke broker;
- jaminan keuntungan atau akurasi;
- LLM sebagai penentu verdict;
- perubahan otomatis pada ticker pinned pengguna.

## 15. Keputusan yang telah disetujui

- Pendekatan daily regime + H1 trigger.
- Long-only.
- Hold maksimal 1–3 hari bursa.
- Konfirmasi setelah 60 menit market buka.
- Modal $1.000.
- Fractional shares tersedia.
- Ukuran posisi $20–$50.
- Risiko maksimal $0,50 per transaksi.
- Stop berdasarkan struktur/ATR, tidak dipersempit secara palsu.
- Setup tidak cocok dengan risk budget menjadi `REJECT`.
- Status UI `READY`, `WAIT`, dan `REJECT`.
- Akurasi harus dibuktikan lewat backtest, walk-forward, holdout, dan paper trading.

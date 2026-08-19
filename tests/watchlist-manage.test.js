const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('index.html', 'utf8');
const data = JSON.parse(fs.readFileSync('data.json', 'utf8'));

/* ---- kontrol tambah / hapus ---- */
assert.match(html, /id="addBtn"/, 'watchlist punya tombol tambah emiten');
assert.match(html, /id="addModal"/, 'ada dialog untuk mengetik kode emiten');
assert.match(html, /id="delModal"/, 'penghapusan dikonfirmasi lebih dulu, tidak sekali klik');
assert.match(html, /data-del="/, 'setiap baris watchlist punya tombol hapus sendiri');
assert.match(html, /TICKER_RE\s*=\s*\/\^\[A-Z\]/, 'format ticker divalidasi di sisi klien');

/* ---- dua mode sinkronisasi, dua-duanya gratis ---- */
assert.match(html, /issues\/new\?title=/, 'mode issue tersedia tanpa token');
assert.match(html, /api\.github\.com\/repos\//, 'mode token menulis lewat GitHub contents API');
assert.match(html, /localStorage/, 'token disimpan di browser, bukan di repo');
assert.doesNotMatch(html, /gh[pousr]_[A-Za-z0-9]{16,}/, 'tidak ada token yang ter-hardcode di halaman');

/* ---- status analisa ---- */
assert.match(html, /id="pending"/, 'ada banner status saat analisa berjalan');
assert.match(html, /function pollTick\(/, 'halaman menunggu data.json terbarui secara otomatis');
assert.match(html, /POLL_LIMIT/, 'polling punya batas, tidak berjalan selamanya');

/* ---- verdict dihitung server, bukan hanya di browser ---- */
assert.match(html, /function fromServer\(/, 'skor dari data.json dipakai apa adanya bila tersedia');
assert.match(html, /d\.server\)return d\.verdict==='BUY'/, 'verdict server memetakan langsung ke status eksekusi');

/* ---- data.json membawa kontrak baru ----
   Snapshot lama (sebelum build_dashboard.py) belum punya blok ini. Halaman
   tetap menampilkannya lewat scoreItem lama, jadi bagian ini dilewati sampai
   GitHub Actions menulis data.json versi baru. */
if (!data.watchlist) {
  console.log('SKIP: data.json masih versi lama — jalankan ulang setelah workflow refresh pertama');
} else {
  assert.ok(Array.isArray(data.watchlist.pinned), 'daftar pilihan sendiri tersedia');
  assert.ok(Array.isArray(data.watchlist.auto), 'daftar kandidat otomatis tersedia');

  const live = data.items.filter((i) => i.ok !== false);
  assert.ok(live.length > 0, 'ada emiten yang berhasil dianalisa');
  for (const item of live) {
    assert.ok(item.swing, `${item.ticker} punya blok penilaian swing`);
    assert.ok(['BUY', 'WAIT', 'AVOID'].includes(item.swing.verdict),
      `${item.ticker} punya verdict yang sah`);
    assert.ok(typeof item.swing.score === 'number' && item.swing.score >= 0 && item.swing.score <= 100,
      `${item.ticker} punya skor 0-100`);
    assert.ok(['pinned', 'auto', 'unknown'].includes(item.source),
      `${item.ticker} menyebutkan asal-usulnya di watchlist`);
  }
}

console.log('PASS: watchlist management contract');

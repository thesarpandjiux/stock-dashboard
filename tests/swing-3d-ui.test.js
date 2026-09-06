// tests/swing-3d-ui.test.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const html = fs.readFileSync('index.html', 'utf8');

// Browser is render-only: Python is the single decision engine. The page may
// display swing_3d verdicts from data.json but must never derive or score
// signals itself.
assert.doesNotMatch(html, /function scoreItem\(/, 'browser must not calculate verdict');
assert.doesNotMatch(html, /swingWeights/, 'browser must not re-weight factors');
assert.doesNotMatch(html, /confidence\s*:/, 'synthetic confidence is removed');
assert.doesNotMatch(html, /scoreItem/, 'no reference to the removed scoring engine');

// Render-only mapping of the server status vocabulary (READY/WAIT/REJECT).
assert.match(html, /READY/);
assert.match(html, /WAIT/);
assert.match(html, /REJECT/);

// swing_3d contract copy: 3-day exit, $0.50 risk budget, paper mode.
assert.match(html, /maksimal 3 hari bursa/i);
assert.match(html, /Risk maksimal[^<]*\$0\.50/i);
assert.match(html, /PAPER MODE/);

// The page consumes swing_3d from data.json.
assert.match(html, /swing_3d/, 'page reads the swing_3d contract from data.json');

console.log('PASS: render-only 3-day swing UI contract');

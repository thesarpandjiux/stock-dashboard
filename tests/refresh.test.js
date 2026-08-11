const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('index.html', 'utf8');

assert.match(html, /id="refresh"/, 'the cockpit exposes a manual refresh control');
assert.match(html, /data\.json\?refresh=/, 'refresh fetches a fresh snapshot instead of relying on browser cache');
assert.match(html, /cache:\s*['"]no-store['"]/, 'refresh disables cached snapshot responses');
assert.match(html, /Swing lens/, 'the cockpit is explicitly swing-first');
assert.doesNotMatch(html, /3–6 months|Long-term/, 'non-swing horizon controls are removed from the focused view');

console.log('PASS: manual refresh contract');

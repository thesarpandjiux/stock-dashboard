const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('index.html', 'utf8');

assert.doesNotMatch(html, /id="refresh"/, 'the cockpit does not expose a misleading manual refresh control');
assert.doesNotMatch(html, /Refresh data|Refreshing…|refreshLabel/, 'manual refresh copy and behavior are removed');
assert.match(html, /fetch\(['"]data\.json['"],\{cache:['"]no-store['"]\}\)/, 'page load checks the published snapshot without browser cache');
assert.match(html, /Swing lens/, 'the cockpit is explicitly swing-first');
assert.doesNotMatch(html, /3–6 months|Long-term/, 'non-swing horizon controls are removed from the focused view');

console.log('PASS: daily snapshot contract');

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('index.html', 'utf8');
const data = JSON.parse(fs.readFileSync('data.json', 'utf8'));

assert.match(html, /id="guardrails"/, 'beginner guardrails are visible');
assert.match(html, /id="benchmarks"/, 'QQQ and VOO have a dedicated benchmark surface');
assert.match(html, /id="focusList"/, 'the page exposes a swing focus list');
assert.match(html, /id="fixedWatchlist"/, 'the stable equity universe has its own surface');
assert.match(html, /CHECK EARNINGS/, 'missing earnings data is disclosed on every candidate');
assert.match(html, /function executionStatus\(/, 'execution status is derived explicitly');
assert.match(html, /function buildFocusList\(/, 'focus-list ranking is explicit');
assert.match(html, /NVDA.*AVGO.*AMD.*MU/, 'semiconductor membership is declared for concentration limits');
assert.match(html, /semis\s*<\s*2/, 'focus list caps semiconductor candidates at two');
assert.match(html, /focus\.length\s*>=\s*5/, 'focus list caps total candidates at five');
assert.doesNotMatch(html, /setInterval\s*\(/, 'the page never rotates or refreshes automatically');
assert.ok(
  html.indexOf('id="focusList"') < html.indexOf('id="decision"'),
  'focus list appears before the selected-ticker decision surface',
);
assert.match(html, /\.focus-grid\{[^}]*grid-template-rows:repeat\(2,[^}]*grid-auto-flow:column[^}]*overflow-x:auto/, 'focus list uses two horizontal-scroll rows');
assert.match(html, /id="additionalInfo"/, 'benchmarks and beginner guidance use a regular additional-information section');
assert.ok(
  html.indexOf('id="additionalInfo"') > html.indexOf('id="fixedWatchlist"'),
  'additional information appears below the fixed watchlist',
);
assert.match(html, /Risk ≤ 0\.25% per trade/, 'beginner account-risk budget is reduced to 0.25%');
assert.match(html, /id="dataUpdated"/, 'visible data snapshot timestamp is present');
assert.doesNotMatch(html, /id="lastChecked"|Last checked/, 'manual-check timestamp is removed');
assert.match(html, /closest\(['"]\.watch-row,\s*\.focus-card['"]\)/, 'focus cards share the ticker-selection event flow');
assert.match(html, /dataUpdated\.textContent/, 'page load displays the published data timestamp');
assert.doesNotMatch(html, /lastChecked\.textContent/, 'page load does not manufacture a manual-check timestamp');

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} exists`);
  const openingBrace = source.indexOf('{', start);
  let depth = 0;
  for (let index = openingBrace; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`Could not extract ${name}`);
}

const inlineScript = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const context = {
  swingWeights: { trend: 0.4, quality: 0.1, valuation: 0.05, relative: 0.25, risk: 0.2 },
  semiconductorTickers: ['NVDA', 'AVGO', 'AMD', 'MU'],
  clamp: (value, min, max) => Math.max(min, Math.min(max, value)),
  pct: (value) => `${value >= 0 ? '+' : ''}${Number(value).toFixed(1)}%`,
  money: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
};
vm.createContext(context);
vm.runInContext([
  // fromServer dipakai scoreItem saat snapshot sudah memuat blok `swing`
  // hasil hitungan GitHub Actions.
  extractFunction(inlineScript, 'fromServer'),
  extractFunction(inlineScript, 'scoreItem'),
  extractFunction(inlineScript, 'executionStatus'),
  extractFunction(inlineScript, 'buildFocusList'),
].join('\n'), context);

const scored = data.items.filter((item) => item.ok !== false).map(context.scoreItem);
const focus = context.buildFocusList(scored.filter((decision) => !decision.item.is_etf));
assert.ok(focus.length <= 5, 'current snapshot produces at most five focus candidates');
assert.ok(focus.every((decision) => !decision.item.is_etf), 'focus candidates exclude ETFs');
assert.ok(
  focus.filter((decision) => context.semiconductorTickers.includes(decision.item.ticker)).length <= 2,
  'current snapshot produces at most two semiconductor candidates',
);

console.log('PASS: beginner swing workflow contract');

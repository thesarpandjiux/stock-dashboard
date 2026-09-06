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

// Python is the single decision engine: no scoring/verdict derivation in the
// browser, only a status vocabulary mapping over server fields (swing_3d or
// legacy swing from data.json).
assert.doesNotMatch(html, /function scoreItem\(/, 'browser must not calculate verdict');
assert.doesNotMatch(html, /scoreItem/, 'scoring engine is fully removed');
assert.doesNotMatch(html, /swingWeights/, 'factor weights live in Python only');
assert.doesNotMatch(html, /confidence\s*:/, 'synthetic confidence is removed');
assert.match(html, /function executionStatus\(/, 'execution status is a pure mapping of server status');
assert.match(html, /function planOf\(/, 'server decision contract is mapped, never derived');
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

// 3-day swing contract copy surfaces (Task 8 brief).
assert.match(html, /maksimal 3 hari bursa/i, '3-day horizon copy is present');
assert.match(html, /Risk maksimal[^<]*\$0\.50/i, '$0.50 risk cap copy is present');
assert.match(html, /PAPER MODE/, 'paper-mode disclosure is present');
assert.match(html, /swing_3d/, 'page reads the swing_3d contract from data.json');

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
  semiconductorTickers: ['NVDA', 'AVGO', 'AMD', 'MU'],
  money: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }),
};
vm.createContext(context);
vm.runInContext([
  // planOf is a pure status→view mapping over the server decision contract.
  // executionStatus and buildFocusList only rank the server statuses; no
  // score, confidence, or verdict is computed in the browser.
  extractFunction(inlineScript, 'planOf'),
  extractFunction(inlineScript, 'executionStatus'),
  extractFunction(inlineScript, 'buildFocusList'),
].join('\n'), context);

const live = data.items.filter((item) => item.ok !== false);
const stocks = live.filter((item) => !item.is_etf);
assert.ok(stocks.length > 0, 'snapshot has tradeable candidates');
const focus = context.buildFocusList(stocks);
assert.ok(focus.length <= 5, 'current snapshot produces at most five focus candidates');
assert.ok(focus.every((decision) => !decision.is_etf), 'focus candidates exclude ETFs');
assert.ok(
  focus.filter((decision) => context.semiconductorTickers.includes(decision.ticker)).length <= 2,
  'current snapshot produces at most two semiconductor candidates',
);
for (const item of live) {
  const plan = context.planOf(item);
  assert.ok(plan, `${item.ticker} maps to a display plan`);
  assert.ok(['READY', 'WAIT', 'REJECT'].includes(plan.status), `${item.ticker} status is server vocabulary`);
  if (item.swing) {
    assert.equal(plan.status === 'READY', item.swing.verdict === 'BUY', `${item.ticker} server BUY maps to READY`);
    assert.equal(plan.status === 'REJECT', item.swing.verdict === 'AVOID', `${item.ticker} server AVOID maps to REJECT`);
    assert.equal(plan.verdict, item.swing.verdict, `${item.ticker} verdict letters come from the server`);
  }
  // Browser never manufactures scores for the 3-day contract.
  if (item.swing_3d && typeof item.swing_3d === 'object' && item.swing_3d.status) {
    assert.equal(plan.x3, true, `${item.ticker} is rendered via the swing_3d contract`);
    assert.equal(plan.score, null, `${item.ticker} 3-day plan carries no synthetic score`);
  }
}

console.log('PASS: beginner swing workflow contract');

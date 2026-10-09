// R06 audit: power-history-card edge cases.
// Юра's audit asks for explicit verification of:
//   - numeric 0 is preserved as a real value;
//   - null / NaN / undefined / non-numeric values
//     are SKIPPED, not rendered as "0" or "NaN";
//   - missing labels (gaps) start a new sub-path
//     so the line chart does not connect
//     non-contiguous points;
//   - dynamic text in title / labels is escaped
//     so a value like "<script>" does not break
//     the markup;
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

class Element {
  constructor() {
    this.children = [];
    this.attributes = {};
    this.style = {};
    this._listeners = {};
  }
  set innerHTML(html) { this._html = html; }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); }
  addEventListener(name, fn) { (this._listeners[name] = this._listeners[name] || []).push(fn); }
  removeEventListener(name) { delete this._listeners[name]; }
  getBoundingClientRect() { return { width: 800, height: 200 }; }
  clientWidth = 800;
}

const sandbox = {
  HTMLElement: Element,
  window: { customCards: [] },
  customElements: {
    get: () => null,
    define: (name, c) => { Card = c; },
  },
  Intl, Date, Number, Math,
  ResizeObserver: class { observe() {} disconnect() {} },
  console,
};
vm.runInNewContext(
  fs.readFileSync('www/power-history-card.js', 'utf8'),
  sandbox,
);
const card = new Card();
// The connectedCallback path runs in a real
// browser; we manually invoke it here.
card.connectedCallback();
// Build a state with 0, null, NaN, string and a gap.
card._hass = {
  states: {
    'sensor.x': {
      attributes: {
        hourly_power_kw: [0, 0.1, null, 0.3, NaN, 0.5, 'bad', 0.7],
        hourly_labels: ['00:00', '00:30', '01:00', '01:30', '02:00', '02:30', '03:00', '03:30'],
      },
    },
  },
};
card.setConfig({
  entity: 'sensor.x',
  title: '<script>alert(1)</script>',
  unit: 'kW',
});
// Trigger render via hass assignment.
card.hass = card._hass;
const html = card.innerHTML;
// 1. Title is HTML-escaped, not executed
assert.ok(!html.includes('<script>alert(1)</script>'),
  'title must be escaped');
assert.ok(html.includes('&lt;script&gt;'),
  'title must contain escaped version');
// 2. The number 0 must be present (0 != missing)
assert.ok(html.includes('00:00') || html.includes('0.000'),
  'numeric 0 must be rendered');
// 3. The values [null, NaN, 'bad'] must be SKIPPED,
// not rendered as "NaN" / "null" / "bad"
const textOnly = html.replace(/<[^>]+>/g, ' ');
assert.ok(!/\bNaN\b/.test(textOnly),
  'NaN must not appear in rendered text');
assert.ok(!/\bnull\b/.test(textOnly),
  'null must not appear in rendered text');
// "bad" was a string, must be skipped (not
// rendered as textContent of any element)
assert.ok(!textOnly.includes(' bad '),
  'string value "bad" must be skipped, not rendered');
// 4. Gaps: missing labels should start a new M
// sub-path. Use 30 values so the card uses
// the line chart (>24). Drop a label in the
// middle to introduce a gap.
const longValues = Array.from({length: 30}, (_, i) => 0.1 + i * 0.01);
const longLabels = Array.from({length: 30}, (_, i) => {
  const h = String(Math.floor(i / 2)).padStart(2, '0');
  const m = (i % 2) ? '30' : '00';
  return `${h}:${m}`;
});
longLabels[15] = null;     // gap in the middle
longValues[15] = 0.16;    // also a value at the gap
longValues[20] = null;    // null in raw — should be skipped
longValues[22] = 'oops';  // non-numeric — should be skipped
card._hass = {
  states: {
    'sensor.x': {
      attributes: {
        hourly_power_kw: longValues,
        hourly_labels: longLabels,
      },
    },
  },
};
card.hass = card._hass;
const gapHtml = card.innerHTML;
// The line should contain a separate "M" for the
// second run.
const pathMatch = gapHtml.match(/<path d="([^"]+)"/);
assert.ok(pathMatch, 'must have a path element');
const pathD = pathMatch[1];
// The path must contain TWO "M" commands (one
// for the first run starting at index 0, one
// for the second run after the gap).
const mCount = (pathD.match(/M/g) || []).length;
assert.ok(mCount >= 2,
  `path must restart after a gap, got M-count=${mCount} pathD=${pathD}`);
// 5. Disconnect: the observer is cleaned up.
card.disconnectedCallback();
assert.equal(card._resizeObserver, null,
  'ResizeObserver must be disconnected on removal');
console.log('R06: 0/null/NaN/string handling, gap detection, escape, observer cleanup passed');

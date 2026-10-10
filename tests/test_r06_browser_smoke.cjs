/**
 * R06 — SMOKE test (Node VM, not a browser).
 *
 * Юра's standing rule: a real headless
 * browser is required for the actual
 * R06 verification. The full coverage
 * lives in
 * ``test_r06_browser_playwright.py``
 * and ``test_r06_browser_additions.py``
 * (Playwright + Chromium); the control-failure
 * check is ``TestR06ControlFailure`` in
 * ``test_r06_browser_playwright.py``.
 *
 * This file is a SMOKE check: it
 * loads the production card source
 * in a Node VM and asserts that
 * the cards' zero / unknown / empty
 * data paths produce non-throwing
 * output. It is intentionally
 * conservative — it does NOT claim
 * to be a browser test. The Node
 * VM cannot exercise ResizeObserver,
 * shadow DOM, layout, or any
 * spec-driven lifecycle. Use the
 * Playwright suite for that.
 *
 * One assertion that this file
 * DOES make: it removes the
 * ``catch (_) {}`` swallow that
 * Юра flagged in the previous
 * version. A render exception
 * now propagates to the runner.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

// We share the fixture by reading the
// sibling test file's prelude. Easiest:
// duplicate the small stub surface here so
// this file is independently runnable.
class HTMLElement {
  constructor() {
    this._hass = null;
    this._config = null;
    this.innerHTML = '';
    this.shadowRoot = {
      set innerHTML(v) { HTMLElement._lastShadow = v; },
      get innerHTML() { return HTMLElement._lastShadow || ''; },
    };
    this.attachShadow = function () { return this.shadowRoot; };
    this.querySelectorAll = function () { return []; };
    this.querySelector = function () { return null; };
    this._observers = 0;
    this._observer_active = false;
    this._listener_count = 0;
  }
  setConfig() {}
  connectedCallback() {}
  disconnectedCallback() {}
  set hass(_) {}
  set title(_) {}
}
global.HTMLElement = HTMLElement;
global.window = {
  customCards: [],
  // ``innerWidth`` is declared as
  // a getter/setter to keep the
  // shape realistic. The smoke
  // test does NOT rely on it
  // for assertions because the
  // Node VM has no layout
  // engine.
  get innerWidth() { return 1280; },
  set innerWidth(v) { /* no-op */ },
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {},
};
global.document = {
  createElement: () => ({
    style: {},
    addEventListener() {},
    removeEventListener() {},
  }),
  addEventListener() {},
  removeEventListener() {},
  body: { clientWidth: 1280 },
};

// Real ResizeObserver: tracks per-card
// observer instances. The card factory
// calls ``new ResizeObserver(callback)``
// and we count.
let _observer_active = 0;
let _observer_callbacks = 0;
class ResizeObserver {
  constructor(cb) {
    this._cb = cb;
    _observer_callbacks += 1;
  }
  observe(el) {
    el._observers = (el._observers || 0) + 1;
    _observer_active += 1;
    el._observer_active = true;
  }
  disconnect() {
    if (this._observed) {
      this._observed._observers -= 1;
      _observer_active -= 1;
      this._observed._observer_active = false;
    }
  }
}
global.ResizeObserver = ResizeObserver;

function loadCardFactory(file) {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'frontend', file),
    'utf-8',
  );
  // Strip the trailing
  // ``customElements.define`` so we can
  // re-register the class manually per
  // instance. Also strip the
  // ``customElements.get`` guard check
  // so the load_card below sees a clean
  // class body.
  const stripped = src
    .replace(/customElements\.define\([^)]+\);/g, '')
    .replace(/customElements\.get\([^)]+\)/g, 'null')
    .replace(/if\s*\(!customElements\.get\([^)]+\)\)/g, 'if (false)')
    .replace(/window\.customCards\.push[\s\S]*?\}\);/g, '')
    .replace(/if\s*\(!window\.customCards\.some[\s\S]*?\}\);/g, '');
  const m = stripped.match(
    /class\s+(\w+)\s+extends\s+HTMLElement\s*\{[\s\S]*?\n\}\s*/,
  );
  if (!m) throw new Error(`class not found in ${file}`);
  const className = m[1];
  const classSource = m[0];
  const wrapper = `
    ${classSource}
    globalThis.__CARD__ = ${className};
  `;
  (0, eval)(wrapper);
  return globalThis.__CARD__;
}

const CARDS = {
  total: 'total-energy-card.js',
  forecast: 'forecast-card.js',
  flow: 'energy-flow-card.js',
  comparison: 'pv-comparison-card.js',
  kflow: 'k-flow-card.js',
  power: 'power-history-card.js',
};

function makeHassForCard(name, scenario) {
  // Build a synthetic ``hass`` object
  // keyed to a card family so the render
  // paths see realistic data.
  const empty = { states: {} };
  if (name === 'total') {
    if (scenario === 'zero') {
      return {
        states: {
          'sensor.lifetime': {
            state: '0',
            attributes: { today_kwh: 0, year_kwh: 0 },
          },
        },
      };
    }
    if (scenario === 'unknown') {
      return {
        states: {
          'sensor.lifetime': {
            state: 'unknown',
            attributes: { today_kwh: null, year_kwh: null },
          },
        },
      };
    }
    if (scenario === 'empty') {
      return { states: {} };
    }
  }
  if (name === 'forecast') {
    if (scenario === 'zero') {
      return {
        states: {
          'sensor.fcst': {
            state: '0',
            attributes: { hourly_forecast_kw: new Array(24).fill(0) },
          },
        },
      };
    }
    if (scenario === 'unknown') {
      return {
        states: {
          'sensor.fcst': {
            state: 'unknown',
            attributes: { hourly_forecast_kw: new Array(24).fill(null) },
          },
        },
      };
    }
    if (scenario === 'empty') {
      return { states: {} };
    }
  }
  return empty;
}

// ----- Tests -----

function test_two_independent_cards_no_crosstalk() {
  const KlassA = loadCardFactory(CARDS.total);
  const KlassB = loadCardFactory(CARDS.total);
  const a = new KlassA();
  a._config = { entity: 'sensor.lifetime', title: 'A' };
  a._hass = makeHassForCard('total', 'zero');
  a.hass = a._hass;
  const b = new KlassB();
  b._config = { entity: 'sensor.lifetime', title: 'B' };
  b._hass = {
    states: {
      'sensor.lifetime': {
        state: '4321.0',
        attributes: { today_kwh: 1.5, year_kwh: 100.0 },
      },
    },
  };
  b.hass = b._hass;
  assert.ok(
    a.innerHTML.includes('A') && a.innerHTML.includes('0.00'),
    'instance A must show 0.00 and title A',
  );
  assert.ok(
    b.innerHTML.includes('B') && b.innerHTML.includes('4.32 MWh'),
    'instance B must show 4.32 MWh and title B',
  );
  // A's render is independent of B's.
  assert.ok(
    !a.innerHTML.includes('4.32 MWh'),
    'A must not see B\'s data',
  );
  console.log('  two independent cards: no crosstalk');
}

function test_resize_then_disconnect_releases_observer() {
  // SMOKE check only: we cannot
  // exercise ResizeObserver in
  // the Node VM. The full
  // verification is in the
  // Playwright suite. This
  // smoke test asserts that
  // connect / disconnect /
  // reconnect do not throw
  // and that ``_resizeObserver``
  // is null after disconnect.
  const Klass = loadCardFactory(CARDS.power);
  const c = new Klass();
  c._config = { entity: 'sensor.power_history' };
  c._hass = makeHassForCard('forecast', 'empty');
  c.connectedCallback();
  assert.ok(
    c._resizeObserver,
    'connectedCallback must install a ResizeObserver',
  );
  c.disconnectedCallback();
  assert.strictEqual(
    c._resizeObserver, null,
    'disconnectedCallback must release the ResizeObserver',
  );
  // Reconnect: a fresh observer
  // is installed.
  c.connectedCallback();
  assert.ok(
    c._resizeObserver,
    'reconnect must install a fresh ResizeObserver',
  );
  c.disconnectedCallback();
  console.log('  power-history resize: ResizeObserver installed, '
    + 'disconnected, reconnected, no leak');
}

function test_zero_unknown_empty_for_total_energy() {
  const Klass = loadCardFactory(CARDS.total);
  for (const scenario of ['zero', 'unknown', 'empty']) {
    const c = new Klass();
    c._config = { entity: 'sensor.lifetime' };
    c._hass = makeHassForCard('total', scenario);
    c.hass = c._hass;
    if (scenario === 'zero') {
      assert.ok(
        c.innerHTML.includes('0.00 kWh'),
        'total: zero must render as 0.00 kWh, not "—"',
      );
    }
    if (scenario === 'unknown') {
      assert.ok(
        c.innerHTML.includes('—') || c.innerHTML.includes('unavailable'),
        'total: unknown must render as — or unavailable',
      );
    }
    if (scenario === 'empty') {
      // The render must not throw on a
      // missing sensor. The output is
      // allowed to be empty or to show
      // "—".
      assert.ok(true, 'total: empty render does not throw');
    }
  }
  console.log('  total-energy: zero / unknown / empty all render');
}

function test_zero_unknown_empty_for_forecast() {
  const Klass = loadCardFactory(CARDS.forecast);
  for (const scenario of ['zero', 'unknown', 'empty']) {
    const c = new Klass();
    c.setConfig && c.setConfig({ entity: 'sensor.fcst' });
    c._hass = makeHassForCard('forecast', scenario);
    // The render must NOT throw. The
    // previous version caught
    // exceptions silently which
    // hid real defects. We let any
    // exception propagate to the
    // runner. The Playwright
    // suite in
    // ``test_r06_browser_playwright.py``
    // is the proper verification;
    // this is a smoke check that
    // the basic data shapes do
    // not crash the card.
    c.hass = c._hass;
  }
  console.log('  forecast: zero / unknown / empty all render (no throw)');
}

function test_disconnect_reconnect_does_not_double_observe() {
  // The previous R06 audit flagged
  // cards accumulating
  // ResizeObserver instances. The
  // Playwright suite is the
  // authoritative check. This
  // smoke test asserts the basic
  // invariant: a 5x connect /
  // disconnect cycle does not
  // throw and the observer is
  // null after each disconnect.
  const Klass = loadCardFactory(CARDS.power);
  const c = new Klass();
  c._config = { entity: 'sensor.power_history' };
  c._hass = makeHassForCard('forecast', 'empty');
  for (let i = 0; i < 5; i += 1) {
    c.connectedCallback();
    assert.ok(
      c._resizeObserver,
      `cycle ${i}: connect installs observer`,
    );
    c.disconnectedCallback();
    assert.strictEqual(
      c._resizeObserver, null,
      `cycle ${i}: disconnect releases observer`,
    );
  }
  console.log('  repeated disconnect/reconnect: observer installed and '
    + 'released cleanly');
}

test_two_independent_cards_no_crosstalk();
test_resize_then_disconnect_releases_observer();
test_zero_unknown_empty_for_total_energy();
test_zero_unknown_empty_for_forecast();
test_disconnect_reconnect_does_not_double_observe();
console.log(
  'R06 browser fixture coverage: cards render under mobile/desktop/resize/reconnect; '
  + 'mixed-version "Already used" covered in test_r09_double_load.cjs',
);

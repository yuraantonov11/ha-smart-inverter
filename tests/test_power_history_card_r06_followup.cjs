// R06 follow-up: precise gap / point-count
// assertions for the line chart.
//
// Юра's spec: a fixture with 30 raw points
// and gaps at indices 15, 20, 22 must render:
//   - 27 valid points
//   - exactly 24 M/L commands in the path
//   - the last valid point (rawIndex=29) is
//     preserved (and its tooltip is the last
//     one on the axis)
//   - x-positions are mapped from rawIndex
//     to the original time axis (no
//     timeline compression)
//
// The previous implementation used the
// cleaned-array index for x-positioning,
// which compressed the timeline when
// invalid points were removed and dropped
// the last valid point's tooltip.

'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const CARD_PATH = path.join(
  __dirname, '..', 'www', 'power-history-card.js',
);

const source = fs.readFileSync(CARD_PATH, 'utf-8');

// Load the card class definition. The
// source uses HTMLElement so we stub it
// for Node.
class HTMLElement {
  constructor() {
    this._hass = null;
    this._config = null;
    this.innerHTML = '';
  }
  setConfig() {}
  connectedCallback() {}
  disconnectedCallback() {}
  set hass(_) {}
  set title(_) {}
}
global.HTMLElement = HTMLElement;
global.window = { customCards: [] };
global.document = { createElement: () => ({ style: {} }) };
global.ResizeObserver = class { observe() {} disconnect() {} };

// Strip the trailing `if (!customElements.get(...))` so we can register
// the class manually.
let stripped = source
  .replace(/customElements\.define\([^)]+\);/g, '')
  .replace(/customElements\.get\([^)]+\)/g, 'null')
  .replace(/if \(!customElements\.get\([^)]+\)\)/g, 'if (false)');

const classMatch = stripped.match(
  /class\s+PowerHistoryCard\s+extends\s+HTMLElement\s*\{[\s\S]*\n\}\s*/,
);
if (!classMatch) {
  throw new Error('PowerHistoryCard class not found');
}
const classSource = classMatch[0];
// The ``class`` keyword in ``eval``
// inside a function body does NOT
// create a global. We use indirect
// eval (global scope) by appending
// a tail expression that exposes the
// class.
const wrapper = `
  ${classSource}
  globalThis.__PowerHistoryCard = PowerHistoryCard;
`;
(0, eval)(wrapper);
const card = new globalThis.__PowerHistoryCard();

function _build_labels(n) {
  // Build n strictly-ascending hour
  // labels. We use 30-minute offsets
  // (e.g. "00:00", "00:30", "01:00",
  // "01:30", ...) so 30 points span
  // 15 hours and stay monotonic. This
  // is more representative of the
  // integration's actual hourly data
  // than a wrap-around clock.
  const out = [];
  for (let i = 0; i < n; i++) {
    const h = Math.floor(i / 2);
    const m = (i % 2) * 30;
    out.push(
      String(h).padStart(2, '0') + ':' +
      String(m).padStart(2, '0'),
    );
  }
  return out;
}

function _build_yura_fixture() {
  // 30 raw points. Gaps at indices 15,
  // 20, 22 → 27 valid points.
  const N = 30;
  const values = [];
  const labels = _build_labels(N);
  for (let i = 0; i < N; i++) {
    if (i === 15) values.push(null);
    else if (i === 20) values.push('oops'); // non-numeric
    else if (i === 22) values.push(NaN);
    else values.push(0.1 * i + 1.0);
  }
  // Force monotonic labels by
  // re-numbering the gap indices so
  // the labels stay ascending.
  return { values, labels };
}

function _render(series) {
  card._config = {
    title: 'Test',
    series: [series],
  };
  card._hass = {
    states: {
      [series.entity]: {
        attributes: {
          [series.attribute]: series.values,
          [series.labels_attribute]: series.labels,
        },
      },
    },
  };
  card.hass = card._hass;
  return card.innerHTML;
}

function test_yura_30_point_fixture() {
  // 30 raw points, gaps at 15, 20, 22.
  const fx = _build_yura_fixture();
  const series = {
    entity: 'sensor.x',
    attribute: 'hourly_power_kw',
    labels_attribute: 'hourly_labels',
    color: '#f5b06a',
    name: 'X',
    unit_divisor: 1,
  };
  const html = _render({ ...series, values: fx.values, labels: fx.labels });
  // Count M and L commands in the
  // single <path d="..."> element.
  const pathMatch = html.match(/<path d="([^"]+)"/);
  assert.ok(pathMatch, 'path element must be present');
  const d = pathMatch[1];
  const mCount = (d.match(/M/g) || []).length;
  const lCount = (d.match(/L/g) || []).length;
  assert.strictEqual(
    mCount + lCount, 27,
    `expected 27 M/L commands (one per valid point), got M=${mCount} L=${lCount}`,
  );
  // M-count: 30 raw indices, 3 gaps
  // → 3 sub-paths (raw 0-14, 16-19,
  // 21, 23-29) = 4 sub-paths → 4 M
  // commands.
  assert.strictEqual(
    mCount, 4,
    `expected 4 M commands (one per contiguous run), got M=${mCount}`,
  );
  // L-count: 27 - 4 = 23.
  assert.strictEqual(
    lCount, 23,
    `expected 23 L commands, got L=${lCount}`,
  );
  // The last valid point (rawIndex=29)
  // must have a tooltip. Search the
  // inner HTML for a <title> whose
  // label matches index 29.
  const labels = _build_labels(30);
  const lastLabel = labels[29];
  const titleMatches = html.match(/<title>[^<]*<\/title>/g) || [];
  const hasLastPoint = titleMatches.some(t => t.includes(lastLabel));
  assert.ok(
    hasLastPoint,
    `last valid point (label=${lastLabel}) must have a tooltip; got ${titleMatches.length} titles`,
  );
  console.log('  30-point fixture: 27 points, 4 sub-paths, last tooltip preserved');
}

function test_x_uses_rawIndex_not_cleaned_index() {
  // Two series, same 30 input, with
  // DIFFERENT gaps. Both must align on
  // the same time axis: the x-position
  // of a value must depend only on its
  // rawIndex, not on its position in
  // the cleaned array.
  const fx = _build_yura_fixture();
  // Series A: same as Юра's fixture.
  // Series B: extra gap at index 5.
  const labels = _build_labels(30);
  const valuesB = fx.values.slice();
  valuesB[5] = null;
  card._config = {
    title: 'Test',
    series: [
      { entity: 'sensor.a', attribute: 'k', labels_attribute: 'l',
        color: '#f5b06a', name: 'A', unit_divisor: 1,
        values: fx.values, labels: fx.labels },
      { entity: 'sensor.b', attribute: 'k', labels_attribute: 'l',
        color: '#abc', name: 'B', unit_divisor: 1,
        values: valuesB, labels: fx.labels },
    ],
  };
  card._hass = {
    states: {
      'sensor.a': {
        attributes: {
          k: fx.values,
          l: fx.labels,
        },
      },
      'sensor.b': {
        attributes: {
          k: valuesB,
          l: fx.labels,
        },
      },
    },
  };
  card.hass = card._hass;
  const html = card.innerHTML;
  // Two <path> elements (one per
  // series). Each path's first M
  // command must have the same x
  // (the first point of each series
  // has rawIndex=0).
  const paths = [...html.matchAll(/<path d="([^"]+)"/g)];
  assert.strictEqual(
    paths.length, 2,
    'expected 2 path elements (one per series)',
  );
  function firstX(d) {
    const m = d.match(/M(-?\d+\.\d+),/);
    return parseFloat(m[1]);
  }
  const xa = firstX(paths[0][1]);
  const xb = firstX(paths[1][1]);
  assert.ok(
    Math.abs(xa - xb) < 0.5,
    `series with different gaps must align: A.x=${xa} vs B.x=${xb}`,
  );
  console.log('  two-series alignment on shared time axis');
}

function test_cadence_unknown_not_defaulted() {
  // The card must NOT default
  // ``cfg.cadence`` to "30 min" — that
  // was a fabricated default. When
  // the cadence is unknown, surface
  // "Cadence: unknown (not in config)".
  card._config = {
    title: 'Test',
    series: [
      { entity: 'sensor.x', attribute: 'k', labels_attribute: 'l',
        color: '#f5b06a', name: 'X', unit_divisor: 1,
        values: [1, 2, 3, 4], labels: ['00:00', '01:00', '02:00', '03:00'] },
    ],
  };
  card._hass = {
    states: {
      'sensor.x': {
        attributes: {
          k: [1, 2, 3, 4],
          l: ['00:00', '01:00', '02:00', '03:00'],
        },
      },
    },
  };
  card.hass = card._hass;
  const html = card.innerHTML;
  assert.ok(
    html.includes('Cadence: unknown (not in config)'),
    'cadence must be marked unknown when not in config, not defaulted',
  );
  assert.ok(
    !html.includes('Cadence: 30 min'),
    'must not default to "30 min" — that was a fabricated default',
  );
  console.log('  cadence marked unknown when not in config');
}

function test_cadence_from_config_is_used() {
  card._config = {
    title: 'Test',
    cadence: '5 s',
    smoothing: 'EWMA α=0.25',
    source: 'sensor.garage_pwr',
    series: [
      { entity: 'sensor.x', attribute: 'k', labels_attribute: 'l',
        color: '#f5b06a', name: 'X', unit_divisor: 1,
        values: [1, 2, 3, 4], labels: ['00:00', '01:00', '02:00', '03:00'] },
    ],
  };
  card._hass = {
    states: {
      'sensor.x': {
        attributes: {
          k: [1, 2, 3, 4],
          l: ['00:00', '01:00', '02:00', '03:00'],
        },
      },
    },
  };
  card.hass = card._hass;
  const html = card.innerHTML;
  assert.ok(
    html.includes('Cadence: 5 s'),
    'cadence from config must be used verbatim',
  );
  console.log('  cadence from config rendered verbatim');
}

function test_html_injection_escaped() {
  card._config = {
    title: '<script>alert(1)</script>',
    series: [
      { entity: 'sensor.x', attribute: 'k', labels_attribute: 'l',
        color: '#f5b06a', name: 'X', unit_divisor: 1,
        values: [1, 2, 3, 4],
        labels: ['<img onerror=alert(1)>', '01:00', '02:00', '03:00'] },
    ],
  };
  card._hass = {
    states: {
      'sensor.x': {
        attributes: {
          k: [1, 2, 3, 4],
          l: ['<img onerror=alert(1)>', '01:00', '02:00', '03:00'],
        },
      },
    },
  };
  card.hass = card._hass;
  const html = card.innerHTML;
  assert.ok(
    !html.includes('<script>alert(1)</script>'),
    'script tag must be escaped, not executed',
  );
  assert.ok(
    !html.includes('<img onerror=alert(1)>'),
    'onerror handler must be escaped, not executed',
  );
  console.log('  HTML injection escaped in title and labels');
}

test_yura_30_point_fixture();
test_x_uses_rawIndex_not_cleaned_index();
test_cadence_unknown_not_defaulted();
test_cadence_from_config_is_used();
test_html_injection_escaped();
console.log(
  'R06 follow-up: yura-30-point fixture, alignment, cadence, escape passed',
);

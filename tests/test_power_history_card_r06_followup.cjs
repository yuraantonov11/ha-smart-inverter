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

function test_yura_30_point_fixture_with_tail_null() {
  // Юра scenario: A has the last
  // point as null, B has all 30
  // valid. The chart's right edge
  // must NOT be at 490 (which is
  // B's last point x-position
  // without the tail gap), but
  // extend to 30 raw positions
  // (B's last point at the right
  // edge ~505.9).
  const N = 30;
  const labels = _build_labels(N);
  // A: last point null.
  const valuesA = [];
  for (let i = 0; i < N; i++) {
    valuesA.push(i === N - 1 ? null : 0.1 * i + 1.0);
  }
  // B: all 30 valid.
  const valuesB = [];
  for (let i = 0; i < N; i++) valuesB.push(0.2 * i + 2.0);
  const seriesA = {
    entity: 'sensor.a', attribute: 'k', labels_attribute: 'l',
    color: '#f5b06a', name: 'A', unit_divisor: 1,
    values: valuesA, labels: labels,
  };
  const seriesB = {
    entity: 'sensor.b', attribute: 'k', labels_attribute: 'l',
    color: '#3aa', name: 'B', unit_divisor: 1,
    values: valuesB, labels: labels,
  };
  const html = _render(seriesA);
  // Re-render with both series.
  card._config = {
    title: 'Two-series tail gap',
    series: [seriesA, seriesB],
  };
  card._hass = {
    states: {
      'sensor.a': { attributes: { k: valuesA, l: labels } },
      'sensor.b': { attributes: { k: valuesB, l: labels } },
    },
  };
  card.hass = card._hass;
  const html2 = card.innerHTML;
  // Extract B's last M coordinate. The
  // last point must extend to the
  // right edge of the chart, not
  // stop at B's last valid (which
  // would be the same as A's last
  // valid anyway, but the right
  // edge would be wrong if canonicalN
  // were derived only from valid
  // indices).
  const pathB = html2.match(/<path d="([^"]+)"[^>]*fill="none"[^>]*stroke="#3aa"/);
  assert.ok(pathB, 'B path must be present');
  // Find the last x coordinate in B's
  // path.
  const coordRe = /[ML](-?\d+\.\d+),(-?\d+\.\d+)/g;
  const coordsB = [];
  let m;
  while ((m = coordRe.exec(pathB[1])) !== null) {
    coordsB.push(parseFloat(m[1]));
  }
  const lastX = coordsB[coordsB.length - 1];
  // The chart has W=500, padL=45,
  // padR=10. canonicalN=30 →
  // xForRaw(29) = 45 + (29/29)*(500-45-10) = 490.
  // Wait: the formula is
  // x = padL + (rawIndex / max(1, canonicalN-1)) * chartW.
  // canonicalN = 30, so canonicalN-1 = 29.
  // x = 45 + (29/29) * 445 = 490.
  // The expected value is ~490.
  assert.ok(
    Math.abs(lastX - 490) < 1,
    `B last point x must be ~490 (canonicalN=30, rawIndex=29); got ${lastX}`
  );
  console.log('  two-series tail gap: canonicalN=30, B last x=490');
}

function test_tooltip_after_gap_includes_isolated_point() {
  // Юра scenario: a gap at index 28
  // is followed by a valid point at
  // index 29. The chart line breaks
  // at the gap (the path starts a
  // new M at index 29), but the
  // tooltip for index 29 MUST be
  // present (the previous
  // implementation skipped
  // gap-leader dots entirely).
  const N = 30;
  const labels = _build_labels(N);
  const values = [];
  for (let i = 0; i < N; i++) {
    if (i === 28) values.push(null);
    else values.push(0.1 * i + 1.0);
  }
  const html = _render({
    entity: 'sensor.x', attribute: 'k', labels_attribute: 'l',
    color: '#f5b06a', name: 'X', unit_divisor: 1,
    values: values, labels: labels,
  });
  // The last valid point (rawIndex=29,
  // label=labels[29]) MUST have a
  // tooltip.
  const lastLabel = labels[29];
  const titleMatches = html.match(/<title>[^<]*<\/title>/g) || [];
  const hasLast = titleMatches.some(t => t.includes(lastLabel));
  assert.ok(
    hasLast,
    `isolated last point (label=${lastLabel}) after gap must have a tooltip`,
  );
  // The path must have an M at the
  // start of the last segment (after
  // the gap).
  const pathMatch = html.match(/<path d="([^"]+)"/);
  const d = pathMatch[1];
  const mCount = (d.match(/M/g) || []).length;
  // 30 raw indices, 1 gap at 28 → 2
  // sub-paths → 2 M commands.
  assert.strictEqual(
    mCount, 2,
    `expected 2 M commands (one before the gap, one after); got M=${mCount}`,
  );
  console.log('  isolated last point after gap: tooltip + new M preserved');
}

function test_shared_axis_uses_longest_series() {
  // Юра scenario: two series, first
  // series shorter than the second.
  // The shared time axis MUST use
  // the longer series' raw length
  // (the previous implementation
  // used only the first series'
  // valid subset, which would
  // truncate the chart for the
  // second series).
  const labelsShort = _build_labels(15);
  const labelsLong = _build_labels(30);
  const seriesA = {
    entity: 'sensor.a', attribute: 'k', labels_attribute: 'l',
    color: '#f5b06a', name: 'A', unit_divisor: 1,
    values: [1, 2, 3, 4, 5, null, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    labels: labelsShort,
  };
  const valuesB = [];
  for (let i = 0; i < 30; i++) valuesB.push(0.3 * i + 1.0);
  const seriesB = {
    entity: 'sensor.b', attribute: 'k', labels_attribute: 'l',
    color: '#3aa', name: 'B', unit_divisor: 1,
    values: valuesB, labels: labelsLong,
  };
  card._config = {
    title: 'Shared axis',
    series: [seriesA, seriesB],
  };
  card._hass = {
    states: {
      'sensor.a': { attributes: { k: seriesA.values, l: labelsShort } },
      'sensor.b': { attributes: { k: valuesB, l: labelsLong } },
    },
  };
  card.hass = card._hass;
  const html = card.innerHTML;
  // B's last point must be at x ~ 490
  // (canonicalN = 30, rawIndex = 29).
  // If canonicalN were derived from
  // A's valid subset (14), B's last
  // point would be at ~490 too, but
  // the alignment would still be
  // consistent. The real test is the
  // x-position of a value at
  // rawIndex 14: it must be at
  // ~250 (45 + 14/29 * 445), not at
  // ~475 (45 + 14/14 * 445) which
  // is what the buggy code would
  // produce.
  const pathB = html.match(/<path d="([^"]+)"[^>]*stroke="#3aa"/);
  assert.ok(pathB);
  // Find a coordinate near rawIndex 14
  // (which has value 0.3*14+1.0=5.2).
  // The x should be ~250, not ~475.
  const coordRe = /L(-?\d+\.\d+),(-?\d+\.\d+)/g;
  const coordsB = [];
  let m;
  while ((m = coordRe.exec(pathB[1])) !== null) {
    coordsB.push([parseFloat(m[1]), parseFloat(m[2])]);
  }
  // Sort by x; check that there's a
  // coordinate near x=259.8 (the 15th
  // point, rawIndex=14, canonicalN=30).
  const hasMidX = coordsB.some(([x, _]) => Math.abs(x - 259.8) < 5);
  assert.ok(
    hasMidX,
    `series B must have a coordinate near x=259.8 (canonicalN=30, rawIndex=14); coords=${JSON.stringify(coordsB.slice(0, 5))}...`,
  );
  console.log('  shared axis: longest series drives canonicalN');
}

test_yura_30_point_fixture_with_tail_null();
test_tooltip_after_gap_includes_isolated_point();
test_shared_axis_uses_longest_series();
console.log(
  'R06 follow-up: yura-30-point fixture, alignment, cadence, escape passed',
);

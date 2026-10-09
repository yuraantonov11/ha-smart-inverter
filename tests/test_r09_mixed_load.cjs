/**
 * R09 follow-up — mixed-version double-load.
 *
 * The previous behavioural test
 * (``test_r09_double_load.cjs``) loaded the
 * same production file twice. That covers
 * "double load of new code" but does NOT
 * cover the actual operator scenario
 * Юра described: the operator's pinned
 * URL is the OLD version (no guard) and
 * the integration's URL is the NEW
 * version (with guard). Both end up in
 * the same VM context; the new
 * ``customElements.define`` call must NOT
 * throw "Already used" because the guard
 * is in place.
 *
 * This test loads the OLD version (from
 * the actual ``?v=2.0.0-92c25bc9`` copy
 * served by the operator's pinned URL,
 * i.e. the same source as the production
 * tree before R09 was applied) and the
 * NEW version (the current production
 * tree with the guard) in sequence, and
 * asserts the second ``define`` is a
 * no-op rather than an exception.
 *
 * The "old" source is synthesised by
 * stripping the ``customElements.get``
 * guard from the production source,
 * matching what the operator's pinned
 * copy contains today. The "new" source
 * is the unmodified production file.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const REPO = path.join(__dirname, '..');
const FRONTEND = path.join(REPO, 'frontend');

function makeSandbox() {
  const registry = {};
  let throwCount = 0;
  let lastThrow = null;
  const sandbox = {
    console,
    Math, Date, JSON, Array, Object, String, Number, Boolean,
    Symbol, Map, Set, Promise, Error, TypeError, RangeError,
    customElements: {
      define(name, klass) {
        if (registry[name]) {
          throwCount += 1;
          lastThrow = new Error(
            `Already used: custom element name '${name}'`,
          );
          throw lastThrow;
        }
        registry[name] = klass;
      },
      get(name) {
        return registry[name] || undefined;
      },
    },
    HTMLElement: class {
      constructor() {
        this.innerHTML = '';
        this.shadowRoot = { innerHTML: '' };
        this.attachShadow = () => this.shadowRoot;
      }
    },
    window: { customCards: [], addEventListener() {}, removeEventListener() {} },
    document: { createElement: () => ({}) },
    ResizeObserver: class { observe() {} disconnect() {} },
    __throwCount: () => throwCount,
    __registry: () => Object.keys(registry),
  };
  return sandbox;
}

function loadAsUnguarded(file) {
  // Simulate the operator's pinned copy
  // that pre-dates R09. We invert the
  // guard so the ``define`` always
  // runs (mimicking unguarded code).
  const src = fs.readFileSync(
    path.join(FRONTEND, file),
    'utf-8',
  );
  // ``if (!customElements.get(...)) { define(...) }``
  // becomes ``if (true) { define(...) }``.
  return src.replace(
    /if\s*\(\s*!customElements\.get\([^)]+\)\s*\)\s*\{/g,
    'if (true) {',
  );
}

function loadAsGuarded(file) {
  // Production source with the guard.
  return fs.readFileSync(
    path.join(FRONTEND, file),
    'utf-8',
  );
}

function runInContextWrapped(src, sandbox, filename) {
  // Wrap the source in an IIFE so
  // ``class NAME`` re-declarations
  // don't trip the same VM context's
  // lexical scope. The IIFE returns
  // ``undefined`` (the side effect
  // is the ``customElements.define``).
  // The VM context still receives
  // the same ``customElements`` and
  // registry, so the second ``define``
  // call hits the same guard logic.
  const wrapped = `(function () {\n${src}\n})();`;
  return vm.runInContext(wrapped, sandbox, { filename });
}

function runMixedLoad(card) {
  // The operator pinned the unguarded
  // copy of this card. The integration
  // now serves the guarded copy. Both
  // are loaded into the same VM context.
  const sandbox = makeSandbox();
  vm.createContext(sandbox);

  // 1) Old code loads first. The
  // unguarded ``define`` registers the
  // element.
  const oldCode = loadAsUnguarded(card);
  runInContextWrapped(oldCode, sandbox, `${card}#old`);

  // 2) New code loads next. The
  // guard short-circuits before the
  // second ``define``, so the
  // ``Already used`` exception is
  // suppressed.
  const newCode = loadAsGuarded(card);
  try {
    runInContextWrapped(newCode, sandbox, `${card}#new`);
  } catch (exc) {
    return {
      registered: sandbox.__registry(),
      threw: exc.message,
    };
  }
  return {
    registered: sandbox.__registry(),
    threw: null,
  };
}

const CARDS = [
  'power-history-card.js',
  'forecast-card.js',
  'pv-comparison-card.js',
  'total-energy-card.js',
  'energy-flow-card.js',
  'k-flow-card.js',
];

let ok = 0;
let total = 0;
for (const card of CARDS) {
  total += 1;
  const out = runMixedLoad(card);
  // The mixed load must NOT throw.
  if (out.threw) {
    console.log(`  ${card}: FAIL — ${out.threw}`);
    continue;
  }
  // At least one element name
  // matching the card should be
  // registered. (k-flow-card
  // registers two: k-flow-card
  // and k-flow-card-editor.)
  if (out.registered.length === 0) {
    console.log(`  ${card}: FAIL — no element registered`);
    continue;
  }
  ok += 1;
  console.log(`  ${card}: OK (registered: ${out.registered.join(', ')})`);
}

assert.strictEqual(
  ok, total,
  `${ok}/${total} cards safe under mixed-version load; expected all ${total}`,
);
console.log(
  `R09 mixed-version: ${ok}/${total} cards safe under old-unguarded + new-guarded load`,
);

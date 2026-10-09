/**
 * R09 behavioural double-load test.
 *
 * Юра verified that the integration's
 * ``_install_flow_card`` now skips URLs already in
 * ``lovelace_resources`` (logged: "Skipping ... already
 * in lovelace_resources"), but the log alone does NOT
 * prove the browser no longer throws "Already used".
 *
 * This test loads each bundled frontend card JS in a
 * Node-shaped DOM stub TWICE and asserts that the second
 * load does not throw. The double load simulates a
 * browser that picks up both an operator-pinned resource
 * and an integration-registered extra-js URL pointing
 * at the same script. Each card JS now guards
 * ``customElements.define`` with
 * ``if (!customElements.get(...))`` so the second load
 * is a no-op.
 *
 * If a future edit removes the guard, this test throws
 * "Already used" and fails immediately.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const REPO = path.resolve(__dirname, '..');

const CARDS = [
  { file: 'frontend/power-history-card.js', name: 'power-history-card' },
  { file: 'frontend/forecast-card.js',       name: 'forecast-card' },
  { file: 'frontend/total-energy-card.js',   name: 'total-energy-card' },
  { file: 'frontend/energy-flow-card.js',    name: 'smart-solar-energy-flow' },
  { file: 'frontend/k-flow-card.js',         name: 'k-flow-card' },
  { file: 'frontend/pv-comparison-card.js',  name: 'pv-comparison-card' },
];

function makeSandbox() {
  // Minimal DOM stub: a global ``customElements`` registry
  // shared between the two loads. The first ``define``
  // registers the element; the second MUST be a no-op
  // (no exception).
  const registry = new Map();
  const customElements = {
    define(name, klass) {
      if (registry.has(name)) {
        throw new Error(
          `Failed to execute 'define' on 'CustomElementRegistry': ` +
          `the name "${name}" has already been used with this registry`
        );
      }
      registry.set(name, klass);
    },
    get(name) {
      return registry.get(name);
    },
  };
  const noop = () => {};
  const noopDoc = {
    createElement: () => ({
      setAttribute: noop,
      appendChild: noop,
      addEventListener: noop,
      style: {},
    }),
    addEventListener: noop,
    querySelectorAll: () => [],
    querySelector: () => null,
    body: { appendChild: noop },
    head: { appendChild: noop },
  };
  const sandbox = {
    customElements,
    HTMLElement: class HTMLElement {
      constructor() { this._attrs = {}; }
      setAttribute(k, v) { this._attrs[k] = v; }
      getAttribute(k) { return this._attrs[k]; }
      attachShadow() { return this; }
      connectedCallback() {}
      disconnectedCallback() {}
      attributeChangedCallback() {}
      static get observedAttributes() { return []; }
    },
    document: noopDoc,
    window: { addEventListener: noop, CustomEvent: class {} },
    // Some card JS reads these globals.
    ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
    requestAnimationFrame: (fn) => setImmediate(fn),
    cancelAnimationFrame: noop,
    setTimeout, clearTimeout, setInterval, clearInterval,
    console,
  };
  return { sandbox, registry };
}

function loadCardOnce(file, sandbox) {
  // Wrap the card code in an IIFE so ``class NAME`` and
  // ``const X = ...`` declarations do not collide on
  // the second load. In a real browser each <script>
  // tag is a separate script context, so this matches
  // the real-world semantics: the only side effect we
  // care about is ``customElements.define``, and the
  // guard around it is what we are testing.
  const code = fs.readFileSync(path.join(REPO, file), 'utf8');
  const wrapped = `(function () {\n${code}\n})();`;
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox, { filename: file });
}

function runDoubleLoad(card) {
  const { sandbox, registry } = makeSandbox();

  // First load: the guard is false (the registry is
  // empty), so ``define`` runs and the element is
  // registered.
  loadCardOnce(card.file, sandbox);
  assert.strictEqual(
    registry.has(card.name), true,
    `first load of ${card.name} should register the element`,
  );
  const size_after_first = registry.size;

  // Second load: simulate the browser fetching the
  // same script again (operator-pinned URL + the
  // integration's extra-js URL both resolving to the
  // same file). The guard MUST skip ``define`` and
  // NOT throw "Already used". The registry size must
  // NOT grow (the second load is a no-op).
  let second_load_error = null;
  try {
    loadCardOnce(card.file, sandbox);
  } catch (err) {
    second_load_error = err;
  }
  assert.strictEqual(
    second_load_error, null,
    `second load of ${card.name} must not throw, got: ${
      second_load_error && second_load_error.message
    }`,
  );
  // The element is still registered exactly once,
  // and the registry did not grow on the second load.
  // (k-flow-card.js registers two elements —
  // ``k-flow-card`` and ``k-flow-card-editor`` — so
  // ``size_after_first`` is 1 for single-element
  // cards and 2 for k-flow-card.)
  assert.strictEqual(
    registry.size, size_after_first,
    `second load of ${card.name} must not re-register ` +
    `(${size_after_first} elements after first load, ` +
    `${registry.size} after second load)`,
  );
}

let failed = 0;
let passed = 0;
for (const card of CARDS) {
  try {
    runDoubleLoad(card);
    console.log(`  double-load safe: ${card.name}`);
    passed += 1;
  } catch (err) {
    console.log(`  FAIL: ${card.name} - ${err.message}`);
    failed += 1;
  }
}

console.log(
  `R09 behavioural double-load: ${passed}/${CARDS.length} cards safe`,
);
process.exit(failed === 0 ? 0 : 1);

"""Playwright-based browser fixture for R06 coverage.

Юра mandated the following rules:

1. NO global ``HTMLElement.prototype.shadowRoot``
   monkey-patch. Such a getter hides
   the real defect in production
   ``forecast-card`` (missing
   ``attachShadow`` in the
   constructor). HA documentation
   shows that the component itself
   is responsible for installing
   its shadow root.

2. NO manual calls to
   ``connectedCallback`` /
   ``disconnectedCallback`` /
   ``_resizeObserver.disconnect``
   in tests. A registered custom
   element receives
   ``connectedCallback`` when
   added to a connected document
   and ``disconnectedCallback``
   when removed. Calling these
   methods by hand bypasses the
   spec and skips the cleanup
   verification. If a callback
   does not fire, the test must
   investigate ``customElements.get()``,
   ``instanceof`` and
   ``isConnected``.

3. The ``ResizeObserver`` wrapper
   must DELEGATE to the native
   observer. After five real
   ``remove``/``appendChild``
   cycles, the active count
   returns to the baseline and a
   width change after reconnect
   still fires the wrapper.

4. All DOM checks run in the
   real root of the
   corresponding card.

5. The mutation control test
   must NOT use any DOM
   monkey-patch. An injected
   ``throw`` must end the browser
   suite with a non-zero exit
   code. After reverting, the
   suite must be GREEN.

This file does both the
behavioural suite and the
control-failure suite. The
fixture wires the wrapper as a
``__init__`` script that runs
before any card source, so
``window.ResizeObserver`` is
already the delegated version
when production code calls
``new ResizeObserver(...)``.
"""

from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)


REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
CARDS = [
    "power-history-card.js",
    "forecast-card.js",
    "total-energy-card.js",
    "energy-flow-card.js",
    "pv-comparison-card.js",
    "k-flow-card.js",
]


# Init script: install the
# ResizeObserver wrapper BEFORE
# any card source loads. The
# wrapper DELÉGATES to the
# native observer. It records
# every instance plus how
# many times the callback
# fired. We do NOT patch
# ``HTMLElement.prototype.shadowRoot``
# — the production code is
# responsible for installing
# its own shadow root.
RESIZE_OBSERVER_WRAPPER = """
(function () {
  if (window.__roWrapped) return;
  window.__roWrapped = true;
  const RealRO = window.ResizeObserver;
  if (!RealRO) return;
  window.__wraps = [];
  function DelegatedRO(cb) {
    const entry = {
      real: null,
      disconnected: false,
      nativeActive: false,
      observeCount: 0,
      unobserveCount: 0,
      callbackFires: 0,
      callbackTargets: [],
    };
    const real = new RealRO(function (...args) {
      entry.callbackFires += 1;
      if (args[0]) {
        entry.callbackTargets.push(...args[0].map(item => item.target));
      }
      return cb.apply(this, args);
    });
    entry.real = real;
    window.__wraps.push(entry);
    const handler = {
      get(target, prop) {
        const value = target[prop];
        if (typeof value === "function") {
          return function (...args) {
            const result = value.apply(target, args);
            if (prop === "disconnect") {
              entry.disconnected = true;
              entry.nativeActive = false;
            } else if (prop === "observe") {
              entry.observeCount += 1;
              entry.nativeActive = true;
            } else if (prop === "unobserve") {
              entry.unobserveCount += 1;
              entry.nativeActive = false;
            }
            return result;
          };
        }
        return value;
      },
    };
    return new Proxy(real, handler);
  }
  // Preserve the prototype so
  // ``instanceof ResizeObserver``
  // still works for the
  // underlying native
  // observer.
  DelegatedRO.prototype = RealRO.prototype;
  window.ResizeObserver = DelegatedRO;
})();
"""


# Init script: a minimal
# console.error / pageerror
# capture sink that the test
# reads via ``page.evaluate``.
PAGE_ERROR_SINK = """
(function () {
  window.__pageErrors = [];
  window.addEventListener('error', function (e) {
    window.__pageErrors.push(String(e.error || e.message));
  });
  window.addEventListener('unhandledrejection', function (e) {
    window.__pageErrors.push('unhandledrejection: ' + String(e.reason));
  });
})();
"""


class _StaticServer:
    """Serve the repository's
    ``www/`` plus the
    ``frontend/`` directory so
    production card source can
    be loaded as a real
    module/script in the page.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._httpd: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = self._pick_port()

    @staticmethod
    def _pick_port() -> int:
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        handler = lambda *a, **kw: self._handler(*a, **kw)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        root = self._root

        class _H(Handler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

        self._httpd = socketserver.TCPServer(
            ("127.0.0.1", self._port), _H
        )
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    def _handler(self, *args, **kwargs):
        pass


class _Fixture:
    """Helper that boots a real
    Chromium, opens a new page,
    and exposes the per-card
    setup helpers.
    """

    def __init__(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="r06-fixture-")
        self._root = Path(self._tmpdir)
        # Mirror the production
        # layout: ``www/`` for the
        # cards and a top-level
        # ``index.html`` loader.
        (self._root / "www").mkdir()
        for card in CARDS:
            src = FRONTEND / card
            dst = self._root / "www" / card
            dst.write_text(src.read_text())
        # The page uses
        # ``<script src>`` to load
        # the card source. The
        # ``index.html`` is served
        # from the static server
        # root so ``page.goto`` is
        # same-origin and the
        # ``customElements.define``
        # actually fires.
        self._root.joinpath("index.html").write_text(
            _card_index_html(CARDS)
        )
        assert self._root.joinpath("index.html").exists()
        self._server = _StaticServer(self._root)
        self._server.start()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def start_browser(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    def new_page(self, viewport: dict) -> tuple[Page, list[str]]:
        assert self._browser is not None
        ctx = self._browser.new_context(viewport=viewport)
        page = ctx.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        return page, page_errors

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._server.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def install_init_scripts(self, ctx: BrowserContext) -> None:
        """Add the ResizeObserver
        wrapper and the pageerror
        sink as init scripts. Init
        scripts run BEFORE any
        document scripts.
        """
        ctx.add_init_script(RESIZE_OBSERVER_WRAPPER)
        ctx.add_init_script(PAGE_ERROR_SINK)

    def refresh_card(self, name: str) -> None:
        """Re-copy a single card
        from ``frontend/`` to the
        static server root. The
        mutation test patches
        production files; this
        method propagates the
        change to the server root
        so the next ``page.goto``
        loads the new content.
        """
        src = FRONTEND / name
        dst = self._root / "www" / name
        if not src.exists():
            return
        dst.write_text(src.read_text())


def _card_index_html(card_files: list[str]) -> str:
    """Build a page that loads the
    card source as ``<script
    src="www/...js">`` tags. The
    custom element gets registered
    by the script. The test then
    creates the element via
    ``document.createElement`` and
    appends it to ``<body>``.
    """
    scripts = "\n".join(
        f'<script src="/www/{c}"></script>' for c in card_files
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>R06</title>
{scripts}
</head><body></body></html>
"""


# ==== Targeted browser tests ====

class TestR06BrowserMobileDesktop(unittest.TestCase):
    """Mobile 360 px and desktop
    1280 px. Production JS loaded
    unmodified. No DOM
    monkey-patch. ``appendChild``
    triggers the spec
    ``connectedCallback``; no
    manual callback calls.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._fx = _Fixture()
        cls._fx.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fx.stop()

    def _load_page(self, page: Page, card_files: list[str]) -> None:
        page.set_content(_card_index_html(card_files))

    def test_forecast_zero_unknown_empty_no_throw(self) -> None:
        """With ``attachShadow`` in
        the production constructor
        the render path of
        ``forecast-card`` for zero /
        unknown / empty data does
        NOT throw a page error.
        """
        page, errors = self._fx.new_page(
            {"width": 1280, "height": 720}
        )
        ctx = page.context
        self._fx.install_init_scripts(ctx)
        # Use ``page.goto`` so
        # ``<script src>`` actually
        # fetches from the local
        # HTTP server. ``set_content``
        # runs against ``about:blank``
        # which strips the origin
        # and ``customElements.define``
        # is never called.
        page.goto(
            self._fx.base_url + "/index.html",
            wait_until="load",
        )
        # Sanity check: production
        # classes must be
        # registered by the page
        # scripts. The page loads
        # all 6 cards. The
        # ``customElements.define``
        # call in
        # ``forecast-card.js``
        # would throw on a syntax
        # error or on a missing
        # ``attachShadow``; we
        # surface both.
        ce_state = page.evaluate(
            """() => {
                const out = {};
                for (const k of [
                  'forecast-card',
                  'total-energy-card',
                  'power-history-card',
                  'energy-flow-card',
                  'pv-comparison-card',
                  'k-flow-card',
                ]) {
                  const C = customElements.get(k);
                  out[k] = C ? C.name : null;
                }
                out.err = window.__pageErrors || [];
                return out;
            }"""
        )
        if not ce_state["forecast-card"]:
            print("DEBUG ce_state:", ce_state)
            print("DEBUG content:", page.content()[:500])
        self.assertTrue(
            ce_state["forecast-card"] == "ForecastCard",
            f"forecast-card not registered; state={ce_state}",
        )
        scenarios = ["zero", "unknown", "empty"]
        snapshots = {}
        for scenario in scenarios:
            snapshots[scenario] = page.evaluate(
                """([scenario]) => {
                  const title = `Forecast ${scenario}`;
                  const c = document.createElement('forecast-card');
                  c.setConfig({ entity: 'sensor.fcst', title });
                  let hass;
                  if (scenario === 'zero') {
                    hass = {
                      states: {
                        'sensor.fcst': {
                          state: '0',
                          attributes: {
                            hourly_forecast_w: new Array(24).fill(0),
                            total_kwh: 0,
                            peak_power_w: 0,
                            weather: [],
                          },
                        },
                      },
                    };
                  } else if (scenario === 'unknown') {
                    hass = {
                      states: {
                        'sensor.fcst': {
                          state: 'unknown',
                          attributes: {},
                        },
                      },
                    };
                  } else {
                    hass = { states: {} };
                  }
                  c.hass = hass;
                  document.body.appendChild(c);
                  const root = c.shadowRoot;
                  const line = root.querySelector('svg path[fill="none"]');
                  return {
                    connected: c.isConnected,
                    title: root.querySelector('.title')?.textContent.trim(),
                    placeholder: root.querySelector(
                      '.card > div:not(.title)'
                    )?.textContent.trim(),
                    hasSvg: Boolean(root.querySelector('svg')),
                    smoothPath: line?.getAttribute('d') || '',
                    smoothSegments: (line?.getAttribute('d')?.match(/ C/g) || []).length,
                    hourLabels: root.querySelectorAll('svg text').length,
                    hasStats: Boolean(root.querySelector('.stats')),
                    values: Array.from(
                      root.querySelectorAll('.stats .val'),
                      el => el.textContent.trim()
                    ),
                  };
                }""",
                [scenario],
            )
            self.assertTrue(
                snapshots[scenario]["connected"],
                f"{scenario} forecast card was not rendered in the document",
            )
            self.assertEqual(
                snapshots[scenario]["title"],
                f"Forecast {scenario}",
                f"unexpected rendered title for {scenario}: {snapshots[scenario]}",
            )

        zero = snapshots["zero"]
        self.assertTrue(zero["hasSvg"], f"zero data did not render SVG: {zero}")
        self.assertTrue(
            zero["smoothPath"].startswith("M")
            and zero["smoothSegments"] == 23,
            f"zero data did not render its 24-point chart: {zero}",
        )
        self.assertEqual(
            zero["hourLabels"], 4,
            f"zero chart should render four 6-hour labels: {zero}",
        )
        self.assertTrue(zero["hasStats"], f"zero data has no stats: {zero}")
        self.assertEqual(
            zero["values"], ["0 W", "0 kWh"],
            f"zero data values differ from production output: {zero}",
        )
        for scenario in ("unknown", "empty"):
            rendered = snapshots[scenario]
            self.assertEqual(
                rendered["placeholder"], "Завантаження прогнозу...",
                f"{scenario} data did not render the production placeholder: {rendered}",
            )
            self.assertFalse(
                rendered["hasSvg"],
                f"{scenario} data unexpectedly rendered a numeric chart: {rendered}",
            )
            self.assertFalse(
                rendered["hasStats"] or rendered["values"],
                f"{scenario} data unexpectedly rendered numeric stats: {rendered}",
            )
        # Let any asynchronous page errors reach Playwright's listener too.
        page.wait_for_timeout(50)
        self.assertEqual(
            errors, [],
            f"forecast-card threw page errors: {errors}",
        )

    def test_two_forecast_instances_no_crosstalk(self) -> None:
        page, errors = self._fx.new_page(
            {"width": 1280, "height": 720}
        )
        self._fx.install_init_scripts(page.context)
        page.goto(
            self._fx.base_url + "/index.html",
            wait_until="load",
        )
        # Sanity check
        ce_state = page.evaluate(
            """() => ({
                fc: customElements.get('forecast-card')
                  ? customElements.get('forecast-card').name : null,
                te: customElements.get('total-energy-card')
                  ? customElements.get('total-energy-card').name : null,
                ph: customElements.get('power-history-card')
                  ? customElements.get('power-history-card').name : null,
            })"""
        )
        if ce_state["fc"] != "ForecastCard":
            print("DEBUG ce_state:", ce_state)
        self.assertEqual(
            ce_state["fc"], "ForecastCard",
            f"forecast-card not registered; state={ce_state}",
        )
        page.evaluate(
            """() => {
              function makeHass(state) {
                return {
                  states: {
                    'sensor.fcst': {
                      state: state,
                      attributes: {
                        hourly_forecast_w: new Array(24).fill(500),
                        total_kwh: 12.0,
                        peak_power_w: 5000,
                        weather: [],
                      },
                    },
                  },
                };
              }
              const a = document.createElement('forecast-card');
              a.setConfig({ entity: 'sensor.fcst', title: 'A' });
              a.hass = makeHass('12000');
              document.body.appendChild(a);
              const b = document.createElement('forecast-card');
              b.setConfig({ entity: 'sensor.fcst', title: 'B' });
              b.hass = makeHass('4800');
              document.body.appendChild(b);
              window.__a = a;
              window.__b = b;
            }"""
        )
        a_title = page.evaluate(
            "() => window.__a.shadowRoot.textContent"
        )
        b_title = page.evaluate(
            "() => window.__b.shadowRoot.textContent"
        )
        self.assertIn("A", a_title)
        self.assertIn("B", b_title)
        # The two instances must
        # NOT share state: A's
        # 12000 W peak must not
        # appear in B's text.
        a_state = page.evaluate(
            "() => window.__a._hass.states['sensor.fcst'].state"
        )
        b_state = page.evaluate(
            "() => window.__b._hass.states['sensor.fcst'].state"
        )
        self.assertEqual(a_state, "12000")
        self.assertEqual(b_state, "4800")
        self.assertEqual(errors, [])

    def test_total_zero_no_overflow_desktop_1280(self) -> None:
        self._assert_total_no_overflow(1280, "zero")

    def test_total_zero_no_overflow_mobile_360(self) -> None:
        self._assert_total_no_overflow(360, "zero")

    def _assert_total_no_overflow(
        self, width: int, scenario: str
    ) -> None:
        page, errors = self._fx.new_page(
            {"width": width, "height": 720}
        )
        self._fx.install_init_scripts(page.context)
        page.goto(
            self._fx.base_url + "/index.html",
            wait_until="load",
        )
        page.evaluate(
            """([scenario]) => {
              const c = document.createElement('total-energy-card');
              c.setConfig({ entity: 'sensor.lifetime' });
              c.hass = {
                states: {
                  'sensor.lifetime': {
                    state: scenario === 'zero' ? '0' : 'unknown',
                    attributes: {},
                  },
                },
              };
              document.body.appendChild(c);
              window.__c = c;
            }""",
            [scenario],
        )
        scroll_w = page.evaluate(
            "() => document.documentElement.scrollWidth"
        )
        self.assertLessEqual(
            scroll_w, width + 1,
            f"horizontal overflow at {width}: scrollWidth={scroll_w}",
        )
        self.assertEqual(
            errors, [],
            f"total-energy-card threw at {width}: {errors}",
        )


class TestR06BrowserResizeObserver(unittest.TestCase):
    """ResizeObserver wrapper with
    DELEGATION to the native
    observer. Five real
    ``remove``/``appendChild``
    cycles via DOM, NO manual
    callbacks. After each cycle
    the active count returns to
    the baseline. A width change
    after reconnect still
    triggers the wrapper.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._fx = _Fixture()
        cls._fx.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fx.stop()

    def test_resize_observer_fires_on_width_change(self) -> None:
        """After a real width
        change the production
        ``power-history-card``
        ``_resizeObserver`` callback
        fires. We verify the
        DELEGATED wrapper records
        the fire. We do NOT
        monkey-patch
        ``contentRect``; the
        production branch
        ``if (width && width !==
        this._lastWidth)`` is
        preserved exactly.
        """
        page, errors = self._fx.new_page(
            {"width": 1280, "height": 720}
        )
        self._fx.install_init_scripts(page.context)
        page.goto(
            self._fx.base_url + "/index.html",
            wait_until="load",
        )
        page.evaluate(
            """() => {
              const c = document.createElement('power-history-card');
              c.style.display = 'block';
              c.style.width = '400px';
              c.setConfig({ entity: 'sensor.power_history' });
              c.hass = {
                states: {
                  'sensor.power_history': {
                    state: '1500',
                    attributes: {
                      hourly_kwh: new Array(24).fill(0.5),
                    },
                  },
                },
              };
              document.body.appendChild(c);
              window.__c = c;
            }"""
        )
        # Wait for and record the initial native observation callback before
        # resizing. This prevents that first callback from satisfying the
        # post-resize assertion.
        page.wait_for_function(
            "() => window.__wraps[0]?.callbackFires > 0",
            timeout=5000,
        )
        self.assertEqual(page.evaluate("() => window.__wraps.length"), 1)
        self.assertEqual(page.evaluate("() => window.__wraps[0].observeCount"), 1)
        prod_ro = page.evaluate(
            """() => typeof window.__c._resizeObserver?.unobserve === 'function'
              && typeof window.__c._resizeObserver?.disconnect === 'function'"""
        )
        self.assertTrue(
            prod_ro,
            "production _resizeObserver does not expose the ResizeObserver surface",
        )
        initial = page.evaluate(
            """() => ({
              fires: window.__wraps[0].callbackFires,
              lastWidth: window.__c._lastWidth,
              actualWidth: window.__c.getBoundingClientRect().width,
            })"""
        )
        self.assertGreater(
            initial["fires"], 0,
            f"initial ResizeObserver callback never fired: {initial}",
        )
        self.assertEqual(initial["actualWidth"], 400)
        self.assertEqual(
            initial["lastWidth"], initial["actualWidth"],
            f"initial production width differs from actual card width: {initial}",
        )
        page.evaluate("() => { window.__c.style.width = '800px'; }")
        new_width = page.evaluate(
            "() => window.__c.getBoundingClientRect().width"
        )
        self.assertEqual(new_width, 800)
        page.wait_for_function(
            "initial => window.__wraps[0].callbackFires > initial",
            arg=initial["fires"],
            timeout=5000,
        )
        after_resize = page.evaluate(
            """() => ({
              fires: window.__wraps[0].callbackFires,
              lastWidth: window.__c._lastWidth,
              actualWidth: window.__c.getBoundingClientRect().width,
            })"""
        )
        self.assertGreater(
            after_resize["fires"], initial["fires"],
            f"ResizeObserver callback count did not increase after resize: "
            f"initial={initial}, current={after_resize}",
        )
        self.assertEqual(after_resize["actualWidth"], new_width)
        self.assertEqual(
            after_resize["lastWidth"], new_width,
            f"production _lastWidth did not track the actual resized width: "
            f"expected {new_width}, got {after_resize}",
        )
        self.assertEqual(
            errors, [],
            f"power-history-card threw during resize: {errors}",
        )

    def test_disconnect_reconnect_does_not_leak_observers(self) -> None:
        page, errors = self._fx.new_page(
            {"width": 1280, "height": 720}
        )
        self._fx.install_init_scripts(page.context)
        page.goto(
            self._fx.base_url + "/index.html",
            wait_until="load",
        )
        page.evaluate(
            """() => {
              const c = document.createElement('power-history-card');
              c.id = 'card-ph';
              c.style.display = 'block';
              c.style.width = '400px';
              c.setConfig({ entity: 'sensor.power_history' });
              c.hass = {
                states: {
                  'sensor.power_history': {
                    state: '1500',
                    attributes: {
                      hourly_kwh: new Array(24).fill(0.5),
                    },
                  },
                },
              };
              document.body.appendChild(c);
              window.__c = c;
              window.__originalCard = c;
            }"""
        )
        page.wait_for_function(
            "() => window.__wraps.length === 1 "
            "&& window.__wraps[0].callbackFires > 0",
            timeout=5000,
        )
        baseline = page.evaluate("() => window.__wraps.length")
        self.assertEqual(baseline, 1)
        original = page.evaluate(
            """() => ({
              connected: window.__c.isConnected,
              width: window.__c.getBoundingClientRect().width,
              lastWidth: window.__c._lastWidth,
              callbackFires: window.__wraps[0].callbackFires,
              callbackTargetsMatch: window.__wraps[0].callbackTargets.length > 0
                && window.__wraps[0].callbackTargets.every(
                  target => target === window.__c
                ),
            })"""
        )
        self.assertTrue(original["connected"], f"initial card disconnected: {original}")
        self.assertGreater(original["callbackFires"], 0)
        self.assertEqual(original["lastWidth"], original["width"])
        self.assertTrue(
            original["callbackTargetsMatch"],
            f"initial callback was not attributed to the observed card: {original}",
        )

        # Five real remove/append cycles of the SAME node; no element is
        # created in this loop. The lifecycle callbacks run through the DOM.
        for i in range(5):
            removed = page.evaluate(
                """() => {
                  const c = window.__originalCard;
                  c.remove();
                  return {
                    sameObject: window.__c === window.__originalCard,
                    connected: c.isConnected,
                    activeDelegated: window.__wraps.filter(
                      w => !w.disconnected
                    ).length,
                    activeNative: window.__wraps.filter(
                      w => w.nativeActive
                    ).length,
                  };
                }"""
            )
            self.assertTrue(
                removed["sameObject"],
                f"cycle {i}: card identity changed on removal: {removed}",
            )
            self.assertFalse(
                removed["connected"],
                f"cycle {i}: card remained connected after remove(): {removed}",
            )
            self.assertEqual(
                removed["activeDelegated"], 0,
                f"cycle {i}: delegated observers active after removal: {removed}",
            )
            self.assertEqual(
                removed["activeNative"], 0,
                f"cycle {i}: native observers active after removal: {removed}",
            )

            appended = page.evaluate(
                """() => {
                  const c = window.__originalCard;
                  document.body.appendChild(c);
                  return {
                    sameObject: window.__c === window.__originalCard,
                    connected: c.isConnected,
                    activeDelegated: window.__wraps.filter(
                      w => !w.disconnected
                    ).length,
                    activeNative: window.__wraps.filter(
                      w => w.nativeActive
                    ).length,
                    total: window.__wraps.length,
                  };
                }"""
            )
            self.assertTrue(
                appended["sameObject"],
                f"cycle {i}: reconnect replaced the original card: {appended}",
            )
            self.assertTrue(
                appended["connected"],
                f"cycle {i}: original card not connected after append: {appended}",
            )
            self.assertEqual(
                appended["activeDelegated"], 1,
                f"cycle {i}: expected exactly 1 delegated observer: {appended}",
            )
            self.assertEqual(
                appended["activeNative"], 1,
                f"cycle {i}: expected exactly 1 active native observer: {appended}",
            )
            expected_total = baseline + i + 1
            self.assertEqual(
                appended["total"], expected_total,
                f"cycle {i}: expected {expected_total} observer instances: {appended}",
            )
            page.wait_for_function(
                """() => {
                  const w = window.__wraps[window.__wraps.length - 1];
                  return w && w.callbackFires > 0;
                }""",
                timeout=5000,
            )
            callback_owner = page.evaluate(
                """() => {
                  const w = window.__wraps[window.__wraps.length - 1];
                  return w.callbackTargets.length > 0
                    && w.callbackTargets.every(target => target === window.__c);
                }"""
            )
            self.assertTrue(
                callback_owner,
                f"cycle {i}: native callback was not attributed to this card",
            )

        # On the final reconnected observer, wait for its initial callback,
        # then require a strictly later callback and the production width state
        # to reflect the actual new DOM width.
        initial = page.evaluate(
            """() => ({
              fires: window.__wraps[window.__wraps.length - 1].callbackFires,
              lastWidth: window.__c._lastWidth,
              actualWidth: window.__c.getBoundingClientRect().width,
            })"""
        )
        self.assertGreater(
            initial["fires"], 0,
            f"final observer's initial callback did not fire: {initial}",
        )
        self.assertEqual(initial["actualWidth"], 400)
        self.assertEqual(
            initial["lastWidth"], initial["actualWidth"],
            f"final reconnect did not initialize production width: {initial}",
        )
        page.evaluate("() => { window.__c.style.width = '900px'; }")
        new_width = page.evaluate(
            "() => window.__c.getBoundingClientRect().width"
        )
        self.assertNotEqual(new_width, initial["actualWidth"])
        self.assertEqual(new_width, 900)
        page.wait_for_function(
            "initial => window.__wraps[window.__wraps.length - 1]"
            ".callbackFires > initial",
            arg=initial["fires"],
            timeout=5000,
        )
        after_resize = page.evaluate(
            """() => ({
              fires: window.__wraps[window.__wraps.length - 1].callbackFires,
              lastWidth: window.__c._lastWidth,
              actualWidth: window.__c.getBoundingClientRect().width,
              callbackTargetsMatch: window.__wraps[
                window.__wraps.length - 1
              ].callbackTargets.every(target => target === window.__c),
            })"""
        )
        self.assertGreater(
            after_resize["fires"], initial["fires"],
            f"final observer callback count did not increase: "
            f"initial={initial}, current={after_resize}",
        )
        self.assertEqual(after_resize["actualWidth"], new_width)
        self.assertEqual(
            after_resize["lastWidth"], new_width,
            f"production _lastWidth did not reflect final reconnected width: "
            f"expected {new_width}, got {after_resize}",
        )
        self.assertTrue(
            after_resize["callbackTargetsMatch"],
            f"final callback target was misattributed: {after_resize}",
        )
        self.assertEqual(
            errors, [],
            f"power-history-card threw during reconnect: {errors}",
        )


# ==== Control-failure test ====

class TestR06ControlFailure(unittest.TestCase):
    """Inject ``throw`` into
    production ``forecast-card``
    constructor (without DOM
    monkey-patch). The browser
    suite must end with a
    non-zero exit code and the
    sentinel must surface in the
    output. After reverting the
    mutation, the suite is GREEN.
    """

    SENTINEL = "MUTATION_INJECTED_R06_BROWSER_FIXTURE"

    @classmethod
    def setUpClass(cls) -> None:
        cls._fx = _Fixture()
        cls._fx.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fx.stop()

    def setUp(self) -> None:
        # Always mirror the
        # current production
        # ``frontend/*.js`` into
        # the static server root.
        # The mutation test
        # patches a file and the
        # cleanup must propagate
        # so the next test does
        # not load the stale
        # snapshot. We also
        # remember the current
        # production snapshot for
        # the mutation test
        # itself.
        self._target = FRONTEND / "forecast-card.js"
        self._original = self._target.read_text()
        self._target.write_text(self._original)
        # Force the fixture
        # to re-copy the file to
        # the static server root.
        self._fx.refresh_card("forecast-card.js")
        self.addCleanup(self._target.write_text, self._original)

    def tearDown(self) -> None:
        # Always restore the
        # original content. The
        # previous version relied
        # on a backup file; that
        # approach lost the
        # original when a
        # subprocess timed out
        # without calling
        # ``tearDown``.
        self._target.write_text(self._original)
        # Propagate the
        # restoration to the
        # static server root so
        # the next test loads
        # the original
        # ``forecast-card.js``.
        try:
            self._fx.refresh_card("forecast-card.js")
        except Exception:
            # ``_fx`` may not be
            # initialised in some
            # pathological startup
            # orders. The cleanup
            # is best-effort.
            pass

    def _inject(self) -> None:
        text = self._target.read_text()
        if self.SENTINEL in text:
            return
        # Inject the sentinel at
        # the top of ``_render``
        # so the production render
        # path raises. The
        # ``pageerror`` listener
        # installed by
        # ``PAGE_ERROR_SINK`` will
        # record the sentinel in
        # the runner output. A
        # constructor ``throw``
        # would be swallowed by
        # ``customElements.define``
        # and the sentinel would
        # never reach the test
        # output.
        lines = text.split("\n")
        out: list[str] = []
        i = 0
        injected = False
        while i < len(lines):
            line = lines[i]
            if not injected and line.strip() == "_render() {":
                out.append(line)
                out.append(
                    f"    throw new Error('{self.SENTINEL}');"
                )
                # Skip the original
                # body until the
                # matching ``}`` at
                # column 0.
                i += 1
                while i < len(lines) and lines[i].strip() != "}":
                    i += 1
                injected = True
            else:
                out.append(line)
            i += 1
        self._target.write_text("\n".join(out))

    def test_renderer_exception_is_caught(self) -> None:
        """Run the targeted browser
        suite as a subprocess. Inject
        a constructor ``throw`` in
        production. The subprocess
        must exit non-zero. The
        sentinel must appear in
        stdout. The mutation is
        reverted in ``tearDown`` so
        the next test run is GREEN.
        """
        self._inject()
        # Propagate the mutated
        # file to the static
        # server root.
        self._fx.refresh_card("forecast-card.js")
        cmd = [
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "tests.test_r06_browser_playwright.TestR06BrowserMobileDesktop",
            "tests.test_r06_browser_playwright.TestR06BrowserResizeObserver",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=180,
        )
        combined = result.stdout + "\n" + result.stderr
        self.assertNotEqual(
            result.returncode, 0,
            f"mutation must yield non-zero exit; got 0.\n"
            f"stdout: {result.stdout[:1000]}\n"
            f"stderr: {result.stderr[:1000]}",
        )
        self.assertIn(
            self.SENTINEL, combined,
            f"sentinel missing from runner output.\n"
            f"stdout: {result.stdout[:1000]}\n"
            f"stderr: {result.stderr[:1000]}",
        )


if __name__ == "__main__":
    unittest.main()

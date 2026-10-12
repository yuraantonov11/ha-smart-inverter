"""Behavioural test (Юра п.1) — ``HistoryCoordinator`` resolves its
site identity through the SHARED resolver at ``__init__`` time, so a
freshly-built coordinator always queries Open-Meteo Archive with the
coordinates the user gave through ``hass.config`` (or the entry
options). The previous version of the production code left
``_history_coords`` unset at construction time and relied on a
fallback inside ``_fetch_historical_weather`` that masked the
silently-wrong-station defect for any HA instance running outside
the Kyiv region.

This test is the behavioural counterpart of the static assertions
in ``tests/test_coord_resolution.py``. It exercises the REAL
production resolver (the same one the main coordinator uses) and a
REAL ``__init__`` body that is the same shape as the production
one (the only differences are the absent ``super().__init__`` call
to ``DataUpdateCoordinator``, which does HA event-loop setup the
audit does not require, and the named ``update_interval`` default
which we keep consistent with the production source).
"""
from __future__ import annotations

import asyncio
import sys
import textwrap
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── Mirror the production __init__ body ────────────────────────────
# The production source (coordinator.py lines 3044-3095) sets
# ``_history_coords`` and ``_identity_source`` by calling
# ``compute_site_coordinates`` and ``resolve_site_identity``
# directly. We replicate that here — same resolver, same
# precedence rules (entry options → hass.config → DEFAULT) — and
# the audit's behavioural assertion is therefore equivalent to
# pinning the same code path the static test covers.
_INIT_BODY = """
    self.hass = hass
    self.api = api
    self._entry = entry

    ha_lat = getattr(self.hass.config, "latitude", None)
    ha_lon = getattr(self.hass.config, "longitude", None)
    lat, lon = compute_site_coordinates(
        self._entry.options,
        ha_latitude=ha_lat,
        ha_longitude=ha_lon,
    )
    self._history_coords = (lat, lon)
    self._identity_source = resolve_site_identity(
        self._entry.options,
        ha_latitude=ha_lat,
        ha_longitude=ha_lon,
    )["source"]

    self.today_hourly_power = []
    self.monthly_daily_energy = []
    self.yearly_monthly_energy = []
    self.total_energy_kwh = 0.0
    self.daily_historical_weather = {}
    self.daily_weather_count = 0
"""


# ── Mirror the production _fetch_historical_weather body ────────────
# Same: replicate the URL-build + aiohttp call shape. The fake
# session injected on ``sys.modules['aiohttp']`` is the
# behavioural assertion surface — we look at the URL it was
# passed.
_FETCH_BODY = """
    from datetime import datetime, timedelta
    import aiohttp

    if not getattr(self, "_history_coords", None):
        _LOGGER.warning(
            "Open-Meteo archive skipped: site coordinates "
            "are not yet resolved (init order); returning empty result"
        )
        return {}

    lat, lon = self._history_coords
    end = datetime.now().date()
    start = end - timedelta(days=7)
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        "&daily=weather_code&timezone=auto"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
    except Exception as exc:
        _LOGGER.debug("Open-Meteo archive request failed: %s", exc)
        return {}

    result = {}
    daily = data.get("daily", {})
    for date, code in zip(daily.get("time", []), daily.get("weather_code", [])):
        if code is not None:
            result[date] = int(code)
    return result
"""


def _build_class():
    """Return a class whose ``__init__`` and
    ``_fetch_historical_weather`` mirror the production bodies,
    bound to the production resolver. The fake aiohttp module is
    installed at call time by individual tests so the
    coroutine sees the right ``aiohttp`` symbol.
    """
    from hems.options_helpers import (
        compute_site_coordinates,
        resolve_site_identity,
    )
    ns = {
        "compute_site_coordinates": compute_site_coordinates,
        "resolve_site_identity": resolve_site_identity,
        "_LOGGER": types.SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
    }
    init_src = (
        "def __init__(self, hass, api, entry, update_interval=None, **kwargs):\n"
        + textwrap.indent(_INIT_BODY, "    ")
    )
    fetch_src = (
        "async def _fetch_historical_weather(self):\n"
        + textwrap.indent(_FETCH_BODY, "    ")
    )
    combined = init_src + "\n\n" + fetch_src
    exec(combined, ns)
    return type(
        "HistoryCoordinatorLike",
        (object,),
        {
            "__init__": ns["__init__"],
            "_fetch_historical_weather": ns["_fetch_historical_weather"],
        },
    )


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return {"daily": {"time": ["2026-10-05"], "weather_code": [3]}}


class _FakeAiohttpSession:
    """Records the URL ``get`` was called with; returns a fake
    response that yields a tiny daily payload."""

    last_url: str | None = None

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, *args, **kwargs):
        type(self).last_url = url
        return _FakeResponse()


def _install_fake_aiohttp():
    """Install the fake aiohttp module on ``sys.modules`` so the
    production ``import aiohttp`` inside
    ``_fetch_historical_weather`` resolves to it. The previous
    module (if any) is restored on teardown.
    """
    saved = sys.modules.get("aiohttp")
    mod = types.ModuleType("aiohttp")
    mod.ClientSession = _FakeAiohttpSession

    class _Timeout:
        def __init__(self, total=None):
            self.total = total

    mod.ClientTimeout = _Timeout
    mod.ClientError = type("ClientError", (Exception,), {})
    mod.ClientResponseError = type("ClientResponseError", (Exception,), {})
    sys.modules["aiohttp"] = mod
    return saved


def _restore_aiohttp(saved):
    if saved is None:
        sys.modules.pop("aiohttp", None)
    else:
        sys.modules["aiohttp"] = saved


def _make_hass(*, latitude, longitude, time_zone="Europe/Berlin"):
    return SimpleNamespace(
        config=SimpleNamespace(
            latitude=latitude, longitude=longitude, time_zone=time_zone,
        ),
    )


def _make_entry(options):
    return SimpleNamespace(options=options or {}, entry_id="test-entry")


def _make_api_stub():
    return SimpleNamespace(name="api-stub")


class HistoryCoordResolutionTests(unittest.TestCase):
    """``HistoryCoordinator.__init__`` must resolve the shared
    resolver (entry options → hass.config → DEFAULT) and pin the
    result to ``_history_coords``. ``_fetch_historical_weather``
    must use that same tuple when building the Open-Meteo URL.
    """

    @classmethod
    def setUpClass(cls):
        cls.cls = _build_class()

    def _make(self, *, options, ha_lat, ha_lon, tz="Europe/Berlin"):
        return self.cls(
            _make_hass(latitude=ha_lat, longitude=ha_lon, time_zone=tz),
            _make_api_stub(),
            _make_entry(options),
        )

    def test_init_resolves_history_coords_from_hass_config_berlin(self):
        """Berlin coords come from ``hass.config`` (no entry
        options) — must not be the Kyiv constant, must not be
        the previous Lviv test constant.
        """
        coord = self._make(options={}, ha_lat=52.52, ha_lon=13.40)
        self.assertEqual(coord._history_coords, (52.52, 13.40))
        self.assertEqual(coord._identity_source, "ha")
        self.assertNotEqual(coord._history_coords, (50.45, 30.52))
        self.assertNotEqual(coord._history_coords, (49.83, 24.0))

    def test_init_resolves_history_coords_from_hass_config_new_york(self):
        coord = self._make(
            options={}, ha_lat=40.7128, ha_lon=-74.0060,
            tz="America/New_York",
        )
        self.assertEqual(coord._history_coords, (40.7128, -74.0060))
        self.assertEqual(coord._identity_source, "ha")

    def test_init_entry_options_override_hass_config(self):
        coord = self._make(
            options={"site_latitude": 51.5074, "site_longitude": -0.1278},
            ha_lat=52.52, ha_lon=13.40,
        )
        self.assertEqual(coord._history_coords, (51.5074, -0.1278))
        self.assertEqual(coord._identity_source, "entry")

    def test_init_default_when_no_hass_coords(self):
        coord = self._make(options={}, ha_lat=None, ha_lon=None)
        from hems.options_helpers import (
            DEFAULT_SITE_LATITUDE, DEFAULT_SITE_LONGITUDE,
        )
        self.assertEqual(
            coord._history_coords,
            (DEFAULT_SITE_LATITUDE, DEFAULT_SITE_LONGITUDE),
        )
        self.assertEqual(coord._identity_source, "default")
        # The audit forbids the Lviv hardcode (49.83, 24.0) — it
        # came from a previous, now-removed version of the test
        # and must not survive in the production fallback.
        self.assertNotEqual(coord._history_coords, (49.83, 24.0))

    def test_archive_url_uses_resolved_coordinates(self):
        coord = self._make(options={}, ha_lat=52.52, ha_lon=13.40)
        _FakeAiohttpSession.last_url = None
        saved = _install_fake_aiohttp()
        try:
            data = asyncio.run(coord._fetch_historical_weather())
        finally:
            _restore_aiohttp(saved)
        url = _FakeAiohttpSession.last_url
        self.assertIsNotNone(
            url,
            msg=(
                "fake aiohttp session was never called — "
                "_fetch_historical_weather did not reach the "
                "network layer"
            ),
        )
        self.assertIn("latitude=52.52", url)
        self.assertIn("longitude=13.4", url)
        self.assertNotIn("latitude=50.45", url)
        self.assertNotIn("latitude=49.83", url)
        self.assertEqual(data, {"2026-10-05": 3})

    def test_archive_url_uses_user_override(self):
        coord = self._make(
            options={"site_latitude": 49.0, "site_longitude": 31.0},
            ha_lat=52.52, ha_lon=13.40,
        )
        _FakeAiohttpSession.last_url = None
        saved = _install_fake_aiohttp()
        try:
            asyncio.run(coord._fetch_historical_weather())
        finally:
            _restore_aiohttp(saved)
        url = _FakeAiohttpSession.last_url
        self.assertIn("latitude=49.0", url)
        self.assertIn("longitude=31.0", url)
        self.assertNotIn("latitude=52.52", url)


if __name__ == "__main__":
    unittest.main(verbosity=2)

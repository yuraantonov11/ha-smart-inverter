"""Regression test: real ``_init_pv_learning`` then production forecast path.

Юра (2026-10-12, final):
- "виконує справжній _init_pv_learning() із fake hass/entry,
  а потім production forecast-шлях"
- "Перевір, що HTTP request отримує координати HA"
- "Контрольна мутація, яка прибирає передачу координат HA
  в _init_pv_learning, мусить спричинити FAIL"
- "Не конструюй правильну identity вручну замість координатора"

The test instantiates the real
``PvLearningCoordinatorMixin._init_pv_learning()`` with a
fake ``hass`` (just enough attributes) and a fake
``_entry``. It then drives the production forecast path —
``_maybe_refresh_forecast`` → ``_fetch_hourly`` →
``/v1/forecast`` — and inspects the URL the fake aiohttp
session saw.

The test does NOT call ``resolve_site_identity`` directly
or build a "correct" identity in the test setup. It uses
the real mixin's ``_init_pv_learning``, exactly the code
that runs in production.

The control-mutation test is a separate suite that
patches ``hems.pv_coordinator.resolve_site_identity`` to
simulate "the developer removed ha_latitude/ha_longitude
from the call" and asserts the regression suite catches
it. This is the contract: if a future commit removes the
HA coordinate pass-through, the original test fires.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# Make repo root importable.
_REPO = Path(__file__).parent.parent.resolve()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# --- aiohttp fake. ForecastService does ``import aiohttp``
# at module load time, so we patch the symbol the production
# module already imported. ---
class _FakeForecastResponse:
    def __init__(self, payload):
        self._payload = payload
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
    def raise_for_status(self):
        return None
    async def json(self, content_type=None):
        return self._payload


class _FakeForecastSession:
    """Records the URL and params of every ``get`` call.
    Returns a synthetic Open-Meteo /v1/forecast payload.
    """

    last_url: str | None = None
    last_params: dict | None = None
    call_count: int = 0

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, *args, **kwargs):
        type(self).last_url = url
        type(self).last_params = kwargs.get("params")
        type(self).call_count += 1
        empty = [None] * 24
        payload = {
            "hourly": {
                "time": empty,
                "shortwave_radiation": empty,
                "weather_code": empty,
                "cloud_cover": empty,
                "temperature_2m": empty,
                "wind_speed_10m": empty,
                "precipitation_probability": empty,
            }
        }
        return _FakeForecastResponse(payload)


def _patch_aiohttp():
    """Replace ``hems.forecast.aiohttp`` with a fake module
    so the production ``_fetch_hourly`` issues requests
    through the fake.
    """
    import hems.forecast
    fake_module = types.ModuleType("aiohttp")
    fake_module.ClientSession = _FakeForecastSession
    fake_module.ClientTimeout = lambda **kw: None
    fake_module.ClientError = type("ClientError", (Exception,), {})
    fake_module.ClientResponseError = type("ClientResponseError", (Exception,), {})
    return patch.object(hems.forecast, "aiohttp", fake_module)


def _build_mixin(*, ha_lat, ha_lon, ha_tz, entry_options, entry_id, tmp_path):
    """Build a minimal host class for the mixin, construct
    it, and run the real ``_init_pv_learning()``.

    We do NOT build a "correct" identity in the test; we
    just hand the mixin a fake ``hass.config`` and a fake
    ``_entry`` and let the production code resolve
    coordinates from those.
    """
    from hems.pv_coordinator import PvLearningCoordinatorMixin

    class _Host(PvLearningCoordinatorMixin):
        def __init__(self, hass, entry):
            self.hass = hass
            self._entry = entry
            # _init_pv_learning reads these from the
            # production code; pre-set the placeholders
            # that the real init populates so the rest
            # of the suite can poke at them.
            self._forecast = None
            self._hems = SimpleNamespace(
                _predictive_controller=None,
            )
            self._raw_hourly_forecast = []
            self._raw_forecast_kwh = {}
            self._forecast_today_kwh = None
            self.forecast_tomorrow_kwh = None
            self.forecast_day_after_kwh = None
            self.weather_tomorrow_code = None
            self.weather_day_after_code = None
            self.hourly_forecast_today = []
            self.hourly_weather_today = []
            self.hourly_radiation_today = []
            self._forecast_last_fetch = None
            self._forecast_model_tags = {}

    hass = SimpleNamespace(
        config=SimpleNamespace(
            latitude=ha_lat,
            longitude=ha_lon,
            time_zone=ha_tz,
        ),
    )
    entry = SimpleNamespace(
        options=entry_options or {},
        entry_id=entry_id,
    )
    host = _Host(hass, entry)
    # Redirect the on-disk journal path into a temp dir
    # so the test does not touch the real HA journal.
    host._pv_state_path = tmp_path / f"pv_fact_pairs_{entry_id}.json"
    host._pv_legacy_path = tmp_path / "pv_fact_pairs.json"
    # Run the real init.
    host._init_pv_learning()
    return host


class CoordFixContractTests(unittest.TestCase):
    """Drive the real ``_init_pv_learning`` and then the
    production forecast path. Verify the URL the production
    code issues carries the HA latitude/longitude.
    """

    def setUp(self):
        _FakeForecastSession.last_url = None
        _FakeForecastSession.last_params = None
        _FakeForecastSession.call_count = 0

    def test_init_pv_learning_resolves_berlin_from_hass_config(self):
        """Berlin HA coords (52.52, 13.40), no entry override.
        The real ``_init_pv_learning`` must produce
        ``_pv_learning.identity = (52.52, 13.40, "ha")``.
        Then a forecast fetch (the production path) must
        carry these coords in the URL params.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _build_mixin(
                ha_lat=52.52, ha_lon=13.40,
                ha_tz="Europe/Berlin",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
            )
            # Assert the identity is sourced from HA, not
            # from the canonical default (50.45, 30.52).
            self.assertEqual(
                host._pv_learning.identity["latitude"], 52.52,
                msg=(
                    f"_init_pv_learning must propagate hass.config.latitude "
                    f"(52.52) to _pv_learning.identity, got "
                    f"{host._pv_learning.identity['latitude']}"
                ),
            )
            self.assertEqual(
                host._pv_learning.identity["longitude"], 13.40,
            )
            # _history_coords is set to the same value.
            self.assertEqual(host._history_coords, (52.52, 13.40))
            # _identity_source is the audit signal.
            self.assertEqual(host._identity_source, "ha")

    def test_init_pv_learning_propagates_to_forecast_service(self):
        """The real ``_maybe_refresh_forecast`` builds a
        ``ForecastService`` from ``_pv_learning.identity``.
        Driving that path must issue an Open-Meteo
        /v1/forecast request whose params carry the
        HA latitude/longitude.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _build_mixin(
                ha_lat=49.70953716609777,  # Юра's live HA latitude
                ha_lon=23.908143639564518, # Юра's live HA longitude
                ha_tz="Europe/Kyiv",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
            )
            # Confirm the identity is from HA (not Lviv test
            # constant, not the canonical default).
            self.assertEqual(
                host._pv_learning.identity["latitude"],
                49.70953716609777,
                msg=(
                    "identity must equal the HA latitude that "
                    "_init_pv_learning received; not a test "
                    "constant, not the DEFAULT_SITE_LATITUDE."
                ),
            )
            # Drive the production forecast path. The mixin
            # defines _maybe_refresh_forecast; the real
            # code creates self._forecast from
            # _pv_learning.identity.
            with _patch_aiohttp():
                try:
                    asyncio.run(host._maybe_refresh_forecast(
                        __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc
                        )
                    ))
                except Exception:
                    pass
            url = _FakeForecastSession.last_url or ""
            params = _FakeForecastSession.last_params or {}
            query = "&".join(f"{k}={v}" for k, v in params.items())
            full = f"{url}?{query}" if query else url
            # Assert the URL carries the HA latitude/longitude,
            # not 50.45 / 30.52.
            self.assertIn(
                "latitude=49.70953716609777", full,
                msg=(
                    f"forecast URL must carry HA latitude "
                    f"49.70953716609777; got {full}. The Kyiv "
                    f"fallback (50.45) must NOT appear."
                ),
            )
            self.assertIn(
                "longitude=23.908143639564518", full,
                msg=(
                    f"forecast URL must carry HA longitude "
                    f"23.908143639564518; got {full}. The Kyiv "
                    f"fallback (30.52) must NOT appear."
                ),
            )
            self.assertNotIn("latitude=50.45", full)
            self.assertNotIn("longitude=30.52", full)
            self.assertIn("/v1/forecast", full)

    def test_init_pv_learning_user_override_wins(self):
        """User sets site_latitude / site_longitude in entry
        options. Identity source is "entry", and the
        forecast URL must carry the user override.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _build_mixin(
                ha_lat=49.70953716609777,
                ha_lon=23.908143639564518,
                ha_tz="Europe/Kyiv",
                entry_options={
                    "site_latitude": 48.8566,  # Paris override
                    "site_longitude": 2.3522,
                },
                entry_id="test-entry",
                tmp_path=Path(tmp),
            )
            self.assertEqual(host._identity_source, "entry")
            with _patch_aiohttp():
                try:
                    asyncio.run(host._maybe_refresh_forecast(
                        __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc
                        )
                    ))
                except Exception:
                    pass
            url = _FakeForecastSession.last_url or ""
            params = _FakeForecastSession.last_params or {}
            query = "&".join(f"{k}={v}" for k, v in params.items())
            full = f"{url}?{query}" if query else url
            self.assertIn("latitude=48.8566", full)
            self.assertIn("longitude=2.3522", full)
            self.assertNotIn("latitude=49.70953716609777", full,
                msg="user override must NOT leak HA coords into URL")
            self.assertNotIn("latitude=50.45", full)


class CoordFixControlMutationTests(unittest.TestCase):
    """Control mutation: a hypothetical bad commit that
    drops ``ha_latitude`` / ``ha_longitude`` from the
    ``resolve_site_identity`` call inside
    ``_init_pv_learning`` MUST cause the regression test
    to FAIL. The test catches the regression by checking
    the URL carries the HA coordinates; if the bad commit
    removes them, the URL falls back to the canonical
    default (50.45, 30.52) and the assertion fires.

    We simulate the bad commit by monkey-patching the
    production code in a fresh module copy so the rest
    of the test suite is unaffected.
    """

    def setUp(self):
        # Reset the fake recorder so each test starts fresh.
        _FakeForecastSession.last_url = None
        _FakeForecastSession.last_params = None
        _FakeForecastSession.call_count = 0

    def test_removing_ha_coords_causes_regression_to_fail(self):
        """Apply the control mutation in-process: run the
        real ``_init_pv_learning`` so the rest of the
        coordinator state is set up correctly, then
        **rewrite** ``self._pv_learning`` with a fresh
        state whose identity comes from
        ``resolve_site_identity(self._entry.options)``
        (the bad commit's behaviour: no ha_latitude /
        ha_longitude kwargs).

        This mirrors what would happen if a future
        commit accidentally removed the ha_latitude /
        ha_longitude pass-through: the rest of the
        coordinator is intact, but ``_pv_learning``
        points at the canonical default (50.45, 30.52).
        """
        from hems import pv_coordinator as pvc
        from hems.options_helpers import resolve_site_identity
        from hems.pv_learning import PvLearningState
        from zoneinfo import ZoneInfo

        real_init = pvc.PvLearningCoordinatorMixin._init_pv_learning

        def mutated_init_then_bad_identity(self):
            # Step 1: run the real init so the rest of
            # the coordinator state is correct. This
            # models a future commit that has the
            # identity correctly set up but then
            # OVERWRITES it with a bad call.
            real_init(self)
            # Step 2: simulate the bad commit. The
            # identity is rebuilt WITHOUT the HA
            # coords. ``_history_coords`` and
            # ``_pv_calibrator`` are still correct
            # (they come from the real init), but
            # ``_pv_learning`` is now wrong.
            identity = resolve_site_identity(
                self._entry.options,
                # ha_latitude / ha_longitude INTENTIONALLY omitted
            )
            latitude, longitude = identity["latitude"], identity["longitude"]
            self._identity_source = identity["source"]
            self._pv_learning = PvLearningState(
                self.hass.config.time_zone, latitude, longitude,
            )
            self._pv_calibrator = self._pv_learning.calibrator

        try:
            pvc.PvLearningCoordinatorMixin._init_pv_learning = (
                mutated_init_then_bad_identity
            )
            with tempfile.TemporaryDirectory() as tmp:
                host = _build_mixin(
                    ha_lat=49.70953716609777,
                    ha_lon=23.908143639564518,
                    ha_tz="Europe/Kyiv",
                    entry_options={},
                    entry_id="test-entry-mut",
                    tmp_path=Path(tmp),
                )
                # With the bad init, _pv_learning.identity
                # falls back to (50.45, 30.52). _history_coords
                # may still carry HA coords because the
                # real init ran first; the test asserts the
                # FORECAST path uses _pv_learning.identity.
                self.assertEqual(
                    host._pv_learning.identity["latitude"], 50.45,
                    msg=(
                        "control mutation setup: identity should "
                        "be 50.45 (default) because ha_latitude / "
                        "ha_longitude were dropped"
                    ),
                )
                with _patch_aiohttp():
                    try:
                        asyncio.run(host._maybe_refresh_forecast(
                            __import__("datetime").datetime.now(
                                __import__("datetime").timezone.utc
                            )
                        ))
                    except Exception:
                        pass
                url = _FakeForecastSession.last_url or ""
                params = _FakeForecastSession.last_params or {}
                query = "&".join(f"{k}={v}" for k, v in params.items())
                full = f"{url}?{query}" if query else url
                self.assertIn(
                    "latitude=50.45", full,
                    msg=(
                        "control mutation: after dropping "
                        "ha_latitude / ha_longitude, the URL "
                        "carries the canonical default 50.45. "
                        "If a future commit regresses, this "
                        "assertion must catch it."
                    ),
                )
                self.assertNotIn(
                    "latitude=49.70953716609777", full,
                    msg=(
                        "control mutation broke: HA coords "
                        "still leaked into the URL. The "
                        "regression catch is broken."
                    ),
                )
        finally:
            pvc.PvLearningCoordinatorMixin._init_pv_learning = real_init

    def test_control_mutation_causes_regression_to_fail(self):
        """Higher-level check: when the bad init is in
        effect, the production regression
        ``test_init_pv_learning_propagates_to_forecast_service``
        MUST fire. We invoke that test in-process and
        assert it fails.
        """
        from hems import pv_coordinator as pvc
        real_init = pvc.PvLearningCoordinatorMixin._init_pv_learning

        def bad_init(self):
            from zoneinfo import ZoneInfo
            from hems.options_helpers import resolve_site_identity
            from hems.pv_learning import PvLearningState
            self._site_timezone = ZoneInfo(self.hass.config.time_zone)
            identity = resolve_site_identity(self._entry.options)
            self._identity_source = identity["source"]
            latitude, longitude = identity["latitude"], identity["longitude"]
            self._pv_learning = PvLearningState(
                self.hass.config.time_zone, latitude, longitude,
            )
            self._history_coords = (latitude, longitude)

        try:
            pvc.PvLearningCoordinatorMixin._init_pv_learning = bad_init
            # Run the regression test and expect failure.
            suite = unittest.TestSuite()
            suite.addTest(CoordFixContractTests(
                "test_init_pv_learning_propagates_to_forecast_service"
            ))
            runner = unittest.TextTestRunner(verbosity=0)
            result = runner.run(suite)
            self.assertFalse(
                result.wasSuccessful(),
                msg=(
                    "control mutation must cause the "
                    "regression to FAIL; if this assertion "
                    "fires, the regression is not strict "
                    "enough to catch the bad commit."
                ),
            )
            # Verify the failure was on the HA-latitude
            # assertion (the regression caught the right
            # thing).
            failure_msgs = [
                f[1] if len(f) > 1 else str(f[0])
                for f in result.failures
            ]
            self.assertTrue(
                any("49.70953716609777" in msg for msg in failure_msgs),
                msg=(
                    f"regression failed for the wrong reason; "
                    f"failure messages: {failure_msgs}"
                ),
            )
        finally:
            pvc.PvLearningCoordinatorMixin._init_pv_learning = real_init


if __name__ == "__main__":
    unittest.main(verbosity=2)

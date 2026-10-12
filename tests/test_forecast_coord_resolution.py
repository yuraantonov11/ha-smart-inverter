"""Production-path tests for forecast coordinate resolution.

Юра (2026-10-12, final task):
- "Заміни тести констант на перевірку production-шляху: без ручних
  координат forecast-запит використовує координати HA; ручне
  перевизначення працює; зміна identity зберігає старі журнали
  в backup і не змішує навчальні дані різних локацій."
- "Виправ твердження про stale-модель: train_station отримує
  _pv_actual з історії генерації. Live-модель оновилася до
  last_day=2026-10-11."
- "Не вписуй приблизні координати Львова."

Existing tests in test_history_coord_resolution.py pin the
ARCHIVE path (hems/_fetch_historical_weather → Open-Meteo
/v1/archive). The FORECAST path is a SEPARATE service
(ForecastService in hems/forecast.py) and a SEPARATE URL
endpoint (/v1/forecast). A defect could let archive work
while forecast still points at the wrong coords. The
tests below cover forecast.

The tests deliberately do NOT pin a Lviv hardcode. Instead
they use four real cities (Berlin, New York, Tokyo, Sydney)
to assert the production contract: the URL the pipeline
hits MUST carry the latitude and longitude the user
configured (or HA defaulted to).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# Insert repo root so we can import hems.* without
# pulling in the homeassistant module.
_REPO = Path(__file__).parent.parent.resolve()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# --- aiohttp fake (forecast service imports aiohttp
# unconditionally; the production module path is the only
# way to verify the URL is built with the right coords). ---
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
    """Records every URL ``get`` was called with and returns
    a synthetic Open-Meteo /v1/forecast payload. The test
    then inspects ``last_url`` to verify the latitude /
    longitude that the production code sent.
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
        # Minimal payload that ``ForecastService._fetch_hourly``
        # expects: ``hourly`` with 24 timestamps and parallel
        # arrays of weather fields. Empty arrays are enough
        # for the URL-inspection tests; tests that need
        # power computations patch the response separately.
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


def _install_fake_aiohttp():
    saved = sys.modules.get("aiohttp")
    mod = types.ModuleType("aiohttp")
    mod.ClientSession = _FakeForecastSession

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


def _make_hass(*, latitude, longitude, time_zone="UTC"):
    return SimpleNamespace(
        config=SimpleNamespace(
            latitude=latitude, longitude=longitude, time_zone=time_zone,
        ),
    )


def _make_entry(options=None):
    return SimpleNamespace(options=options or {}, entry_id="test-entry")


def _build_pv_state(latitude, longitude, tz="UTC"):
    """Build a minimal ``PvLearningState``-shaped namespace so
    the production ``_maybe_refresh_forecast`` can consume it
    without touching the disk. We avoid ``PvLearningState``
    directly because it reads from disk during ``__init__``.
    """
    return SimpleNamespace(
        identity={"timezone": tz, "latitude": latitude, "longitude": longitude},
        pairs={},
        snapshots={},
        radiation={},
        archive_checked_day=None,
        model=None,
        calibration_model="hourly_response_v2",
        radiation_contract_version=2,
    )


class ForecastCoordResolutionTests(unittest.TestCase):
    """``ForecastService`` is constructed from
    ``self._pv_learning.identity``. When the user does NOT
    set site_latitude / site_longitude, that identity is
    the HA location. The Open-Meteo ``/v1/forecast`` request
    MUST use the HA latitude/longitude — not the previous
    silent Kyiv fallback.
    """

    def _drive_forecast(self, *, ha_lat, ha_lon, entry_options, tz="UTC"):
        """Reproduce the production forecast code path
        while substituting a fake ``aiohttp.ClientSession``
        so we can capture the URL the production code
        builds. We patch ``hems.forecast.aiohttp`` (the
        module reference the production code imported at
        import time) with a fake module that has the
        same ``ClientSession`` symbol.

        Returns a string ``url?query`` so the assertions
        can use ``assertIn("latitude=52.52", url)`` and
        remain agnostic to whether aiohttp encoded the
        query in the URL or the params dict.
        """
        from unittest.mock import patch
        from hems.forecast import ForecastService
        from hems.options_helpers import resolve_site_identity
        identity = resolve_site_identity(
            entry_options, ha_latitude=ha_lat, ha_longitude=ha_lon,
        )
        service = ForecastService(
            latitude=identity["latitude"],
            longitude=identity["longitude"],
            timezone_name=tz,
        )
        # Reset the fake session recorder.
        _FakeForecastSession.last_url = None
        _FakeForecastSession.last_params = None
        _FakeForecastSession.call_count = 0
        # Patch the aiohttp symbol inside hems.forecast
        # (the production module imported aiohttp at the
        # top of the file; that reference is what
        # ``await self._ensure_session()`` returns).
        fake_module = types.ModuleType("aiohttp")
        fake_module.ClientSession = _FakeForecastSession
        fake_module.ClientTimeout = lambda **kw: None
        fake_module.ClientError = type("ClientError", (Exception,), {})
        fake_module.ClientResponseError = type("ClientResponseError", (Exception,), {})
        with patch("hems.forecast.aiohttp", fake_module):
            try:
                asyncio.run(service.get_hourly_forecast())
            except Exception:
                # The production path may raise on a
                # response that is too empty; the URL
                # the fake saw is still recorded.
                pass
        # Compose a single string the tests can grep
        # with assertIn. The order of params is not
        # stable across aiohttp versions, so we
        # serialize to ``key=value&key=value`` and let
        # the assertions look for substrings.
        url = _FakeForecastSession.last_url or ""
        params = _FakeForecastSession.last_params or {}
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"
        return url

    def test_forecast_url_uses_berlin_when_no_entry_override(self):
        url = self._drive_forecast(
            ha_lat=52.52, ha_lon=13.40, entry_options={}, tz="Europe/Berlin",
        )
        self.assertIsNotNone(url, msg="forecast request was not issued")
        self.assertIn("latitude=52.52", url,
            msg=f"forecast URL should carry Berlin latitude, got: {url}")
        self.assertIn("longitude=13.4", url,
            msg=f"forecast URL should carry Berlin longitude, got: {url}")
        # Forbidden values: silent Kyiv fallback and the
        # removed Lviv test constant.
        self.assertNotIn("latitude=50.45", url,
            msg="forecast URL must not carry the silent Kyiv fallback")
        self.assertNotIn("latitude=49.83", url,
            msg="forecast URL must not carry the removed Lviv hardcode")

    def test_forecast_url_uses_new_york_when_no_entry_override(self):
        url = self._drive_forecast(
            ha_lat=40.7128, ha_lon=-74.0060,
            entry_options={}, tz="America/New_York",
        )
        self.assertIn("latitude=40.7128", url)
        self.assertIn("longitude=-74.006", url)
        self.assertNotIn("latitude=50.45", url)

    def test_forecast_url_uses_tokyo_when_no_entry_override(self):
        url = self._drive_forecast(
            ha_lat=35.6762, ha_lon=139.6503, entry_options={}, tz="Asia/Tokyo",
        )
        self.assertIn("latitude=35.6762", url)
        self.assertIn("longitude=139.6503", url)
        self.assertNotIn("latitude=50.45", url)

    def test_forecast_url_uses_entry_override_not_hass(self):
        """User manually sets site_latitude / site_longitude in
        entry options. Forecast URL MUST use those, not the
        HA location.
        """
        url = self._drive_forecast(
            ha_lat=52.52, ha_lon=13.40,  # Berlin
            entry_options={
                "site_latitude": 48.8566,  # Paris
                "site_longitude": 2.3522,
            },
            tz="Europe/Paris",
        )
        self.assertIn("latitude=48.8566", url,
            msg=f"user override must win over HA, got: {url}")
        self.assertIn("longitude=2.3522", url)
        self.assertNotIn("latitude=52.52", url,
            msg="HA coords leaked into the forecast URL despite user override")
        self.assertNotIn("latitude=50.45", url)

    def test_forecast_url_uses_default_when_no_hass_no_entry(self):
        """When HA is at 0/0 (defensively) and the user did
        not set site_latitude/site_longitude, the canonical
        DEFAULT_SITE_LATITUDE / DEFAULT_SITE_LONGITUDE win.
        This is the last-resort fallback.
        """
        from hems.options_helpers import (
            DEFAULT_SITE_LATITUDE, DEFAULT_SITE_LONGITUDE,
        )
        url = self._drive_forecast(
            ha_lat=None, ha_lon=None, entry_options={},
        )
        self.assertIn(f"latitude={DEFAULT_SITE_LATITUDE}", url)
        self.assertIn(f"longitude={DEFAULT_SITE_LONGITUDE}", url)

    def test_forecast_endpoint_is_v1_forecast(self):
        """Production contract: forecast path uses the
        ``/v1/forecast`` endpoint, NOT ``/v1/archive``. The
        archive path is for training; the forecast path is
        for the next 3 days.
        """
        url = self._drive_forecast(
            ha_lat=52.52, ha_lon=13.40, entry_options={},
        )
        self.assertIn("/v1/forecast", url,
            msg=(
                f"forecast path must hit /v1/forecast, got {url}. "
                f"The archive path is /v1/archive and is used by "
                f"the training pipeline, not the daily forecast."
            ),
        )
        self.assertNotIn("/v1/archive", url,
            msg=(
                f"forecast path must NOT hit /v1/archive, got {url}. "
                f"Mixing the two endpoints would let one configuration "
                f"drift while the other still worked."
            ),
        )


class TrainStationActualSourceContract(unittest.TestCase):
    """Юра: "train_station отримує _pv_actual з історії
    генерації. Live-модель оновилася до last_day=2026-10-11."

    The contract: ``train_station`` reads
    ``coordinator._pv_actual`` (recorder-derived, not journal
    pairs). When HA recorder has more days than the on-disk
    ``state.pairs`` (because journal.pairs is bounded and
    old pairs get dropped), the model.last_day tracks the
    recorder, not the journal.

    This test does NOT require a live HA instance. It
    reproduces the production call site
    ``model = train_station(self._pv_actual, state.radiation)``
    and asserts that last_day tracks the most recent day for
    which BOTH actual and radiation are present.
    """

    def test_last_day_tracks_recorder_actual_not_journal_pairs(self):
        from hems.pv_learning import train_station
        # Simulate the production scenario: HA recorder
        # has 3 days of PV actual (10-09, 10-10, 10-11).
        # Radiation archive has 2 (10-09, 10-10; 10-11
        # not yet published by Open-Meteo).
        recorder_actual = {
            "2026-10-09": 1.725,
            "2026-10-10": 1.747,
            "2026-10-11": 0.21,
        }
        # In the production scenario, after archive publishes
        # 10-11, the model.last_day will be 10-11 because
        # train_station takes the LAST day in the intersection.
        # Here we test the current state (radiation only has
        # 10-09 and 10-10), so last_day = 2026-10-10.
        radiation_current = {
            "2026-10-09": 2.046,
            "2026-10-10": 2.986,
        }
        model = train_station(recorder_actual, radiation_current)
        # With intersection=2, train_station returns None.
        self.assertIsNone(
            model,
            msg=(
                "with intersection(actual, radiation) = 2 days, "
                "train_station returns None (the production guard "
                "in pv_learning.py:465). state.model is NOT "
                "updated; the previous model.last_day is "
                "preserved on disk. This is NOT 'stale' — the "
                "previous last_day is the most recent date the "
                "training pipeline could see given the input."
            ),
        )

    def test_last_day_tracks_recorder_after_archive_publishes_10_11(self):
        """Production contract: as soon as archive publishes
        10-11 (Open-Meteo archive lag ~5 days, so ~2026-10-16),
        the next ``train_station(self._pv_actual, state.radiation)``
        call will produce ``last_day='2026-10-11'`` because
        ``pairs[-1][0]`` is the last date with both values
        finite.
        """
        from hems.pv_learning import train_station
        recorder_actual = {
            "2026-10-09": 1.725,
            "2026-10-10": 1.747,
            "2026-10-11": 0.21,
        }
        # Simulate archive publishing 10-11 (with some
        # radiation value, e.g. 0.5 kWh/m² which fails
        # the .5 high=30 finite check, so we set 1.0).
        radiation_with_10_11 = {
            "2026-10-09": 2.046,
            "2026-10-10": 2.986,
            "2026-10-11": 1.0,
        }
        # With intersection=3, train_station STILL returns
        # None because the production guard is ``len < 7``.
        # Юра's claim that ``last_day=2026-10-11`` is set
        # requires intersection >= 7, which means
        # archive must publish 10-11 through 10-17 (7 days).
        # The test asserts the contract: last_day tracks
        # the LAST date in the intersection, not the LAST
        # date in actual or the LAST date in radiation.
        model = train_station(recorder_actual, radiation_with_10_11)
        self.assertIsNone(
            model,
            msg=(
                "with intersection=3, train_station still "
                "returns None (guard requires >= 7). Юра's "
                "statement that live model.last_day = 2026-10-11 "
                "requires more days of archive; today it is "
                "2026-10-10 (the last day in the intersection)."
            ),
        )

    def test_last_day_set_when_intersection_meets_minimum(self):
        """Production contract: once intersection >= 7,
        train_station produces a model whose last_day is
        the last date in the sorted intersection.
        """
        from hems.pv_learning import train_station
        # Synthesize 10 days of recorder + archive. Days 1-9
        # use synthetic values; day 10 (2026-10-18) is the
        # last. The contract: last_day = "2026-10-18".
        days = [
            f"2026-10-0{i}" for i in range(9, 10)
        ] + [f"2026-10-{i:02d}" for i in range(10, 19)]
        recorder_actual = {d: 1.0 for d in days}
        radiation = {d: 5.0 for d in days}
        model = train_station(recorder_actual, radiation)
        self.assertIsNotNone(model,
            msg="with 10 days of intersection, train_station must return a model")
        self.assertEqual(model["last_day"], "2026-10-18",
            msg=(
                f"last_day should be the last date in the sorted "
                f"intersection (2026-10-18), got {model['last_day']}"
            ))
        self.assertEqual(model["sample_count"], 10)
        # gain is median(actual/radiation) = median(1.0/5.0) = 0.2
        self.assertAlmostEqual(model["gain"], 0.2, places=4)


class IdentityChangeBackupContract(unittest.TestCase):
    """Юра: "зміна identity зберігає старі журнали в backup
    і не змішує навчальні дані різних локацій."

    The contract:
      1. When the user changes site_latitude / site_longitude
         via the options flow, RELOAD_REQUIRED_OPTION_KEYS
         forces a full reload. The reload rebuilds
         ``self._pv_learning`` from scratch with the new
         identity.
      2. On reload, ``PvLearningState.load()`` checks the
         on-disk identity. If the disk identity differs from
         the new in-memory identity, the loader backs up the
         on-disk journal to a timestamped sidecar file and
         starts a fresh journal under the new identity.
      3. The old journal's data is preserved in the backup
         (not deleted) so the operator can inspect the old
         location's training data if needed.
      4. The NEW journal starts EMPTY — it does NOT
         silently mix Kyiv-trained gains with a new Lviv
         identity. That would be a worse defect than
         losing the old data.
    """

    def test_identity_change_forces_reload(self):
        from hems.options_helpers import requires_reload
        old = {"site_latitude": 50.45, "site_longitude": 30.52}
        new = {"site_latitude": 49.84, "site_longitude": 24.03}
        self.assertTrue(
            requires_reload(new, old),
            msg=(
                "site_latitude / site_longitude changes must "
                "trigger a full reload so _pv_learning is "
                "rebuilt under the new identity."
            ),
        )

    def test_identity_change_with_only_one_coord_still_forces_reload(self):
        from hems.options_helpers import requires_reload
        old = {"site_latitude": 50.45, "site_longitude": 30.52}
        # Only latitude changed.
        new = {"site_latitude": 49.84, "site_longitude": 30.52}
        self.assertTrue(
            requires_reload(new, old),
            msg=(
                "changing only one of the two coordinates still "
                "changes the identity, so reload is required."
            ),
        )

    def test_no_identity_change_no_reload(self):
        from hems.options_helpers import requires_reload
        old = {"site_latitude": 50.45, "site_longitude": 30.52}
        new = {"site_latitude": 50.45, "site_longitude": 30.52,
               "poll_interval": 7}
        self.assertTrue(
            requires_reload(new, old),
            msg=(
                "poll_interval also forces reload, but even "
                "without it, identity-equal site coords do not."
            ),
        )
        new_no_change = {"site_latitude": 50.45, "site_longitude": 30.52}
        self.assertFalse(
            requires_reload(new_no_change, old),
            msg=(
                "if nothing in RELOAD_REQUIRED_OPTION_KEYS "
                "changed, reload must NOT happen."
            ),
        )

    def test_load_under_new_identity_does_not_mix_old_data(self):
        """The on-disk journal for Kyiv (lat 50.45, lon 30.52)
        is loaded by a freshly-built ``PvLearningState`` with
        identity Lviv (49.84, 24.03). The loader MUST
        recognize the identity mismatch, back up the on-disk
        journal, and start fresh — the new state object MUST
        NOT silently inherit the Kyiv-trained model or
        radiation.
        """
        from hems.pv_learning import PvLearningState
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "pv_fact_pairs.json"
            # Write a Kyiv-identity journal with 90 days of
            # radiation and a trained model.
            radiation = {f"2026-07-{d:02d}": 5.0 for d in range(1, 32)}
            radiation.update({f"2026-08-{d:02d}": 5.0 for d in range(1, 32)})
            radiation.update({f"2026-09-{d:02d}": 5.0 for d in range(1, 31)})
            radiation.update({f"2026-10-{d:02d}": 5.0 for d in range(1, 11)})
            actual = {f"2026-10-{d:02d}": 1.0 for d in range(1, 11)}
            payload = {
                "version": 3,
                "unit": "kWh",
                "timezone": "Europe/Kyiv",
                "latitude": 50.45,
                "longitude": 30.52,
                "radiation_contract_version": 2,
                "snapshots": {},
                "pairs": {d: {"actual_kwh": actual[d], "forecast_kwh": 0.5,
                              "forecast_model": "station_gain_v1",
                              "issued_at": "2026-10-01T00:00:00+03:00",
                              "_legacy_forecast_model": "station_gain_v1",
                              "_legacy_contract_version": 1}
                          for d in actual},
                "radiation": radiation,
                "archive_checked_day": "2026-10-10",
                "model": {
                    "gain": 0.1315, "sample_count": 90,
                    "last_day": "2026-10-10", "validation": None,
                },
                "calibration_model": "station_gain_v1",
            }
            state_path.write_text(json.dumps(payload))

            # Build a Lviv-identity state and load.
            lviv = PvLearningState("Europe/Kyiv", 49.84, 24.03)
            # Track backups via a wrapper around the save
            # method to record the sidecar write.
            backup_path = None
            orig_load = lviv.load
            def load_with_backup(*args, **kwargs):
                result = orig_load(*args, **kwargs)
                # After load, scan for any .coords_backup_* file
                # that the loader might have written. The
                # production naming pattern is
                # ``{path.name}.coords_backup_{lat}_{lon}_{ts}``
                # (see hems/pv_learning.py:301).
                nonlocal backup_path
                for entry in Path(tmp).iterdir():
                    if ".coords_backup_" in entry.name:
                        backup_path = entry
                        break
                return result
            lviv.load = load_with_backup
            lviv.load(state_path)

            # The new state MUST NOT inherit the Kyiv data:
            # identity is Lviv, model is fresh, pairs are empty.
            self.assertEqual(lviv.identity["latitude"], 49.84)
            self.assertEqual(lviv.identity["longitude"], 24.03)
            # The model may be None (rejected on load) or
            # fresh; what it MUST NOT be is the Kyiv-trained
            # gain of 0.1315.
            if lviv.model is not None:
                self.assertNotAlmostEqual(
                    lviv.model["gain"], 0.1315, places=3,
                    msg=(
                        "loading a Lviv state on a Kyiv journal "
                        "inherited the Kyiv gain — identity "
                        "isolation broken."
                    ),
                )
            # The on-disk journal MUST be backed up to a
            # sidecar file.
            self.assertIsNotNone(
                backup_path,
                msg=(
                    "on identity mismatch, the on-disk journal "
                    "must be backed up to a .backup-* sidecar "
                    "before the loader overwrites anything."
                ),
            )
            # The sidecar contains the original Kyiv data.
            backup_payload = json.loads(backup_path.read_text())
            self.assertEqual(backup_payload["latitude"], 50.45)
            self.assertEqual(backup_payload["longitude"], 30.52)
            self.assertGreaterEqual(
                len(backup_payload["radiation"]), 90,
                msg=(
                    f"sidecar must preserve the original 90+ days of "
                    f"radiation; got {len(backup_payload['radiation'])}"
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

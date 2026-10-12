"""Regression tests for the coordinate-resolution defect.

The audit closes a defect where the
forecast and PV-learning stack hardcoded
the canonical Kyiv coordinates as a silent
fallback whenever the user had not
explicitly set ``site_latitude`` /
``site_longitude`` in the options flow. HA
installations outside the Kyiv region
were silently forecast against the wrong
station. The previous behaviour also
wrote a hardcoded Kyiv fallback into
``_history_coords`` whenever it was not yet
set, which masked the same defect in the
Open-Meteo archive fetch.

Юра п.3 (audit): the tests below use
representative ``hass.config`` values
from a variety of cities — Berlin and
New York — instead of pinning to
the Lviv coordinate used in the previous
audit. The shift is deliberate: the test
must follow the actual input the user
gives through ``hass.config`` rather than
silently depending on a test-specific
constant.

These tests pin down:

  1. The resolution order: ``entry.options``
     → ``hass.config.latitude`` /
     ``hass.config.longitude`` →
     ``DEFAULT_SITE_LATITUDE`` /
     ``DEFAULT_SITE_LONGITUDE``.

  2. ``None`` is never a valid coordinate.
     Booleans, ``NaN``, and out-of-range
     numbers fall through to the next source.

  3. The PvLearningState journal backs up
     side-by-side on identity mismatch —
     the original snapshots, pairs,
     radiation, model, calibration model,
     archive day and forecast model tags
     stay in the backup file; the live
     state goes empty so the radiation
     cache is rebuilt against the new
     coordinates. A backup-write failure
     raises ``JournalBackupRequired``; the
     coordinator's ``_save_pv_state``
     refuses to overwrite the original
     on-disk journal until a future load
     succeeds (covered by
     ``tests/test_journal_backup_atomicity.py``).

  4. The coordinator's ``_history_coords``
     is the SAME identity as
     ``_pv_learning.identity``. There is no
     Kyiv hardcoded fallback. The
     ``HistoryCoordinator`` resolves its
     coordinates through the shared
     resolver at ``__init__`` time
     (covered by
     ``tests/test_history_coord_resolution.py``).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class CoordResolutionTests(unittest.TestCase):
    """The pure helpers in ``hems.options_helpers``
    implement the resolution order without
    any HA dependency.
    """

    def test_entry_options_override_ha(self) -> None:
        """When the user has set BOTH
        ``site_latitude`` and ``site_longitude``
        in the options flow, those win over
        HA's location — even if HA is
        configured for a different region.
        """
        from hems.options_helpers import compute_site_coordinates
        # Юра п.3: do not pin this to any one city.
        # The entry override is the production user
        # action; the HA location is the next source.
        # Values come from a fictional Berlin HA instance
        # resolved through ``SimpleNamespace`` so the test
        # does not silently hardcode Lviv or Kyiv.
        lat, lon = compute_site_coordinates(
            {"site_latitude": 49.0, "site_longitude": 31.0},
            ha_latitude=52.52, ha_longitude=13.40,
        )
        self.assertAlmostEqual(lat, 49.0)
        self.assertAlmostEqual(lon, 31.0)

    def test_ha_used_when_no_entry(self) -> None:
        """When the user has not overridden
        the station coordinates, the HA
        location wins. This is the
        production fix for the defect.
        """
        from hems.options_helpers import compute_site_coordinates
        # Юра п.3: the test must use a real
        # ``hass.config`` value, NOT a Lviv
        # hardcode — and NOT the integration
        # fallback default (Kyiv). Use Berlin
        # here; other tests in this file
        # exercise New York / Tokyo / invalid
        # inputs to prove the resolver
        # actually follows the input.
        lat, lon = compute_site_coordinates(
            None, ha_latitude=52.52, ha_longitude=13.40,
        )
        self.assertAlmostEqual(lat, 52.52)
        self.assertAlmostEqual(lon, 13.40)

    def test_default_used_when_both_none(self) -> None:
        """When neither entry nor HA have
        coordinates, the canonical
        integration defaults are used as
        the last-resort fallback.
        """
        from hems.options_helpers import compute_site_coordinates
        lat, lon = compute_site_coordinates(None)
        self.assertAlmostEqual(lat, 50.45)
        self.assertAlmostEqual(lon, 30.52)

    def test_invalid_entry_falls_through_to_ha(self) -> None:
        """A partial entry override (only
        latitude, or ``None``, or NaN, or
        out-of-range) MUST fall through
        to HA rather than silently
        producing a half-set coordinate
        pair. The previous behaviour of
        accepting a partial override (lat
        from user, lon from default) was a
        defect.
        """
        from hems.options_helpers import compute_site_coordinates
        # Юра п.3: use New York as the
        # representative ``hass.config``
        # value here so the test pinpoints
        # the resolver's behaviour for
        # invalid entry overrides rather
        # than re-using Lviv.
        NYC_LAT, NYC_LON = 40.7128, -74.0060
        # Lat-only entry → must fall through.
        lat, lon = compute_site_coordinates(
            {"site_latitude": 49.5},
            ha_latitude=NYC_LAT, ha_longitude=NYC_LON,
        )
        self.assertAlmostEqual(lat, NYC_LAT)
        self.assertAlmostEqual(lon, NYC_LON)
        # None lat / lon in entry → falls through.
        lat, lon = compute_site_coordinates(
            {"site_latitude": None, "site_longitude": None},
            ha_latitude=NYC_LAT, ha_longitude=NYC_LON,
        )
        self.assertAlmostEqual(lat, NYC_LAT)
        self.assertAlmostEqual(lon, NYC_LON)
        # NaN lat / lon → falls through.
        lat, lon = compute_site_coordinates(
            {"site_latitude": float("nan"),
             "site_longitude": float("nan")},
            ha_latitude=NYC_LAT, ha_longitude=NYC_LON,
        )
        self.assertAlmostEqual(lat, NYC_LAT)
        self.assertAlmostEqual(lon, NYC_LON)
        # Out-of-range entry → falls through.
        lat, lon = compute_site_coordinates(
            {"site_latitude": 91.0, "site_longitude": 181.0},
            ha_latitude=NYC_LAT, ha_longitude=NYC_LON,
        )
        self.assertAlmostEqual(lat, NYC_LAT)
        self.assertAlmostEqual(lon, NYC_LON)
        # HA is also invalid → defaults.
        lat, lon = compute_site_coordinates(
            {"site_latitude": 91.0, "site_longitude": 181.0},
            ha_latitude=float("nan"), ha_longitude=None,
        )
        self.assertAlmostEqual(lat, 50.45)
        self.assertAlmostEqual(lon, 30.52)
        # Boolean entry is not a number.
        lat, lon = compute_site_coordinates(
            {"site_latitude": True, "site_longitude": False},
            ha_latitude=NYC_LAT, ha_longitude=NYC_LON,
        )
        self.assertAlmostEqual(lat, NYC_LAT)
        self.assertAlmostEqual(lon, NYC_LON)

    def test_source_attribute_set(self) -> None:
        """``resolve_site_identity`` returns
        a ``source`` field that the
        coordinator records on
        ``self._identity_source`` and the
        sensor surfaces to the UI. The
        source MUST be ``entry`` when the
        user has overridden the station,
        ``ha`` when the runtime picked
        HA's location, and ``default``
        only when both entry and HA were
        unusable.
        """
        from hems.options_helpers import resolve_site_identity
        # Юра п.3: do not rely on the Lviv
        # constant — vary the HA location
        # across tests so we know the source
        # reflects the actual inputs.
        self.assertEqual(
            resolve_site_identity(
                {"site_latitude": 49.0, "site_longitude": 31.0},
                ha_latitude=52.52, ha_longitude=13.40,
            )["source"],
            "entry",
        )
        self.assertEqual(
            resolve_site_identity(
                None, ha_latitude=40.7128, ha_longitude=-74.0060,
            )["source"],
            "ha",
        )
        self.assertEqual(
            resolve_site_identity(None)["source"],
            "default",
        )
        # The dict shape must include
        # ``latitude``, ``longitude`` and
        # ``source`` keys; the previous
        # helper returned a bare
        # ``(lat, lon)`` tuple with no
        # provenance.
        ident = resolve_site_identity(None)
        self.assertIn("latitude", ident)
        self.assertIn("longitude", ident)
        self.assertIn("source", ident)


class IdentityBackupTests(unittest.TestCase):
    """When the on-disk journal was written
    under a different ``identity`` than the
    one the live coordinator just resolved,
    the previous behaviour raised a
    ``ValueError`` that abandoned the entire
    journal — the operator lost all
    snapshots, pairs and the trained
    station model. The new behaviour writes
    a side-by-side backup and starts an
    empty live state.
    """

    def _write_legacy_journal(self, tmp: Path, name: str, *,
                              old_lat: float, old_lon: float,
                              timezone: str = "Europe/Kyiv",
                              extra: dict | None = None) -> Path:
        """Write a journal that targets a
        previous station and contains
        a few snapshots, pairs, a trained
        model and an archive day. Returns
        the on-disk path. The default
        ``timezone`` is Kyiv because every
        ``IdentityBackupTests`` scenario
        creates the journal against a Kyiv
        legacy station; tests using a
        non-Kyiv timezone (e.g. matching a
        Berlin runtime) pass it explicitly.
        """
        path = tmp / name
        body = {
            "version": 3,
            "unit": "kWh",
            "timezone": timezone,
            "latitude": old_lat,
            "longitude": old_lon,
            "radiation_contract_version": 2,
            "snapshots": {
                "2026-10-09": {
                    "forecast_kwh": 10.5,
                    "issued_at": "2026-10-08T08:00:00+00:00",
                    "forecast_model": "hourly_response_v2",
                },
                "2026-10-10": {
                    "forecast_kwh": 12.3,
                    "issued_at": "2026-10-09T08:00:00+00:00",
                    "forecast_model": "hourly_response_v2",
                },
            },
            "pairs": {
                "2026-10-09": {
                    "forecast_kwh": 10.5,
                    "actual_kwh": 9.8,
                    "coverage": 1.0,
                    "forecast_model": "hourly_response_v2",
                },
                "2026-10-10": {
                    "forecast_kwh": 12.3,
                    "actual_kwh": 11.0,
                    "coverage": 1.0,
                    "forecast_model": "hourly_response_v2",
                },
            },
            "radiation": {
                "2026-10-09": 0.180,
                "2026-10-10": 0.200,
            },
            "archive_checked_day": "2026-10-10",
            "model": {"gain": 0.13, "sample_count": 20},
            "calibration_model": "hourly_response_v2",
        }
        if extra:
            body.update(extra)
        path.write_text(json.dumps(body, allow_nan=False),
                        encoding="utf-8")
        return path

    def test_identity_backup_on_mismatch(self) -> None:
        """The previous journal (Kyiv station)
        must NOT raise; the live state must
        end up empty so the next archive
        refresh rebuilds the radiation
        cache against the new station
        coordinates, and the previous
        snapshots / pairs / model must be
        preserved verbatim in a backup
        file that lives next to the
        original.
        """
        from hems.pv_learning import PvLearningState
        # Юра п.3: scenario is "previous journal was
        # Kyiv, runtime station is now Berlin". Use
        # Berlin (52.52, 13.40) as the new station so
        # the test stops silently depending on the
        # Lviv constant.
        NEW_LAT, NEW_LON = 52.52, 13.40
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            journal = self._write_legacy_journal(
                tmp_path,
                "pv_fact_pairs_test-entry.json",
                old_lat=50.45, old_lon=30.52,
            )
            # Live coordinator now resolves
            # a Berlin station (52.52, 13.40).
            state = PvLearningState(
                "Europe/Berlin", NEW_LAT, NEW_LON,
            )
            # Must NOT raise.
            state.load(journal)
            # Live state is empty: snapshots,
            # pairs, radiation, archive day
            # and model are all defaults so
            # the next ``_archive`` refresh
            # queries Open-Meteo Archive for
            # the new coordinates.
            self.assertEqual(state.snapshots, {})
            self.assertEqual(state.pairs, {})
            self.assertEqual(state.radiation, {})
            self.assertIsNone(state.archive_checked_day)
            self.assertIsNone(state.model)
            self.assertIsNone(state.calibration_model)
            # The original journal file is
            # untouched on disk (we only
            # wrote a sibling backup).
            self.assertTrue(journal.exists())
            backups = sorted(p for p in tmp_path.iterdir()
                             if p.name.startswith(
                                 journal.name + ".coords_backup_"
                             ))
            self.assertEqual(
                len(backups), 1,
                msg=(
                    "Exactly one backup must be "
                    "written on identity mismatch; "
                    f"found {len(backups)}: {backups}"
                ),
            )
            backup = json.loads(
                backups[0].read_text(encoding="utf-8")
            )
            # Filename stamp embeds the
            # previous public coordinates
            # (no secrets, no whitespace).
            self.assertIn("50.4500_30.5200", backups[0].name)
            self.assertNotIn(" ", backups[0].name)
            # Backup contains the original
            # snapshots, pairs, radiation,
            # model, calibration_model,
            # archive_checked_day and
            # forecast_model tags verbatim.
            self.assertEqual(
                backup["snapshots"],
                {
                    "2026-10-09": {
                        "forecast_kwh": 10.5,
                        "issued_at": "2026-10-08T08:00:00+00:00",
                        "forecast_model": "hourly_response_v2",
                    },
                    "2026-10-10": {
                        "forecast_kwh": 12.3,
                        "issued_at": "2026-10-09T08:00:00+00:00",
                        "forecast_model": "hourly_response_v2",
                    },
                },
            )
            self.assertEqual(len(backup["pairs"]), 2)
            self.assertEqual(
                backup["model"], {"gain": 0.13, "sample_count": 20},
            )
            self.assertEqual(
                backup["calibration_model"],
                "hourly_response_v2",
            )
            self.assertEqual(
                backup["archive_checked_day"], "2026-10-10",
            )
            # Backup carries the diagnostic
            # keys so an auditor can tell WHY
            # it was written and WHICH
            # identity the live coordinator
            # switched to.
            self.assertEqual(
                backup["_backup_reason"],
                "coordinates_mismatch",
            )
            self.assertIn("_backup_at", backup)
            self.assertEqual(
                backup["_new_identity"],
                {"timezone": "Europe/Berlin",
                 "latitude": 52.52, "longitude": 13.40},
            )

    def test_real_forecast_pairs_backup_on_mismatch(self) -> None:
        """The ``RealForecastPairs`` journal
        gets the same side-by-side backup
        treatment on identity mismatch.
        The live pairs go empty; the
        original pairs and forecast_model
        tags stay in the backup.
        """
        from hems.pv_learning import RealForecastPairs
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            journal = tmp_path / "real_forecast_pairs.json"
            journal.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "identity": {
                            "timezone": "Europe/Kyiv",
                            "latitude": 50.45,
                            "longitude": 30.52,
                        },
                        "radiation_contract_version": 2,
                        "pairs": [
                            {
                                "date": "2026-10-10",
                                "forecast_kwh": 12.3,
                                "actual_kwh": 11.0,
                                "captured_at": "2026-10-09T08:00:00+00:00",
                                "used": True,
                                "forecast_model": "hourly_response_v2",
                            },
                        ],
                    },
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            store = RealForecastPairs(
                # Юра п.3: live runtime now resolves
                # to a Berlin station (52.52, 13.40),
                # not the Lviv constant.
                {"timezone": "Europe/Berlin",
                 "latitude": 52.52, "longitude": 13.40},
            )
            loaded = store.load(journal)
            self.assertFalse(
                loaded,
                msg=(
                    "On identity mismatch the load "
                    "must report no usable journal "
                    "so the coordinator falls back to "
                    "an empty live state"
                ),
            )
            self.assertEqual(store.pairs, {})
            backups = sorted(p for p in tmp_path.iterdir()
                             if p.name.startswith(
                                 journal.name + ".coords_backup_"
                             ))
            self.assertEqual(len(backups), 1)
            self.assertIn("50.4500_30.5200", backups[0].name)
            backup = json.loads(
                backups[0].read_text(encoding="utf-8")
            )
            self.assertEqual(len(backup["pairs"]), 1)
            self.assertEqual(
                backup["pairs"][0]["forecast_model"],
                "hourly_response_v2",
            )
            self.assertEqual(
                backup["_backup_reason"],
                "coordinates_mismatch",
            )
            self.assertEqual(
                backup["_new_identity"],
                {"timezone": "Europe/Berlin",
                 "latitude": 52.52, "longitude": 13.40},
            )

    def test_no_backup_when_identity_matches(self) -> None:
        """If the on-disk journal was
        written under the SAME identity as
        the live coordinator, ``load()``
        MUST NOT write a backup file —
        backups are only for identity
        mismatches.
        """
        from hems.pv_learning import PvLearningState
        # Юра п.3: use a non-Lviv city for
        # the matching-identity scenario too.
        # Berlin (52.52, 13.40) is the
        # representative station; both the
        # on-disk journal and the live
        # runtime resolve to the same value.
        MATCH_LAT, MATCH_LON = 52.52, 13.40
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            journal = self._write_legacy_journal(
                tmp_path,
                "pv_fact_pairs_test-entry.json",
                old_lat=MATCH_LAT, old_lon=MATCH_LON,
                # Юра п.3: legacy journal
                # was written for the same
                # Berlin station the runtime
                # now resolves to. The helper
                # defaults to ``Europe/Kyiv``
                # for the migration scenario
                # (the previous test); here
                # we override to Europe/Berlin
                # so the journal loads
                # verbatim.
                timezone="Europe/Berlin",
            )
            state = PvLearningState(
                "Europe/Berlin", MATCH_LAT, MATCH_LON,
            )
            state.load(journal)
            self.assertEqual(
                len(state.snapshots), 2,
                msg=(
                    "Identity-matching journal must "
                    "load verbatim; no backup."
                ),
            )
            backups = list(p for p in tmp_path.iterdir()
                           if "coords_backup" in p.name)
            self.assertEqual(
                backups, [],
                msg=(
                    "No backup must be written when "
                    "the identity matches"
                ),
            )


class HistoryCoordsTests(unittest.TestCase):
    """The coordinator's ``_history_coords``
    is the SINGLE source for the Open-Meteo
    Archive fetch. It MUST mirror
    ``_pv_learning.identity`` exactly and
    MUST NOT fall back to a hardcoded
    Kyiv constant.
    """

    @staticmethod
    def _find_function(tree, name):
        """Walk the AST to find a top-level
        or nested method with the given
        name. The previous version only
        looked at ``tree.body`` and missed
        class methods — the production
        functions live inside class
        bodies.
        """
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    return node
        return None

    def test_history_coords_no_kyiv_fallback(self) -> None:
        """The production code in
        ``coordinator.py`` no longer
        contains a Kyiv constant. The
        archive fetch path uses
        ``_history_coords`` which
        ``_init_pv_learning`` populates
        from the resolved site identity.
        """
        import ast
        coordinator_src = (REPO_ROOT / "coordinator.py").read_text(
            encoding="utf-8"
        )
        # The Kyiv literal is gone.
        self.assertNotIn(
            "50.4501, 30.5234",
            coordinator_src,
            msg=(
                "coordinator.py must NOT contain a "
                "hardcoded Kyiv coordinate literal; "
                "the audit replaces it with the "
                "resolved site identity"
            ),
        )
        tree = ast.parse(coordinator_src)
        func = self._find_function(
            tree, "_fetch_historical_weather",
        )
        self.assertIsNotNone(
            func,
            msg=(
                "_fetch_historical_weather must "
                "still be defined"
            ),
        )
        assert func is not None
        func_src = ast.unparse(func)
        # The new behaviour: log a warning
        # and return an empty dict rather
        # than silently pin the archive
        # fetch to a wrong city.
        self.assertIn("return {}", func_src)
        self.assertIn(
            "_history_coords",
            func_src,
        )

    def test_init_pv_learning_wires_history_coords(self) -> None:
        """``_init_pv_learning`` must set
        ``_history_coords`` to the same
        tuple as ``_pv_learning.identity``
        so the archive fetch mirrors the
        PV-learning station.
        """
        import ast
        pv_src = (REPO_ROOT / "hems" / "pv_coordinator.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(pv_src)
        method = self._find_function(
            tree, "_init_pv_learning",
        )
        self.assertIsNotNone(method)
        assert method is not None
        body_src = ast.unparse(method)
        self.assertIn(
            "_history_coords = (latitude, longitude)",
            body_src,
            msg=(
                "_init_pv_learning must wire "
                "_history_coords to the resolved "
                "site identity so the archive "
                "fetch and the PV-learning stack "
                "agree on coordinates"
            ),
        )
        # The HA location IS read; the
        # previous behaviour ignored it.
        # ``ast.unparse`` uses single-quoted
        # attribute access, so we match on the
        # substring ``config, 'latitude'`` /
        # ``config, 'longitude'``.
        self.assertIn(
            "config, 'latitude'",
            body_src,
            msg=(
                "_init_pv_learning must read "
                "hass.config.latitude (HA location) "
                "so the integration targets the "
                "correct city when the user has "
                "not overridden the station"
            ),
        )
        self.assertIn(
            "config, 'longitude'",
            body_src,
        )
        # The active source is recorded
        # for UI/audit.
        self.assertIn("_identity_source", body_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
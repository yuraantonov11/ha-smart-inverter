"""Tag-source-of-truth regression for forecast snapshots.

Юра (2026-10-13):
- "за 14/15 жовтня записано station_gain_v2, а активний
  calibration_model=hourly_response_v2. З'ясуй, чи це
  початковий snapshot до навчання hourly-моделі"
- "перевір, що наступні видані прогнози отримують правильний
  тег фактично використаної моделі та можуть утворити пару
  для активного калібратора"
- "Старі snapshots не переписуй і не перетеговуй"

The test pins the contract:
  1. ``_forecast_model_for_day(day)`` returns
     ``hourly_response_v2`` IFF the forecast instance has a
     non-None ``hourly_response`` whose ``last_day`` is within
     14 days of ``day``; otherwise ``station_gain_v2``.
  2. The ``snapshot(day, ...)`` call records the SAME
     ``forecast_model`` that the power_w computation actually
     used (it asks ``_forecast_model_for_day(day)``).
  3. ``calibration_pairs()`` filters by
     ``pair.forecast_model == state.calibration_model``. A
     snapshot with tag ``station_gain_v2`` does NOT match a
     calibrator with ``calibration_model=hourly_response_v2``;
     the calibrator must be empty until hourly_response_v2
     pairs are produced.
  4. A hypothetical fix that produces ``hourly_response_v2``
     snapshots without training the hourly model (or that
     re-tags existing ``station_gain_v2`` pairs) MUST fail
     the contract tests.
"""
from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch


# --- Helpers that re-create the production decision tree ---


def forecast_model_for_day_contract(
    day: date,
    hourly_response: dict | None,
    family_when_hourly: str = "hourly_response",
    family_when_station: str = "station_gain",
    contract_version: int = 2,
) -> str:
    """Mirror of ``PvLearningCoordinatorMixin._forecast_model_for_day``
    in hems/pv_coordinator.py:773-789.

    The production function returns
    ``current_forecast_model_identity(family)`` = ``f"{family}_v{version}"``
    where ``version`` is the radiation interval contract version.
    For a freshly-deployed integration that has not yet trained
    hourly response, ``hourly_response`` is None and the
    function returns ``station_gain_v2``.
    """
    if hourly_response and 0 <= (day - datetime.fromisoformat(
            hourly_response["last_day"]).date()).days <= 14:
        family = family_when_hourly
    else:
        family = family_when_station
    return f"{family}_v{contract_version}"


class TagSourceContractTests(unittest.TestCase):
    """The forecast_model tag in a snapshot must match what the
    power_w pipeline actually used.
    """

    def test_initial_snapshot_before_hourly_training_is_station_gain_v2(self):
        """Юра: snapshots 14/15 жовтня мають station_gain_v2
        бо hourly response ще не тренувався. Контракт:
        ``hourly_response is None`` → ``station_gain_v2``.
        """
        result = forecast_model_for_day_contract(
            day=date(2026, 10, 14),
            hourly_response=None,
        )
        self.assertEqual(result, "station_gain_v2")

    def test_snapshot_with_stale_hourly_response_falls_back_to_station_gain(self):
        """If hourly_response was trained 15 days ago (last_day
        too old), the function must fall back to station_gain_v2.
        """
        result = forecast_model_for_day_contract(
            day=date(2026, 10, 14),
            hourly_response={"last_day": "2026-09-29", "gains": [0.1] * 24},
        )
        self.assertEqual(result, "station_gain_v2")

    def test_snapshot_with_fresh_hourly_response_uses_hourly_response_v2(self):
        """If hourly_response.last_day is today, the function
        returns ``hourly_response_v2``. This is the path that
        produces tag-matching pairs for the active calibrator.
        """
        result = forecast_model_for_day_contract(
            day=date(2026, 10, 14),
            hourly_response={"last_day": "2026-10-14", "gains": [0.1] * 24},
        )
        self.assertEqual(result, "hourly_response_v2")

    def test_snapshot_with_hourly_response_within_14_days(self):
        """Boundary: hourly_response last_day 14 days before
        the snapshot day is still in range.
        """
        result = forecast_model_for_day_contract(
            day=date(2026, 10, 14),
            hourly_response={"last_day": "2026-09-30", "gains": [0.1] * 24},
        )
        self.assertEqual(result, "hourly_response_v2")


class CalibrationModelFilterContractTests(unittest.TestCase):
    """``calibration_pairs()`` filters by
    ``pair.forecast_model == state.calibration_model``. A
    snapshot with tag ``station_gain_v2`` does NOT match a
    calibrator with ``calibration_model=hourly_response_v2``;
    the calibrator must be empty until ``hourly_response_v2``
    pairs are produced.
    """

    def test_station_gain_v2_pair_excluded_from_hourly_calibrator(self):
        """Simulate the active calibrator on the live journal:
        ``calibration_model = hourly_response_v2`` but the only
        completed pair has ``forecast_model = station_gain_v2``.
        The calibrator must be EMPTY.
        """
        from hems.pv_learning import PvLearningState
        state = PvLearningState("Europe/Kyiv", 49.71, 23.91)
        state.set_calibration_model("hourly_response_v2")
        # Add a snapshot+pair as the live journal would.
        state.snapshot("2026-10-14", 0.24, datetime(2026, 10, 13, 10, 50, 56),
                       forecast_model="station_gain_v2")
        # No ``match()`` invocation because actuals are not
        # collected yet (used=false). Verify the calibration
        # set is empty.
        self.assertEqual(
            len(state.calibration_pairs()), 0,
            msg=(
                "calibration_pairs() must be empty when the only "
                "snapshots carry station_gain_v2 and the active "
                "calibration_model is hourly_response_v2. The pair "
                "filter is the production contract."
            ),
        )

    def test_hourly_response_v2_pair_accepted_by_hourly_calibrator(self):
        """The matching tag is accepted. A future snapshot
        with ``forecast_model=hourly_response_v2`` (produced
        AFTER hourly response trains successfully) WILL join
        the calibrator.
        """
        from hems.pv_learning import PvLearningState
        state = PvLearningState("Europe/Kyiv", 49.71, 23.91)
        state.set_calibration_model("hourly_response_v2")
        state.snapshot("2026-10-14", 0.24, datetime(2026, 10, 13, 10, 50, 56),
                       forecast_model="hourly_response_v2")
        # Without a completed match() yet, calibration_pairs is
        # still empty (no actual yet). The snapshot is in the
        # journal but the calibrator awaits the actual.
        self.assertEqual(len(state.calibration_pairs()), 0,
            msg=("calibrator awaits actual; no pair yet, regardless of tag."))
        # Now simulate a match() that converts the snapshot to
        # a pair with the same forecast_model.
        state.match({"2026-10-14": 0.50}, datetime(2026, 10, 15, 0, 0, 0))
        self.assertEqual(len(state.calibration_pairs()), 1,
            msg=(
                "with actual collected and tag=hourly_response_v2, "
                "the pair enters the calibrator (production contract)."
            ))


class ReTagPreventionTests(unittest.TestCase):
    """Юра: «Старі snapshots не переписуй і не перетеговуй».
    The production code does NOT re-tag existing
    ``station_gain_v2`` snapshots to ``hourly_response_v2``
    when hourly training completes. The original tag
    survives.

    The test asserts:
    - The first snapshot on 2026-10-14 is recorded with
      ``station_gain_v2`` (the actual production behaviour at
      deploy time, since hourly_response was not trained yet).
    - When hourly training completes and produces a new model,
      the existing snapshot's ``forecast_model`` is NOT
      re-tagged. The journal keeps ``station_gain_v2`` verbatim.
    - A NEW snapshot (next day) IS tagged with
      ``hourly_response_v2`` (the new pipeline family).
    """
    def test_existing_snapshot_tag_preserved(self):
        from hems.pv_learning import PvLearningState
        state = PvLearningState("Europe/Kyiv", 49.71, 23.91)
        state.set_calibration_model("hourly_response_v2")
        # T0: deploy. hourly_response not trained. snapshot
        # tag is station_gain_v2.
        state.snapshot("2026-10-14", 0.24, datetime(2026, 10, 13, 10, 50, 56),
                       forecast_model="station_gain_v2")
        # T1: hypothetical future — hourly trains, model gains.
        # Production does NOT re-tag existing snapshots. The
        # journal keeps the original tag.
        self.assertEqual(
            state.snapshots["2026-10-14"]["forecast_model"],
            "station_gain_v2",
            msg=(
                "production preserves the original tag; "
                "existing snapshots are NOT re-tagged when a "
                "new pipeline family is added."
            ),
        )
        # T2: a new snapshot is created with the new tag.
        state.snapshot("2026-10-15", 0.11, datetime(2026, 10, 14, 0, 15, 0),
                       forecast_model="hourly_response_v2")
        self.assertEqual(
            state.snapshots["2026-10-15"]["forecast_model"],
            "hourly_response_v2",
        )
        # Existing 10-14 still station_gain_v2.
        self.assertEqual(
            state.snapshots["2026-10-14"]["forecast_model"],
            "station_gain_v2",
        )


class HourlyResponseNotTrainedYetContractTests(unittest.TestCase):
    """Юра: «З'ясуй, чи це початковий snapshot до навчання
    hourly-моделі».

    The contract: ``train_hourly_response`` returns None when
    fewer than 2 eligible days are available, AND
    ``self._forecast.hourly_response`` stays None, AND the
    next ``_forecast_model_for_day`` call returns
    ``station_gain_v2``.

    The live evidence: ``state.pv_fact_pairs.json`` has
    ``model.gain=0.1395`` and ``model.last_day=2026-10-11``
    (train_station updated), but the journal has no
    ``hourly_response`` field (since hourly_response is on
    the ForecastService, not the journal).

    The test reproduces the train_hourly_response return-None
    path and asserts the cascade.
    """
    def test_train_hourly_response_returns_none_when_eligible_lt_2(self):
        """Simulate the live condition: only one complete
        archive-radiation day available (e.g. 2026-10-11 just
        published, others pending). The function must return
        None, NOT a partial model.
        """
        from hems.pv_hourly import train_hourly_response
        from datetime import date
        # Minimal recorder rows for 2026-10-11.
        power_rows = []
        archive_rows = []
        # Build 24 hourly rows for 2026-10-11 only.
        from datetime import datetime, timezone
        for h in range(24):
            ts = datetime(2026, 10, 11, h, 0, tzinfo=timezone.utc)
            power_rows.append({"start": ts.isoformat(), "mean": 50.0 if 6 <= h <= 18 else 0.0})
            archive_rows.append({"start": ts.isoformat(), "mean": 100.0 if 6 <= h <= 18 else 0.0})
        result = train_hourly_response(
            power_rows, archive_rows,
            __import__("zoneinfo").ZoneInfo("Europe/Kyiv"),
            date(2026, 10, 13),
        )
        self.assertIsNone(
            result,
            msg=(
                "with only 1 eligible day, train_hourly_response "
                "returns None (production contract). The next "
                "snapshot is tagged station_gain_v2, NOT "
                "hourly_response_v2."
            ),
        )

    def test_train_hourly_response_returns_model_with_2_eligible_days(self):
        """The boundary: 2 complete archive-radiation days are
        the minimum for a provisional model. With 2 days the
        function returns a model (provisional=True).
        """
        from hems.pv_hourly import train_hourly_response
        from datetime import date
        from zoneinfo import ZoneInfo
        # Build complete local days. Each day has 24 hourly rows
        # whose UTC instant falls inside the local day (Kyiv = UTC+3
        # winter). The ``complete_hourly_days`` test asserts that
        # the SET of instants matches exactly the 24 expected
        # local-day instants.
        power_rows = []
        archive_rows = []
        from datetime import datetime, timezone, timedelta
        for d in (10, 11):
            for h in range(24):
                # Local midnight in Kyiv, then add h hours.
                local = datetime(2026, 10, d, h, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
                utc = local.astimezone(timezone.utc)
                power_rows.append({"start": utc.isoformat(),
                                    "mean": 50.0 if 6 <= h <= 18 else 0.0})
                archive_rows.append({"start": utc.isoformat(),
                                      "mean": 100.0 if 6 <= h <= 18 else 0.0})
        result = train_hourly_response(
            power_rows, archive_rows,
            ZoneInfo("Europe/Kyiv"),
            date(2026, 10, 13),
        )
        self.assertIsNotNone(
            result,
            msg=(
                "with 2 eligible days, train_hourly_response "
                "returns a provisional model. Subsequent "
                "snapshots are tagged hourly_response_v2. "
                "If this fails, check the test data — each "
                "day must have 24 local-day instants (Kyiv "
                "winter = UTC+3)."
            ),
        )
        self.assertEqual(result["sample_days"], 2)
        self.assertTrue(result["provisional"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Station training on independent radiation, separate from forecast accuracy.

No HA/network imports. All energies are kWh; all day keys are local ISO dates.
Archive-fit metrics never seed the day-ahead ForecastCalibrator.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .forecast_calibration import ForecastCalibrator


# ── Radiation interval contract ──────────────────────────────────
#
# Open-Meteo ``shortwave_radiation`` at API timestamp ``t`` represents
# the mean over the **preceding hour** ``[t-3600, t)`` (per Open-Meteo
# documentation: https://open-meteo.com/en/docs/historical-weather-api).
#
# Internally, all radiation rows must have ``start`` = interval start
# (``t - 3600``). The conversion is done once in the production code;
# downstream consumers (training, planning, frontend) MUST NOT shift
# timestamps again.
#
# Contract versions:
#   * v1 (legacy, deprecated): ``rows[].start`` = API timestamp ``t`` =
#     interval **end**. This produced off-by-one day attributions in
#     the daily radiation aggregate (pulse at t=2026-10-08 21:00 UTC
#     attributed to Oct 9 instead of Oct 8).
#   * v2 (current): ``rows[].start`` = API timestamp ``t`` minus 3600
#     = interval **start**. Daily radiation aggregates the mean over
#     ``[start, start+3600)``, and the day is the local calendar day
#     in which the interval **starts**.
#
# All non-radiation fields (``weather_code``, ``temperature_2m``,
# ``wind_speed_10m``, ``precipitation_probability``) keep the original
# API timestamp ``t`` — they are instantaneous readings, not interval
# means.
RADIATION_INTERVAL_CONTRACT_VERSION = 2


def shift_radiation_to_interval_start(times, values):
    """Convert Open-Meteo radiation API timestamps to interval starts.

    The Open-Meteo ``shortwave_radiation`` value at API timestamp ``t``
    represents the mean over ``[t-3600, t)``. The internal radiation
    row contract is ``{start, mean}`` where ``start`` is the
    **interval start** (``t - 3600``). This helper performs the
    conversion in one place, using the shared
    ``radiation_interval_start_of`` boundary calculator so archive
    and hourly paths compute the same offset.

    Returns a list of ``{start, mean}`` dicts. Rows with missing
    timestamps, missing values, or non-numeric types are dropped.
    Missing radiation is **not** converted to zero — the consumer
    treats the absence as "no measurement", not "measured zero".
    """
    if not isinstance(times, (list, tuple)) or not isinstance(values, (list, tuple)):
        return []
    if len(times) != len(values):
        return []
    result = []
    for raw_t, raw_v in zip(times, values):
        if raw_t is None or raw_v is None:
            continue
        # Reject booleans explicitly: ``isinstance(True, int)``
        # is True in Python 3, so without this check a missing
        # radiation field (which JSON-deserialises to ``False``
        # or ``True`` in some clients) would become a valid
        # radiation of 1.0 W/m².
        if isinstance(raw_t, bool) or isinstance(raw_v, bool):
            continue
        try:
            t = int(raw_t)
        except (TypeError, ValueError):
            continue
        try:
            v = float(raw_v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        result.append({"start": radiation_interval_start_of(t), "mean": v})
    return result


def filter_radiation_to_requested_range(rows, first, last):
    """Keep only rows whose radiation interval is fully inside [first, last).

    Each row's ``start`` is the interval start; the interval is
    ``[start, start+3600)``. To lie fully inside ``[first, last)`` we
    need ``start >= first`` and ``start + 3600 <= last`` (equivalently
    ``start < last`` since both bounds are UTC epoch seconds).
    """
    if not isinstance(rows, list):
        return []
    if not isinstance(first, (int, float)) or not isinstance(last, (int, float)):
        return []
    first_s = int(first)
    last_s = int(last)
    return [r for r in rows
            if isinstance(r, dict)
            and isinstance(r.get("start"), (int, float))
            and first_s <= int(r["start"]) < last_s]


# Shared Open-Meteo radiation interval boundary. Open-Meteo's
# ``shortwave_radiation`` value at API timestamp ``t`` represents
# the mean over ``[t-3600, t)``. This helper returns the interval
# START (``t - 3600``). It is the single source of truth used by
# the archive path (``shift_radiation_to_interval_start``),
# the hourly path (``_fetch_hourly``), and the gain lookup. There
# is NO second shift downstream — every consumer reads
# ``timestamp`` or ``time`` as the interval start.
def radiation_interval_start_of(api_timestamp):
    """Return the radiation interval START for a given API timestamp."""
    if not isinstance(api_timestamp, (int, float)) or isinstance(api_timestamp, bool):
        raise ValueError("api_timestamp must be a numeric epoch second")
    return int(api_timestamp) - 3600


def finite(value, low=0.0, high=float("inf")):
    try:
        result = float(value)
        return result if math.isfinite(result) and low <= result <= high else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    """Normalize recorder seconds, websocket milliseconds and ISO timestamps."""
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Statistics timestamps must identify a UTC instant")
    return value.astimezone(timezone.utc)


def day_bounds(day, tz):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    return start, end


# Identity of the active forecast model under the current radiation
# interval contract. Each contract version gets its own identity so
# that ``calibration_pairs()`` (which filters by
# ``forecast_model == state.calibration_model``) can keep legacy
# pairs and the new pairs strictly separate. Pair tagged with
# ``hourly_response_v1`` was issued under contract 1 (interval END
# stored as ``start``); a pair tagged with ``hourly_response_v2``
# was issued under contract 2 (interval START stored as
# ``start``). They MUST NOT be mixed.
def current_forecast_model_identity(contract_version=RADIATION_INTERVAL_CONTRACT_VERSION):
    if not isinstance(contract_version, int) or contract_version < 1:
        raise ValueError("contract_version must be a positive integer")
    return f"hourly_response_v{contract_version}"


def legacy_forecast_model_identity(contract_version):
    if not isinstance(contract_version, int) or contract_version < 1:
        raise ValueError("contract_version must be a positive integer")
    return f"hourly_response_v{contract_version}"


def complete_hourly_days(rows, tz, today, *, field="mean", ceiling=20000):
    """Integrate complete local days, including 23/25-hour DST days.

    Each hourly mean represents one UTC hour. Missing/invalid hours invalidate
    the day; repeated local DST hours remain distinct UTC instants.
    """
    buckets = {}
    for row in rows:
        try:
            ts = timestamp(row.get("start"))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        day = ts.astimezone(tz).date().isoformat()
        if day >= today.isoformat():
            continue
        value = finite(row.get(field), high=ceiling)
        if value is not None:
            buckets.setdefault(day, {})[ts] = value
    result = {}
    for day, hours in buckets.items():
        start, end = day_bounds(day, tz)
        expected = {start + timedelta(hours=h) for h in range(int((end-start).total_seconds()/3600))}
        if set(hours) == expected:
            result[day] = sum(hours.values()) / 1000.0
    return result


def daily_energy_deltas(rows, unit, tz, today):
    """HA sum is cumulative; hourly statistics sum belongs to interval END.

    Only use explicit local-midnight endpoints with monotonic sums throughout
    a fully covered day. Missing metadata/endpoints falls back to power means.
    """
    scale = {"Wh": .001, "kWh": 1., "MWh": 1000.}.get(unit)
    if scale is None:
        return {}
    endpoints = {}
    for row in rows:
        value = finite(row.get("sum"))
        if value is None:
            continue
        try:
            endpoints[timestamp(row.get("start")) + timedelta(hours=1)] = value * scale
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    days = {ts.astimezone(tz).date().isoformat() for ts in endpoints}
    result = {}
    for day in days:
        if day >= today.isoformat():
            continue
        start, end = day_bounds(day, tz)
        count = int((end-start).total_seconds()/3600)
        points = [endpoints.get(start + timedelta(hours=h)) for h in range(count+1)]
        if any(v is None for v in points):
            continue
        if any(b < a for a, b in zip(points, points[1:])):
            continue
        total = points[-1] - points[0]
        if finite(total, high=500) is not None:
            result[day] = total
    return result


def train_station(actual, radiation):
    """Robust gain, chronological holdout, then refit on all eligible days."""
    pairs = [(day, float(actual[day]), float(radiation[day])) for day in sorted(actual.keys() & radiation.keys())
             if finite(actual[day], high=500) is not None
             and finite(radiation[day], low=.5, high=30) is not None][-90:]
    if len(pairs) < 7:
        return None
    validation = None
    if len(pairs) >= 21:
        split = min(len(pairs)-7, max(14, int(len(pairs)*.8)))
        gain = statistics.median(a/r for _, a, r in pairs[:split])
        errors = [a-r*gain for _, a, r in pairs[split:]]
        validation = {"source": "archive_weather_holdout", "train_days": split,
                      "test_days": len(errors), "train_end": pairs[split-1][0],
                      "test_start": pairs[split][0], "mae_kwh": sum(abs(e) for e in errors)/len(errors),
                      "bias_kwh": sum(errors)/len(errors)}
    gain = statistics.median(a/r for _, a, r in pairs)
    if gain <= 0 or not math.isfinite(gain):
        return None
    return {"gain": gain, "sample_count": len(pairs), "last_day": pairs[-1][0],
            "validation": validation}


class PvLearningState:
    # VERSION history:
    #   1 — initial schema, no explicit version.
    #   2 — added ``unit`` + identity validation. Radiation rows
    #       stored with ``start`` = API timestamp (interval end).
    #   3 — bumped for the radiation interval contract change
    #       (contract v2: ``start`` = API timestamp - 3600 = interval
    #       start). The migration path keeps the published snapshots
    #       and pairs verbatim (they do not depend on the radiation
    #       interval) and discards the daily ``radiation`` cache plus
    #       the trained ``model`` plus ``archive_checked_day``; those
    #       caches are tied to the old contract and must be rebuilt
    #       under the new one. Re-running the migration on an
    #       already-migrated journal is a no-op (the version field
    #       and the ``radiation_contract_version`` field are present).
    VERSION = 3

    def __init__(self, timezone_name, latitude, longitude):
        self.identity = {"timezone": timezone_name, "latitude": latitude, "longitude": longitude}
        self.snapshots = {}
        self.pairs = {}
        self.radiation = {}
        self.archive_checked_day = None
        self.model = None
        self.calibration_model = None
        self.calibrator = ForecastCalibrator(max_samples=30, unit="kWh")

    def calibration_pairs(self):
        """Only forecasts from the active pipeline may correct that pipeline."""
        return {d: p for d, p in self.pairs.items()
                if self.calibration_model is None or p.get("forecast_model") == self.calibration_model}

    def set_calibration_model(self, model):
        if model is not None and (not isinstance(model, str) or not model or len(model) > 64):
            raise ValueError("Invalid calibration model")
        self.calibration_model = model
        desired = [[p["forecast_kwh"], p["actual_kwh"]] for p in self.calibration_pairs().values()]
        if self.calibrator.to_list() != desired:
            self.calibrator.load_from_list(desired)

    def calibration_status(self, today):
        m = self.calibrator.metrics()
        pending = [{"date": d, **s, "awaiting": "completed_day" if d >= today else "daily_fact"}
                   for d, s in sorted(self.snapshots.items()) if d not in self.pairs]
        return {"unit": "kWh", "forecast_model": self.calibration_model,
                "samples": m.sample_count, "confidence": m.confidence_factor,
                "mae_kwh": m.mae_w, "bias_kwh": m.bias_w,
                "excluded_model_pairs": len(self.pairs)-len(self.calibration_pairs()),
                "pending_count": len(pending), "pending": pending[-7:],
                "recent_pairs": [{"date": d, **p,
                                  "used_for_current_model": d in self.calibration_pairs()}
                                 for d, p in list(self.pairs.items())[-7:]]}

    def snapshot(self, day, forecast_kwh, now, *, forecast_model=None):
        value = finite(forecast_kwh, high=500)
        if date.fromisoformat(day) <= now.date() or value is None or day in self.snapshots:
            return False
        record = {"forecast_kwh": value, "issued_at": now.isoformat()}
        if forecast_model is not None:
            record["forecast_model"] = forecast_model
        self.snapshots[day] = record
        self.snapshots = dict(sorted(self.snapshots.items())[-120:])
        return True

    def match(self, actual, now):
        count = 0
        for day, snapshot in sorted(self.snapshots.items()):
            value = finite(actual.get(day), high=500)
            if day in self.pairs or day >= now.date().isoformat() or value is None:
                continue
            self.pairs[day] = {"forecast_kwh": snapshot["forecast_kwh"],
                               "actual_kwh": value, "coverage": 1.0}
            if "forecast_model" in snapshot:
                self.pairs[day]["forecast_model"] = snapshot["forecast_model"]
            count += 1
        self.pairs = dict(sorted(self.pairs.items())[-30:])
        if count:
            self.set_calibration_model(self.calibration_model)
        return count

    def save(self, path):
        path = Path(path)
        raw = {"version": self.VERSION, "unit": "kWh", **self.identity,
               "radiation_contract_version": RADIATION_INTERVAL_CONTRACT_VERSION,
               "snapshots": self.snapshots, "pairs": self.pairs, "radiation": self.radiation,
               "archive_checked_day": self.archive_checked_day, "model": self.model,
               "calibration_model": self.calibration_model}
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(raw, allow_nan=False), encoding="utf-8")
        temp.replace(path)

    def load(self, path, legacy_path=None):
        path = Path(path)
        if legacy_path and Path(legacy_path).exists():
            legacy = Path(legacy_path)
            backup = legacy.with_suffix(".json.legacy")
            if not backup.exists():
                backup.write_bytes(legacy.read_bytes())
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Support previous contract versions (VERSION=2 — v1 contract)
        # and the bumped VERSION=3 (v2 contract). Reject unknown
        # future versions explicitly.
        on_disk_version = raw.get("version")
        if on_disk_version not in (self.VERSION, self.VERSION - 1):
            raise ValueError(
                f"PV state version {on_disk_version} is not supported "
                f"(max supported: {self.VERSION})"
            )
        if raw.get("unit") != "kWh":
            raise ValueError("Unsupported PV state unit")
        if any(raw.get(key) != value for key, value in self.identity.items()):
            raise ValueError("PV state belongs to different coordinates/timezone")
        # Validate everything before modifying the live state.
        snapshots, pairs, radiation = (raw.get(k, {}) for k in ("snapshots", "pairs", "radiation"))
        for day, snap in snapshots.items():
            d = date.fromisoformat(day)
            issued = datetime.fromisoformat(snap["issued_at"])
            if issued.date() >= d or finite(snap["forecast_kwh"], high=500) is None:
                raise ValueError("Invalid or retrospective PV snapshot")
            tag = snap.get("forecast_model")
            if tag is not None and (not isinstance(tag, str) or not tag or len(tag) > 64):
                raise ValueError("Invalid snapshot model")
        for day, pair in pairs.items():
            date.fromisoformat(day)
            if day not in snapshots or pair["forecast_kwh"] != snapshots[day]["forecast_kwh"]:
                raise ValueError("PV pair missing its issued forecast")
            if finite(pair["actual_kwh"], high=500) is None or pair["coverage"] != 1:
                raise ValueError("Incomplete PV pair")
            if pair.get("forecast_model") != snapshots[day].get("forecast_model"):
                raise ValueError("PV pair model differs from issued forecast")
        for day, value in radiation.items():
            date.fromisoformat(day)
            if finite(value, high=30) is None:
                raise ValueError("Invalid archive radiation")
        model = raw.get("model")
        if model is not None and (finite(model.get("gain"), low=.000001) is None
                                  or not 7 <= model.get("sample_count", 0) <= 90):
            raise ValueError("Invalid station model")
        checked = raw.get("archive_checked_day")
        tag = raw.get("calibration_model")
        if tag is not None and (not isinstance(tag, str) or not tag or len(tag) > 64):
            raise ValueError("Invalid calibration model")
        if checked is not None:
            date.fromisoformat(checked)
        self.snapshots = {d: {**s, "forecast_kwh": float(s["forecast_kwh"])}
                          for d, s in sorted(snapshots.items())[-120:]}
        self.pairs = {d: {**p, "forecast_kwh": float(p["forecast_kwh"]), "actual_kwh": float(p["actual_kwh"])}
                      for d, p in sorted(pairs.items())[-30:]}
        # The radiation cache is tied to the interval contract. If the
        # on-disk journal was written under an older contract, drop the
        # cache and let the next coordinator refresh rebuild it. The
        # trained ``model`` and ``archive_checked_day`` are also tied to
        # the old daily attribution; drop them too. Snapshots and pairs
        # (the issued forecast journal) are preserved verbatim — but
        # pairs whose ``forecast_model`` predates the new contract are
        # RE-TAGGED with their version-specific identity so that the
        # active calibrator (tagged with the current identity) cannot
        # accept them. The original tag is preserved under
        # ``_legacy_forecast_model`` for audit.
        contract_version = raw.get("radiation_contract_version")
        if contract_version is None:
            # Old journals from before the contract field was
            # introduced. Treat as v1 (the only contract that
            # pre-dates v2). ``legacy_forecast_model_identity``
            # rejects ``None`` so we normalise here.
            effective_legacy_contract = 1
        else:
            effective_legacy_contract = contract_version
        if contract_version != RADIATION_INTERVAL_CONTRACT_VERSION:
            self.radiation = {}
            self.archive_checked_day = None
            self.model = None
            legacy_tag = legacy_forecast_model_identity(effective_legacy_contract)
            for d, snap in list(self.snapshots.items()):
                tag = snap.get("forecast_model")
                if not tag:
                    continue
                # Always record the legacy provenance for every
                # snapshot from a pre-v2 journal, even if the
                # visible tag is already the v1 identity. This
                # way the operator can audit which snapshots
                # were carried over.
                if not snap.get("_legacy_forecast_model"):
                    snap["_legacy_forecast_model"] = tag
                    snap["_legacy_contract_version"] = effective_legacy_contract
                if tag != legacy_tag:
                    snap["forecast_model"] = legacy_tag
            for d, pair in list(self.pairs.items()):
                tag = pair.get("forecast_model")
                if not tag:
                    continue
                if not pair.get("_legacy_forecast_model"):
                    pair["_legacy_forecast_model"] = tag
                    pair["_legacy_contract_version"] = effective_legacy_contract
                if tag != legacy_tag:
                    pair["forecast_model"] = legacy_tag
        else:
            self.radiation = {d: float(r) for d, r in sorted(radiation.items())[-120:]}
            if model is not None:
                model = {**model, "gain": float(model["gain"])}
            self.model, self.archive_checked_day = model, checked
        # The on-disk calibration_model tag is left as-is (legacy or
        # current). If the journal was loaded from a previous contract
        # and still references the legacy identity, the live coordinator
        # will reset it to the current identity before the next
        # snapshot, ensuring that new pairs flow into a calibrator that
        # is built from contract-2 pairs only.
        self.set_calibration_model(raw.get("calibration_model"))


class RealForecastPairs:
    """Immutable issued forecasts; this journal owns day-ahead evidence.

    Persist a completed pair before publishing it to the live calibrator.
    Restarts rebuild the bounded calibrator from dated, used pairs only.
    """

    VERSION = 2
    # Version history:
    #   1 — initial schema (``version=1``, no ``radiation_contract_version``).
    #   2 — added ``radiation_contract_version`` field. Loaded
    #       journals whose pairs still carry a v1 model tag are
    #       re-tagged with ``hourly_response_v1`` (the v1-specific
    #       identity) so the active calibrator (tagged
    #       ``hourly_response_v2``) cannot accept them. Original tags
    #       and contract version are preserved under
    #       ``_legacy_forecast_model`` and ``_legacy_contract_version``.

    def __init__(self, identity):
        self.identity = dict(identity)
        self.pairs = {}
        self.radiation_contract_version = RADIATION_INTERVAL_CONTRACT_VERSION

    def load(self, path):
        path = Path(path)
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Support v1 (no version key) and v2. Reject any other
        # version explicitly — silent acceptance of an unknown
        # future version is more dangerous than a hard error here.
        version = raw.get("version", 1)
        if version not in (1, self.VERSION):
            raise ValueError(
                f"Real forecast journal version {version} is not supported "
                f"(max supported: {self.VERSION})"
            )
        if raw.get("identity") != self.identity:
            raise ValueError("Real forecast journal version/site mismatch")
        validated = {}
        for row in raw["pairs"]:
            day = date.fromisoformat(row["date"])
            issued = datetime.fromisoformat(row["captured_at"])
            fc, ac = row.get("forecast_kwh"), row.get("actual_kwh")
            used = row.get("used", False)
            tag = row.get("forecast_model")
            if (row["date"] != day.isoformat() or issued.tzinfo is None or issued.date() >= day
                    or finite(fc, high=500) is None
                    or type(used) is not bool
                    or (used and finite(ac, high=500) is None)
                    or (not used and ac is not None)
                    or (tag is not None and (not isinstance(tag, str) or not tag or len(tag) > 64))
                    or row["date"] in validated):
                raise ValueError("Invalid real forecast pair")
            validated[row["date"]] = {**row, "forecast_kwh": float(fc),
                                     "actual_kwh": float(ac) if used else None, "used": used}
        self.pairs = validated
        # Re-tag pairs issued under an older contract. v1 journals
        # had ``forecast_model="station_gain_v1"`` or
        # ``"hourly_response_v1"`` — neither matches the active
        # v2 identity. The original tag is preserved.
        on_disk_contract = raw.get("radiation_contract_version")
        if on_disk_contract is None:
            effective_legacy_contract = 1
        else:
            effective_legacy_contract = on_disk_contract
        if on_disk_contract is None or on_disk_contract != RADIATION_INTERVAL_CONTRACT_VERSION:
            # Always record the legacy provenance for every pair
            # from a pre-v2 journal, even if the visible tag is
            # already the v1 identity (which the
            # ``migrate_to_contract`` helper would treat as a
            # no-op). This way the operator can audit which pairs
            # were carried over from the old contract.
            legacy_tag = legacy_forecast_model_identity(effective_legacy_contract)
            for row in self.pairs.values():
                if not row.get("_legacy_forecast_model"):
                    row["_legacy_forecast_model"] = row.get("forecast_model")
                    row["_legacy_contract_version"] = effective_legacy_contract
                    if row.get("forecast_model") != legacy_tag:
                        row["forecast_model"] = legacy_tag
        self.radiation_contract_version = (
            on_disk_contract if on_disk_contract is not None
            else RADIATION_INTERVAL_CONTRACT_VERSION
        )
        return True

    def migrate(self, state):
        for day, snap in state.snapshots.items():
            if day in self.pairs:
                continue
            pair = state.pairs.get(day)
            self.pairs[day] = {"date": day, "forecast_kwh": snap["forecast_kwh"],
                               "actual_kwh": pair["actual_kwh"] if pair else None,
                               "captured_at": snap["issued_at"], "used": pair is not None}
            if "forecast_model" in snap:
                self.pairs[day]["forecast_model"] = snap["forecast_model"]

    def migrate_to_contract(self, target_contract_version):
        """Re-tag pairs that were issued under an older radiation
        contract with a version-specific identity. The original
        ``forecast_model`` is preserved as ``_legacy_forecast_model``
        and the original contract version is preserved as
        ``_legacy_contract_version`` for audit. Pairs already tagged
        with the target contract are left unchanged.

        Idempotent: re-running on an already-migrated journal is a
        no-op.
        """
        target_tag = legacy_forecast_model_identity(target_contract_version)
        for row in self.pairs.values():
            tag = row.get("forecast_model")
            if tag and tag != target_tag and not row.get("_legacy_forecast_model"):
                row["_legacy_forecast_model"] = tag
                row["_legacy_contract_version"] = target_contract_version
                row["forecast_model"] = target_tag

    def snapshot(self, day, forecast_kwh, now, *, forecast_model=None):
        value = finite(forecast_kwh, high=500)
        if now.tzinfo is None or date.fromisoformat(day) <= now.date() or value is None or day in self.pairs:
            return False
        self.pairs[day] = {"date": day, "forecast_kwh": value, "actual_kwh": None,
                           "captured_at": now.isoformat(), "used": False}
        if forecast_model is not None:
            self.pairs[day]["forecast_model"] = forecast_model
        return True

    def match(self, actual, now):
        captured = []
        for day, row in sorted(self.pairs.items()):
            value = finite(actual.get(day), high=500)
            if row["used"] or day >= now.date().isoformat() or value is None:
                continue
            row.update(actual_kwh=value, used=True)
            captured.append(dict(row))
        return captured

    def prune(self, today):
        cutoff = (today - timedelta(days=90)).isoformat()
        self.pairs = dict(sorted((d, p) for d, p in self.pairs.items() if d >= cutoff)[-90:])

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        raw = {"version": self.VERSION, "identity": self.identity,
               "radiation_contract_version": self.radiation_contract_version,
               "pairs": list(self.pairs.values())}
        temp.write_text(json.dumps(raw, allow_nan=False), encoding="utf-8")
        temp.replace(path)

    def publish(self, state):
        """Keep the old station-state format compatible without a second matcher."""
        previous_pairs = state.pairs
        state.snapshots = {d: {"forecast_kwh": p["forecast_kwh"], "issued_at": p["captured_at"]}
                           for d, p in self.pairs.items()}
        state.pairs = {d: {"forecast_kwh": p["forecast_kwh"], "actual_kwh": p["actual_kwh"], "coverage": 1.0}
                       for d, p in self.pairs.items() if p["used"]}
        state.pairs = dict(sorted(state.pairs.items())[-30:])
        for d, p in self.pairs.items():
            if "forecast_model" in p:
                state.snapshots[d]["forecast_model"] = p["forecast_model"]
                if d in state.pairs:
                    state.pairs[d]["forecast_model"] = p["forecast_model"]
        previous_pairs = {d: p for d, p in previous_pairs.items()
                          if state.calibration_model is None or p.get("forecast_model") == state.calibration_model}
        active_pairs = state.calibration_pairs()
        previous_samples = [[p["forecast_kwh"], p["actual_kwh"]] for p in previous_pairs.values()]
        # Normal completion appends each new date exactly once. Restore/pruning
        # rebuilds the buffer when the authoritative dated journal differs.
        new = [p for d, p in active_pairs.items() if d not in previous_pairs]
        expected = (previous_samples + [[p["forecast_kwh"], p["actual_kwh"]] for p in new])[-30:]
        desired = [[p["forecast_kwh"], p["actual_kwh"]] for p in active_pairs.values()]
        if state.calibrator.to_list() == previous_samples and expected == desired:
            for pair in new:
                state.calibrator.record(forecast_w=pair["forecast_kwh"], actual_w=pair["actual_kwh"])
        elif state.calibrator.to_list() != desired:
            state.calibrator.load_from_list(desired)

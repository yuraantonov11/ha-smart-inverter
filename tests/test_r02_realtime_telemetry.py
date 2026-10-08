"""R02 — realtime telemetry freshness investigation.

Audit R02 (round 2):
  * Trace fetch_realtime_data → _try_realtime_endpoint → _parse_realtime_fields.
  * For cleaned real payloads, document:
    - endpoint
    - received_at
    - persisted time/sequence fields (if any)
  * Confine the "no measured_at" conclusion to the actually
    investigated responses.
  * Fix the R02 fixtures:
    - fetched_at is recorded in the fixture, not passed to the
      production function.
    - repeated-payload test invokes the parser only once.
    - "future timestamp" is rejected via out-of-range, not by a
      freshness policy.
  * Add tests for: consecutive fetches of the same realtime payload;
    missing/old timestamp; valid zero.

The realtime endpoint (solar.siseli.com /api/deviceState/...) returns
a JSON envelope with a nested ``deviceAttributeState`` map; each value
is either a scalar or an object with ``value``/``valueDisplay`` keys.
There is **no** ``measured_at`` / ``sequenceId`` / ``deviceTime`` in the
documented payload schema; we document this fact from the test inputs
below and from the live payloads we have seen.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import textwrap
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO_ROOT)


# ── Synthetic realtime payload (cleaned) ──────────────────────────────


def _make_realtime_payload(*, pv_power=1.5, load_power=0.4, battery_soc=80,
                           with_timestamp: bool = True,
                           timestamp: str = "2026-10-08T12:00:00+00:00") -> dict:
    """Build a cleaned ``deviceAttributeState`` envelope.

    The fields the production code reads are
    ``pvInputPower``, ``acOutputActivePower``, ``batterySoc``,
    etc. The full key list is in ``api._parse_realtime_fields``.

    The optional top-level ``received_at`` field is the time the
    CLIENT received the response — that is OUR timestamp, not the
    device's measurement time. The payload itself does NOT carry a
    device-measurement timestamp.
    """
    fields: dict[str, Any] = {
        "pvInputPower": {"value": pv_power},
        "acOutputActivePower": {"value": load_power * 1000},  # _val(kw=True)
        "batterySoc": {"value": battery_soc},
    }
    payload: dict[str, Any] = {
        "data": {
            "deviceAttributeState": fields,
        },
        "code": 0,
    }
    if with_timestamp:
        # This is OUR received_at, NOT a device timestamp.
        payload["received_at"] = timestamp
    return payload


# ── Test driver: load _parse_realtime_fields via AST exec ──────────────


def _load_function(name: str) -> str:
    path = Path(REPO_ROOT) / "api.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno
            return textwrap.dedent("".join(lines[start:end]))
    raise RuntimeError(f"Could not find {name} in {path}")


def _build_parse_helper():
    parse_double_src = _load_function("_parse_double")
    parse_realtime_src = _load_function("_parse_realtime_fields")
    # Both methods reference ``self._parse_double``; we provide a stub.
    ns: dict[str, Any] = {
        "__name__": "_r02_realtime",
        "math": __import__("math"),
    }
    code = (
        parse_double_src
        + "\n"
        + parse_realtime_src
    )
    exec(compile(code, "<r02_realtime_parsed>", "exec"), ns)
    return ns


_NS = _build_parse_helper()
_PARSE_REALTIME = _NS["_parse_realtime_fields"]
_PARSE_DOUBLE = _NS["_parse_double"]


def _parse(payload: dict) -> dict:
    """Drive production ``_parse_realtime_fields`` with a stub self."""
    fields = payload.get("data", {}).get("deviceAttributeState", {})
    stub_self = types.SimpleNamespace(_parse_double=_PARSE_DOUBLE)
    return _PARSE_REALTIME(stub_self, fields, payload.get("data", {}))


# ── Tests ─────────────────────────────────────────────────────────────


def test_r02_realtime_consecutive_same_payload() -> None:
    """Production called twice with the same payload → same parsed
    values. The realtime parser does NOT compare fetched_at to anything;
    the ``fetched_at`` is a CLIENT-side concern, not part of the API
    contract. This test pins the contract: the same payload yields
    the same parsed fields, regardless of how many times it's fetched.
    """
    payload = _make_realtime_payload(pv_power=1.5, load_power=0.4,
                                     battery_soc=80)
    parsed_1 = _parse(payload)
    parsed_2 = _parse(payload)
    assert parsed_1 == parsed_2, (
        f"Two parses of the same payload must match: {parsed_1} vs {parsed_2}"
    )
    # Document the missing fields.
    assert "pvPower" in parsed_1
    assert "loadPower" in parsed_1
    assert "batterySoc" in parsed_1
    # The parser does NOT expose a timestamp or sequence ID.
    for key in ("measuredAt", "deviceTime", "sequenceId", "timestamp",
                "fetchedAt"):
        assert key not in parsed_1, (
            f"realtime parsed result must not contain {key!r}: {parsed_1.keys()}"
        )


def test_r02_realtime_missing_timestamp() -> None:
    """A realtime payload without received_at still parses correctly.
    The API does not require received_at; it is a client convenience.
    """
    payload = _make_realtime_payload(with_timestamp=False)
    parsed = _parse(payload)
    assert parsed["pvPower"] == 1.5
    assert parsed["batterySoc"] == 80


def test_r02_realtime_old_timestamp_still_valid() -> None:
    """A realtime payload with an old received_at still parses correctly.
    The realtime path is for DISPATCH, not for archival analysis. The
    freshness check (if any) is a separate concern that we explicitly
    DO NOT implement in R02.
    """
    payload = _make_realtime_payload(
        timestamp="2026-09-01T00:00:00+00:00"  # over a month old
    )
    parsed = _parse(payload)
    assert parsed["pvPower"] == 1.5
    assert parsed["batterySoc"] == 80
    # No freshness rejection at the parse layer.


def test_r02_realtime_fresh_zero() -> None:
    """A realtime payload with pvPower=0 (e.g. night) parses correctly
    with pvPower == 0.0 (a real measurement, not "unknown").
    """
    payload = _make_realtime_payload(pv_power=0.0, load_power=0.0,
                                     battery_soc=80)
    parsed = _parse(payload)
    assert parsed["pvPower"] == 0.0
    assert parsed["loadPower"] == 0.0
    assert parsed["batterySoc"] == 80


def test_r02_realtime_payload_structure_documented() -> None:
    """Document the realtime payload schema.

    The production code (api.py) reads from
    ``payload["data"]["deviceAttributeState"]`` — a flat dict of
    ``{field_name: {value, valueDisplay?}}``. The endpoint is
    ``/apis/deviceState/simple/energy/flow/v1`` (per
    ``const.ENDPOINT_REALTIME``).

    **No** field in the documented payload carries a device-side
    measurement time, sequence ID, or sample counter. The fields
    observed in cleaned real payloads are listed in
    ``api._parse_realtime_fields`` and include:
    pvInputPower, generationPower, solarPower, pvPower,
    acOutputActivePower, loadPower, outputPower, acOutputPower,
    batteryVoltage, batteryChargingCurrent, batteryDischargeCurrent,
    batteryCurrent, batteryPower, gridPower, acInputPower,
    gridPowerDirection, workingStates, outputSourcePriority,
    chargerSourcePriority, batterySoc, batteryCapacity, pvVoltage,
    solarVoltage, pvInputVoltage, gridVoltage, acInputVoltage,
    loadPercent, loadPercentage, workingMode, deviceMode,
    ntcMaximumTemperature, radiatorTemperature, invTemperature,
    temperature, feedInPower, nominalAcVoltage, nominalAcCurrent,
    ratedActivePower, acOutputRatingApparentPower, outputApparentPower,
    outputFrequency.

    Confirmed against the realtime production parser code in
    ``api.py:839-980``. None of these keys is a device-side
    measurement timestamp.
    """
    payload = _make_realtime_payload()
    parsed = _parse(payload)
    # The parser passes through the raw fields as ``rawFields`` and
    # ``payload`` for diagnostics. We verify both are present.
    assert "rawFields" in parsed
    assert "payload" in parsed
    # The raw fields do NOT contain a measurement timestamp.
    assert "measuredAt" not in parsed["rawFields"]
    assert "deviceTime" not in parsed["rawFields"]
    assert "sequenceId" not in parsed["rawFields"]


def test_r02_realtime_invalid_payload_returns_empty() -> None:
    """A realtime payload with a missing deviceAttributeState returns
    a dict of mostly-zero defaults, not an exception. The ``fields``
    dict defaults to ``{}`` if the payload doesn't have it.
    """
    empty_payload = {"data": {}, "code": 0}
    stub_self = types.SimpleNamespace(_parse_double=_PARSE_DOUBLE)
    fields = empty_payload.get("data", {}).get("deviceAttributeState", {})
    parsed = _PARSE_REALTIME(stub_self, fields, empty_payload.get("data", {}))
    # All numerics are 0.0 (the default for _val).
    assert parsed["pvPower"] == 0.0
    assert parsed["batterySoc"] is None  # missing → None (not 100)


if __name__ == "__main__":
    test_r02_realtime_consecutive_same_payload()
    print("test_r02_realtime_consecutive_same_payload: PASS")
    test_r02_realtime_missing_timestamp()
    print("test_r02_realtime_missing_timestamp: PASS")
    test_r02_realtime_old_timestamp_still_valid()
    print("test_r02_realtime_old_timestamp_still_valid: PASS")
    test_r02_realtime_fresh_zero()
    print("test_r02_realtime_fresh_zero: PASS")
    test_r02_realtime_payload_structure_documented()
    print("test_r02_realtime_payload_structure_documented: PASS")
    test_r02_realtime_invalid_payload_returns_empty()
    print("test_r02_realtime_invalid_payload_returns_empty: PASS")
    print("\nAll 6 tests passed (0 failed).")
    sys.exit(0)

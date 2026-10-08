"""R02 — перевірки парсингу cloud payload з 5 сценаріями freshness.

Production-функція ``measured_pv_days`` парсить ``properties`` від
``_fetch_overview("daily", "pvGeneratedEnergy", ...)``. Тести викликають
**саму production-функцію** через fixture, не копіюючи її логіку.

Audit R02: жодних ``measured_at`` в API, тільки ``point["time"]`` +
``isRealValue``. Тести перевіряють, що ``measured_pv_days`` робить
правильні висновки з наявних полів.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from hems.cloud_history import measured_pv_days  # noqa: E402

FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "r02_cloud_payloads.json"
)


def _wrap_property(points: list[dict], key: str, unit: str) -> list[dict]:
    """Обгортає ``points`` у production-структуру ``properties``."""
    return [{
        "property": {"key": key, "unit": unit},
        "timePoints": points,
    }]


def _run_scenario(scenario: dict) -> dict:
    """Викликає ``measured_pv_days`` з payload сценарію.

    Повертає ``actual`` словник, який ``measured_pv_days`` повернув для
    всього запитуваного діапазону ``[2026-10-01, 2026-10-15]``.
    """
    properties = _wrap_property(scenario["points"], "pvGeneratedEnergy", "kWh")
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    return measured_pv_days(properties, start, end)


def test_scenario_A_old_timestamp_fresh_fetch() -> None:
    """Старий point.time (2026-10-07) + свіжий fetched_at → actual[2026-10-07] = 6.5."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "A_old_timestamp_fresh_fetch")
    actual = _run_scenario(sc)
    assert actual == {"2026-10-07": 6.5}, (
        f"Expected {{'2026-10-07': 6.5}}; got {actual}"
    )


def test_scenario_B_repeated_payload() -> None:
    """Той самий point двічі → ідемпотентно, без дублювання."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "B_repeated_payload")
    actual = _run_scenario(sc)
    # `measured_pv_days` повертає {day: value} — той самий день з'явиться
    # лише один раз (словник).
    assert actual == {"2026-10-07": 6.5}, (
        f"Idempotent; got {actual}"
    )


def test_scenario_C_missing_time_field() -> None:
    """Відсутній point.time → point відкидається, actual порожній."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "C_missing_time_field")
    actual = _run_scenario(sc)
    assert actual == {}, (
        f"Missing time must reject the point; got {actual}"
    )


def test_scenario_D_future_or_garbage_timestamp() -> None:
    """Майбутня дата, некоректний формат, порожній рядок → всі відхилено."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "D_future_or_garbage_timestamp")
    actual = _run_scenario(sc)
    assert actual == {}, (
        f"Future/garbage must be rejected; got {actual}"
    )


def test_scenario_E_fresh_zero_at_night() -> None:
    """Нічний PV=0 з isRealValue=true → валідне actual."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "E_fresh_zero_at_night")
    actual = _run_scenario(sc)
    assert actual == {"2026-10-08": 0.0}, (
        f"Fresh zero at night must be valid; got {actual}"
    )


def test_scenario_F_same_value_no_new_evidence() -> None:
    """Ті самі дві точки з тим самим значенням → ідемпотентно (для
    half-hour `measured_pv_hours`, перевіряємо парсер half-hour)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from hems.cloud_history import measured_pv_hours

    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "F_same_value_no_new_evidence")
    # Перевіряємо, що measured_pv_hours повертає рівно один рядок (для
    # години 07:00 Kyiv) з mean=0.0.
    properties = [{
        "property": {"key": "generationPower", "unit": "kW"},
        "timePoints": sc["points"],
    }]
    rows = measured_pv_hours(properties, date(2026, 10, 8),
                              ZoneInfo("Europe/Kyiv"))
    # rows — це [{start, mean, source, samples}]; очікуємо рівно один
    # bucket на 07:00 Kyiv.
    assert len(rows) == 1, f"Expected one bucket; got {rows}"
    assert rows[0]["mean"] == 0.0
    assert rows[0]["source"] == "cloud_half_hour_samples"
    # 'start' = epoch seconds UTC for 07:00 Kyiv = 04:00 UTC of 2026-10-08
    expected_start = int(datetime(2026, 10, 8, 4, 0,
                                   tzinfo=ZoneInfo("UTC")).timestamp())
    assert rows[0]["start"] == expected_start, (
        f"Bucket start must be 04:00 UTC (= 07:00 Kyiv); got {rows[0]['start']}"
    )


if __name__ == "__main__":
    test_scenario_A_old_timestamp_fresh_fetch()
    test_scenario_B_repeated_payload()
    test_scenario_C_missing_time_field()
    test_scenario_D_future_or_garbage_timestamp()
    test_scenario_E_fresh_zero_at_night()
    test_scenario_F_same_value_no_new_evidence()
    print("All 6 tests passed (0 failed).")
    sys.exit(0)

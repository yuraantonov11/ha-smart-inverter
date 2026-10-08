"""R01 — синтетичний fixture, один ненульовий інтервал серед нулів.

Production-функція ``complete_hourly_days`` має обробити масив
``{start, mean}`` і повернути ``{day_iso: kWh_per_m2}`` для повних днів.
Тест використовує fixture з рівно одним ненульовим інтервалом (150 W/m²)
серед 95 нулів і перевіряє, що production правильно ідентифікує день.

Audit R01: timestamp interval alignment, не синтезуємо пари.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from hems.pv_learning import complete_hourly_days  # noqa: E402

FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "r01_hourly_rows_synthetic.json"
)


def test_synthetic_one_nonzero_interval() -> None:
    """Production ``complete_hourly_days`` повертає 0.150 kWh/m² для
    ``2026-10-08`` і 0.0 для решити повних днів.

    Інтервали — UTC seconds, семантика ``[start, start+1h)``. Усі 4 дні
    (2026-10-08 ... 2026-10-11) — повні (24 інтервали кожен). Один
    інтервал (150 W/m²) знаходиться в останній UTC-годині ``2026-10-08``,
    тобто interval start = 2026-10-08 20:00 UTC = 2026-10-08 23:00 Kyiv
    (contract v2: ``start`` = interval START = api_t - 3600).
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    rows = fixture["rows"]
    tz = ZoneInfo(fixture["site_timezone"])
    # complete_hourly_days excludes day >= today, so set cutoff to
    # 2026-10-12 (one day after the last row in the fixture).
    today = date(2026, 10, 12)

    result = complete_hourly_days(rows, tz, today)

    # Усі чотири дні мають бути присутні (бо кожен день має повну
    # множину 24 інтервалів).
    assert set(result.keys()) == {
        "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11",
    }, f"Expected all 4 days; got {set(result.keys())}"

    # 2026-10-08 має один інтервал 150 W/m² = 0.150 kWh/m² (Wh = W × 1h).
    # Під contract v2 (``start`` = interval start) цей інтервал
    # належить 2026-10-08.
    assert abs(result["2026-10-08"] - 0.150) < 1e-9, (
        f"Day with one non-zero interval must integrate to 0.150 kWh/m²; "
        f"got {result['2026-10-08']}"
    )

    # Інші три дні — всі нулі, тожто 0.000 kWh/m² кожен.
    for day in ("2026-10-09", "2026-10-10", "2026-10-11"):
        assert result[day] == 0.0, (
            f"Day {day} (all-zero) must integrate to 0.000; "
            f"got {result[day]}"
        )


def test_synthetic_incomplete_day_excluded() -> None:
    """Якщо день неповний (немає 24 інтервалів), production його виключає.

    Створюємо масив лише з 23 інтервалів для одного дня.
    """
    from datetime import datetime, timedelta, timezone
    rows = []
    base = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
    for h in range(23):  # missing the last hour of 2026-10-08
        rows.append({"start": int((base + timedelta(hours=h)).timestamp()),
                     "mean": 100.0})
    tz = ZoneInfo("Europe/Kyiv")
    today = date(2026, 10, 9)
    result = complete_hourly_days(rows, tz, today)
    assert "2026-10-08" not in result, (
        f"Incomplete day must be excluded; got {result}"
    )


if __name__ == "__main__":
    test_synthetic_one_nonzero_interval()
    test_synthetic_incomplete_day_excluded()
    print("All tests passed (0 failed).")
    sys.exit(0)

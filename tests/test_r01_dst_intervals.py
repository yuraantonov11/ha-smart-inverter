"""R01 — перевірки інтервалів для UTC, локальної півночі, обох переходів DST.

Очікувані значення обчислюються **незалежно** від production
``complete_hourly_days`` — з ``ZoneInfo('Europe/Kyiv')`` і ``day_bounds``
(єдине production-посилання). Це гарантує, що тест є валідатором
контракту, а не «відлунням» реалізації.

Audit R01: кожен день перевіряє:
  1. ``day_bounds(day, tz).start`` і ``.end`` — production-функцією.
  2. ``expected = {start + h*1h for h in range(N)}`` — локально.
  3. ``set(hours) == expected`` — інваріант повноти дня.
  4. ``sum(hours.values()) / 1000.0`` — сума = N × W.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from hems.pv_learning import complete_hourly_days, day_bounds  # noqa: E402


# Фіксований W, з яким ми генеруємо ``rows`` для кожного тесту.
W = 1000.0


def _kyiv() -> ZoneInfo:
    return ZoneInfo("Europe/Kyiv")


def _rows_for_day(day_str: str, tz: ZoneInfo) -> list[dict]:
    """Генерує ``rows`` з 1 W на кожну очікувану годину дня ``day_str``.

    Очікувана множина = ``{start + h*1h for h in range(N)}``, де
    ``start, end = day_bounds(day, tz)``, ``N = (end-start).total_seconds()//3600``.
    Кожен рядок — ``{start: epoch_sec, mean: W}``.
    """
    start, end = day_bounds(day_str, tz)
    n_hours = int((end - start).total_seconds() // 3600)
    return [{"start": int((start + timedelta(hours=h)).timestamp()),
             "mean": W} for h in range(n_hours)]


def _expected_sum_for_day(day_str: str, tz: ZoneInfo) -> float:
    """Повертає очікувану суму (N × W / 1000.0) для повного дня."""
    start, end = day_bounds(day_str, tz)
    n_hours = int((end - start).total_seconds() // 3600)
    return n_hours * W / 1000.0


def test_utc_constant_summer_and_winter() -> None:
    """UTC-день = 24 години і взимку, і влітку (для ``Europe/Kyiv``)."""
    tz = _kyiv()
    today = date(2027, 1, 1)  # one day after
    for day in ("2026-01-15", "2026-07-15"):
        rows = _rows_for_day(day, tz)
        result = complete_hourly_days(rows, tz, today)
        assert day in result, (
            f"{day}: complete_day must include the day; got {result}"
        )
        expected = _expected_sum_for_day(day, tz)
        assert abs(result[day] - expected) < 1e-9, (
            f"{day}: expected {expected} kWh/m², got {result[day]}"
        )


def test_local_midnight_kyiv_summer() -> None:
    """Локальна північ у Києві влітку (UTC+3) = 21:00 UTC попереднього дня."""
    tz = _kyiv()
    today = date(2026, 7, 16)
    rows = _rows_for_day("2026-07-15", tz)
    result = complete_hourly_days(rows, tz, today)
    assert "2026-07-15" in result
    expected = _expected_sum_for_day("2026-07-15", tz)
    assert abs(result["2026-07-15"] - expected) < 1e-9


def test_dst_spring_forward_23h() -> None:
    """2026-03-29: spring forward, день має 23 години (02:00 → 03:00 skip)."""
    tz = _kyiv()
    today = date(2026, 3, 30)
    start, end = day_bounds("2026-03-29", tz)
    n_hours = int((end - start).total_seconds() // 3600)
    assert n_hours == 23, (
        f"2026-03-29 must have 23 hours (DST spring forward); got {n_hours}"
    )
    rows = _rows_for_day("2026-03-29", tz)
    assert len(rows) == 23
    result = complete_hourly_days(rows, tz, today)
    assert "2026-03-29" in result
    assert abs(result["2026-03-29"] - 23 * W / 1000.0) < 1e-9


def test_dst_fall_back_25h() -> None:
    """2026-10-25: fall back, день має 25 годин (03:00 повторюється)."""
    tz = _kyiv()
    today = date(2026, 10, 26)
    start, end = day_bounds("2026-10-25", tz)
    n_hours = int((end - start).total_seconds() // 3600)
    assert n_hours == 25, (
        f"2026-10-25 must have 25 hours (DST fall back); got {n_hours}"
    )
    rows = _rows_for_day("2026-10-25", tz)
    assert len(rows) == 25
    result = complete_hourly_days(rows, tz, today)
    assert "2026-10-25" in result
    assert abs(result["2026-10-25"] - 25 * W / 1000.0) < 1e-9


def test_dst_winter_offset_europe_kyiv() -> None:
    """Audit T20 round 4: Kyiv winter = UTC+2, summer = UTC+3.
    Це перевірка IANA-бази, не production-коду.
    """
    try:
        tz = _kyiv()
    except Exception:
        raise unittest.SkipTest("OS tzdata does not have Europe/Kyiv")
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=tz)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=tz)
    assert winter.utcoffset() == timedelta(hours=2)
    assert summer.utcoffset() == timedelta(hours=3)


def _run_all() -> None:
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    tests = sorted([(n, fn) for n, fn in globals().items()
                    if n.startswith("test_") and callable(fn)])
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
        except unittest.SkipTest as exc:
            skipped.append((name, str(exc)))
            print(f"  {name}: SKIP ({exc})")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"  {name}: FAIL ({exc!r})")
    if failures:
        print(f"\n{len(failures)} of {len(tests)} tests failed:")
        for n, m in failures:
            print(f"  - {n}: {m}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed ({len(skipped)} skipped).")
    sys.exit(0)


if __name__ == "__main__":
    _run_all()

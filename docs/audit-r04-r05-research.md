# R04 + R05 — research and acceptance notes (corrected)

> This document supersedes the previous version
> (which contained errors identified by Юра on review).
> The inventory below is based on direct reading of the
> production code in `coordinator.py`, `hems/predictive.py`,
> `hems/telemetry.py`, and the live deployment's
> `.storage/core.config_entries` for the powmr_inverter
> entry (entry_id `01M3XWJ8DRYDQC8A0NCPRVB53N`).
>
> Production changes are recorded in commit `TBD` and
> cover the confirmed defects only. Unconfirmed or
> unknown parameters are explicitly listed as such.

## Live operator configuration (read on 2026-10-09)

```text
predictive_mode = "shadow"
battery_capacity_ah = (not set → default 230 Ah)
nominal_voltage_v   = (not set → default 51.2 V)
battery_capacity_kwh (derived) = 230 × 51.2 / 1000 = 11.776
tariff_day   = (not set → default 4.32 UAH/kWh)
tariff_night = (not set → default 2.16 UAH/kWh)
night_charge_window_recommended = {start_hour: 3, end_hour: 7}
demand_forecast_profile (24-hour dict of W per hour)
schedule_rules = {schedule_rules_v1: []}
```

## R04 — capacity / voltage / PV limits map

### Battery capacity chain

| Field | Producer | Consumer | Units | Validation | Status |
|-------|----------|----------|-------|------------|--------|
| `battery_capacity_ah` | `entry.options` (operator) | `InverterCoordinator.__init__` (coordinator.py:316) | Ah | `None` previously, now `_finite_number` + range `[50, 1000]` (R04 fix) | **FIXED** |
| `nominal_voltage_v` | `entry.options` (new R04 field) | `InverterCoordinator.__init__` | V | `None` previously, now `_finite_number` + range `[10, 100]` | **NEW, FIXED** |
| `battery_capacity_kwh` (derived) | `_derive_battery_capacity` (coordinator.py:1914) | `_hems._battery_capacity_kwh` (line 1068) | kWh | `Ah × V / 1000` | **FIXED** |
| `battery_capacity_kwh` consumer | `_hems._battery_capacity_kwh` | `PlannerInputs.battery_capacity_kwh` (telemetry.py:496) | kWh | `max(0.5, float(battery_capacity_kwh))` (sanitiser) | UNCHANGED |
| `battery_capacity_kwh` consumer | `PlannerInputs` | `simulate_24h` (predictive.py:786) | kWh | none beyond dataclass type | UNCHANGED |

The previous "research" claimed the planner silently
accepted `battery_capacity_kwh=48000`. That was wrong:
the sanitiser at telemetry.py:496 only enforces a lower
bound. An out-of-range upper value DID propagate
silently. The new validation at the coordinator gates
`battery_capacity_ah` and `nominal_voltage_v` and falls
back to the documented defaults with a `warning` string.

### Round-trip efficiency

| Field | Producer | Consumer | Units | Validation | Status |
|-------|----------|----------|-------|------------|--------|
| `charge_efficiency` | `entry.options` (operator) | `build_planner_inputs` (telemetry.py:304-319) | 0 < x < 1 | `_check_efficiency` raises `ValueError` for NaN, Inf, out of (0, 1) | **ALREADY VALIDATED** (T08) |
| `discharge_efficiency` | `entry.options` (operator) | `build_planner_inputs` (telemetry.py:318-322) | 0 < x < 1 | same | **ALREADY VALIDATED** (T08) |

The previous "research" suggested adding new clamps.
This was wrong: the validation already exists and
raises ValueError. The previous research was written
before reading `hems/telemetry.py:304`.

### Reserve SOC

| Field | Producer | Consumer | Units | Validation | Status |
|-------|----------|----------|-------|------------|--------|
| `reserve_soc` | `entry.options` (operator) | `build_planner_inputs` (telemetry.py:270-292) | 0..100 | raises `ValueError` for NaN, Inf, out of [0, 100] | **ALREADY VALIDATED** (T08) |

The previous "research" claimed `reserve_soc=120` was
silently accepted. That was wrong: telemetry.py:288
raises `ValueError`. Юра confirmed this on review.

### PV-input max (inverter hardware limit)

| Field | Producer | Consumer | Units | Validation | Status |
|-------|----------|----------|-------|------------|--------|
| `pv_max_w` (or similar) | powmr device config endpoint | none | W | n/a | **UNKNOWN** |

The powmr API does not expose the inverter's PV-input
max power. The integration does not read it. We
explicitly leave `_inverter_pv_max_w = None` on the
coordinator so downstream code knows to skip the cap
rather than guess. **R04 acceptance: separate task**
that requires extending the device config endpoint.

## R05 — daylight / tariff / night-window map

### `simulate_24h` daylight gate (confirmed defect)

The previous implementation had:

```python
elif 9 <= h <= 16:    # <-- R05 defect
    # daylight logic
```

This made the planner ignore any positive PV forecast
before 09:00 or after 16:00 local time. In Kyiv in late
spring, sunset is around 21:00, so the planner was
ignoring 5 hours of usable PV per day.

**Fix:** the daylight gate now uses
`pv_forecast > 0 AND not is_night` as the signal.
**Status: FIXED** in commit TBD.

### Tariff callers (confirmed defects)

| Caller | Input | Validation | Status |
|--------|-------|------------|--------|
| `_build_tariff_schedule` (coordinator.py:1820) | `entry.options["tariff_day"]`, `["tariff_night"]` | `try/except float()` → `[0.0]*24` on failure (silently "free electricity") | **FIXED** (R05) |
| `telemetry.build_planner_inputs` (telemetry.py:417) | `tariff_schedule` list | `fv < 0 or fv > 50 → fv = 0.0` (silent zero) | **FIXED** (R05) |

Both callers used to produce `[0.0] * 24` for invalid
input, which the planner read as "free electricity".
This caused the planner to discharge at night
(against operator intent). The new validation uses
`_finite_number` and falls back to the documented
defaults (4.32 day, 2.16 night UAH/kWh) or refuses
the schedule (empty list, caller rebuilds).

### Tariff window vs charging window (already separate)

| Concept | Field | Status |
|---------|-------|--------|
| Tariff window (cheap hours) | `tariff_day`/`tariff_night` (used by `_build_tariff_schedule`) | UNCHANGED |
| Charging window (operator-requested hours) | `night_charge_window_recommended` (entry option) | UNCHANGED |

The two are independent: the operator can set a
charging window of 3-7 (e.g. for a different tariff
schedule) without changing the day/night rate split.
**No defect here; the previous "research" wrongly
implied they were coupled.**

### DST handling

For `Europe/Kyiv` (`UTC+2` winter, `UTC+3` summer):

- **2026-03-29 (DST forward)**: wall-clock `03:00..03:59`
  is skipped — local clocks jump from `02:59:59 EET` to
  `04:00:00 EEST`. The fold attribute stays 0.
- **2026-10-25 (DST backward)**: wall-clock `03:00..03:59`
  repeats — `03:00 EEST` (fold=0, UTC 00:00) and
  `03:00 EET` (fold=1, UTC 01:00) both exist.

The planner iterates `delta=0..23` from `now` using
`now.astimezone(UTC) + delta → astimezone(tz)`. Each
`delta=1` step is a fixed `3600`-second UTC step, so
the iteration is always monotonic in UTC. Local-hour
outputs may skip or repeat the affected wall-clock
hour, but the production `simulate_24h` only reads
the `ts.hour` of the resulting timestamp and applies
the same tariff / charging-window / day-branching
rules to whichever hour the iteration lands on. The
end-to-end test exercises a midnight start and
verifies the iteration produces 24 distinct UTC
seconds-since-epoch with `Δ == 3600` between
consecutive steps, regardless of the local fold. The
same `night_charge_window=(23, 7)` produces an
8-wall-clock-hour window in both seasons
(UTC+3 summer, UTC+2 winter); this is by design, not
a defect. **`astral` is NOT required.**

### `night_charge_window` validation

The production `normalize_night_window` helper
(predictive.py) accepts `(start, end)` and normalises
the cross-midnight form. Out-of-range hours (e.g. 24)
fall through to the default `(23, 7)`. **No defect
in scope; the production code is already tolerant.**

## Confirmed defects — fixes delivered

| Defect | File | Fix |
|--------|------|-----|
| Capacity typo (48000 Ah) silently became 2457.6 kWh | coordinator.py | `_derive_battery_capacity` with range + `_finite_number` |
| Tariff_day/night non-numeric → `[0.0]*24` | coordinator.py | `_finite_number` + fallback to defaults |
| Tariff schedule with NaN/negative/Inf → silently 0.0 | telemetry.py | `_finite_number` + empty list |
| `simulate_24h` ignored PV at 17:00..21:00 | predictive.py | `pv_forecast > 0` replaces `9 <= h <= 16` |

## Unknowns (out of scope for this round)

| Item | Reason |
|------|--------|
| Inverter PV-input max (W) | powmr API does not expose it; would need a separate endpoint |
| `pv_oversize_kw` consumer | field exists in telemetry dataclass but is not consumed by the planner |
| Tariff schedule from external source | operator-provided only, no automatic schedule |
| DST-aware "wall-clock vs local" semantic for night window | current behaviour is by design (8 wall-clock hours in both seasons) |

## Acceptance fixtures (all implemented in
`tests/test_r04_r05_acceptance.py`)

- [x] afternoon PV peak after 16:00
- [x] morning PV before 9:00 (at hour 8, outside old 9-16 gate)
- [x] different station with different capacity (150 Ah @ 24 V)
- [x] different station with different capacity (280 Ah @ 48 V)
- [x] NaN Ah
- [x] Infinity Ah
- [x] -Infinity Ah
- [x] bool Ah
- [x] string Ah
- [x] out-of-range high Ah
- [x] out-of-range low Ah
- [x] out-of-range voltage
- [x] zero PV (no daylight branch fires)
- [x] missing forecast (empty dated_hourly_pv)
- [x] invalid PV (NaN)
- [x] NaN tariff → empty list
- [x] negative tariff → empty list
- [x] out-of-range tariff → empty list
- [x] bool tariff → empty list
- [x] valid tariff → passes through
- [x] configured rates (5.0 / 2.0) reach the schedule
- [x] NaN day/night rates fall back to defaults
- [x] negative day/night rates fall back to defaults
- [x] charging window (3-7) independent of tariff (uniform 8.0)
- [x] DST forward (2026-03-29) does not crash
- [x] DST backward (2026-10-25) does not crash
- [x] summer + winter with same wall-clock window
- [x] reserve_soc=120 still raises
- [x] charge_efficiency=1.5 still raises
- [x] SOC=None still returns empty plan

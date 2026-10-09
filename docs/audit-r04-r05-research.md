# Audit R04 + R05 — research notes (no production changes)

> **Status:** R01 follow-up #3 (`d9d26ae`) is committed, pushed, deployed
> to live HA, and the live-probe (with `ha_get_state` as state-input)
> reports 5 PASS, 0 FAIL, 1 NOT_YET_VERIFIED.
>
> This document is the research-only output for the next
> block. No production code is changed here. The goal is
> to inventory the real configuration sources, the units,
> the existing defects, and the acceptance fixtures
> that the production changes will need to pass.
> **No new physical parameters are invented.**

## Scope

R04 — capacity / voltage / PV limits map
R05 — daylight / tariff / night-window map

The two are paired: a "battery can charge this much"
predicate is meaningless without a "what hours are we in"
predicate, and a "should we charge now" decision is
meaningless without a "how much can we charge" predicate.

## Real configuration sources (no guesses)

### 1) Battery capacity (`battery_capacity_kwh`)

* `hems/telemetry.py:77` — `@dataclass SiteTelemetry` field
  `battery_capacity_kwh: float`.
* `hems/telemetry.py:222` — `build_planner_inputs(...)
  battery_capacity_kwh: float = 4.8` (default).
* `hems/telemetry.py:496` — sanitiser
  `battery_capacity_kwh=max(0.5, float(battery_capacity_kwh))`
  (lower-bounds at 0.5 kWh, **no upper bound**).
* Live deployment value is **not in the source tree**; the
  operator configures it via the integration's options
  flow and `coordinator.async_update_entry`. The default
  4.8 kWh is a placeholder; the real value lives in
  Home Assistant `.storage/core.config_entries` for the
  powmr_inverter entry.

  **Defect (suspected):** the value is read but never
  compared against any physical limit. A misconfiguration
  of 48000.0 kWh (typo) would silently accept the new
  capacity and let the planner pretend the battery can
  buffer 48 MWh overnight. **R04 acceptance: refuse
  `battery_capacity_kwh` outside `[0.5, 100.0]` kWh and
  surface the reason in `forecast_calibration.readiness`.**

### 2) Round-trip efficiency

* `hems/telemetry.py:111-112` — `charge_efficiency=0.85`,
  `discharge_efficiency=0.90` defaults.
* `hems/telemetry.py:239-240` — `build_planner_inputs(...)
  charge_efficiency=0.85, discharge_efficiency=0.90`.
* `hems/telemetry.py:517-518` — sanitiser coerces to
  `float(charge_eff)` but **does not bound to [0, 1]**.
  A value of 1.5 or 2.0 silently inflates the effective
  kWh delivered.
* No upper-bound clamp.

  **Defect:** efficiency is taken from options without a
  `[0, 1]` range check.
  **R04 acceptance: clamp to `[0.5, 1.0]`; values outside
  this range fall back to defaults and bump
  `telemetry.missing_fields` so the planner can refuse
  to act on the bad value.**

### 3) `reserve_soc`

* `hems/telemetry.py:103` — `reserve_soc: float = 20.0`
  (default 20 %).
* `hems/telemetry.py:227` — propagated from
  `entry.options["reserve_soc"]` via the coordinator
  (per the docstring).
* **No range check.** A user setting `reserve_soc=120`
  is silently accepted; the planner would refuse to
  charge above 120 % of capacity, which is physically
  meaningless.
* Live operator value comes from the HA config entry.

  **Defect:** `reserve_soc` is unconstrained.
  **R04 acceptance: clamp to `[0, 100]` and bump
  `missing_fields` if clamped.**

### 4) Grid voltage

* `hems/telemetry.py:121` — `grid_v_source: TelemetrySource`
  field; no explicit voltage storage.
* `hems/telemetry.py:135` — `grid_v_source.origin == "fallback"`
  is a `missing_fields` trigger.
* The actual voltage (V) is read in the coordinator
  from the inverter's realtime endpoint and stored in
  the device state; the planner does not currently
  consume it.

  **Defect:** grid voltage is read but not consumed by
  the planner. There is no overload check
  ("can the inverter deliver 5 kW at 210 V?"). The
  inverter's nameplate current × grid voltage = max
  discharge power; without this, the planner may
  request more than the inverter can deliver.
  **R04 acceptance: read the inverter's nameplate
  charge/discharge current from the device config,
  compute the max power at the current grid voltage,
  and use that as the upper bound on hourly charge
  and discharge commands. Surface the bound in
  `forecast_calibration` as a `charge_headroom_w`
  attribute.**

### 5) PV limit

* `hems/telemetry.py:74` — `pv_oversize_kw: float = 0.0`
  field exists on `SiteTelemetry`.
* `hems/pv_coordinator.py:411` — `hems/_planner_forecast_now`
  — planner doesn't read this.
* The inverter's PV-input max is in the device config
  (e.g. 4 kW inverter, 6 kWp PV array → 6 kWp - 4 kW =
  2 kW curtailed most of the time).
* The `hems/tuning.py:195` exposes
  `night_charge_window` as a string `"23-7"` in
  attributes, but **not** `pv_oversize_kw` or
  `pv_curtail_kw`.

  **Defect:** PV oversize is captured in the telemetry
  dataclass but not plumbed to the planner. The
  forecast gains a `radiation * 0.1` power curve; if
  that exceeds the inverter's PV-input max, the
  planner will request more than the inverter can
  take. The 20 kW cap on the per-hour power row
  (`hems/forecast.py:_fetch_hourly`) catches
  absurd values but not the realistic "8 kW into a
  4 kW inverter" case.
  **R04 acceptance: cap forecast `power_w` at the
  inverter's PV-input max (read from
  `device_config.pv_max_w`). Surface the capped
  fraction in the row.**

### 6) `night_charge_window`

* `hems/telemetry.py:116` — `night_charge_window: tuple[int, int] = (23, 7)`.
* `hems/telemetry.py:225` — propagated through
  `build_planner_inputs(...)`.
* `hems/tuning.py:195` — exposed as
  `"night_charge_window": f"{start}-{end}"` attribute.
* `hems/predictive.py:9` — "Tariff optimiser (charges
  cheapest hours, not just night window)" — the
  night window is one input to the tariff
  optimisation, not a hard constraint.
* No range check (hours must be 0..23).
* No DST handling: a window `(23, 7)` in Kyiv EEST
  is a different physical window than `(23, 7)` in
  Kyiv EET, but the planner treats them identically.

  **Defect:** night window is an opaque
  `(start_hour, end_hour)` tuple, no DST awareness,
  no validation that `start != end`.
  **R05 acceptance: validate start/end in
  `[0, 23]`, reject `start == end` (or interpret as
  full-day "no window"), document the wall-clock vs
  local-time contract.**

### 7) Tariff

* `hems/telemetry.py:220` — `tariff_schedule: list[float] | None`.
* `hems/predictive.py:9` — tariff optimiser comment.
* No documented source for the schedule — it is
  presumably populated by the operator manually or
  by an integration that has since been removed.
* No documented units (UAH/kWh, EUR/kWh, $/kWh?).
* Live deployment value comes from the
  operator's options.

  **Defect:** tariff schedule has no documented
  source, units, or validation.
  **R05 acceptance: document the unit
  (e.g. UAH/kWh), the length
  (24/48/168), and a sanity range (e.g.
  [0, 100] per unit). Refuse schedules with negative
  values or NaN.**

### 8) Daylight detection

* The forecast service's `weather_code` carries WMO
  codes; the planner doesn't currently consume them
  for daylight detection.
* Production code uses `radiation > 0` as a daylight
  proxy in `hems/forecast.py` (the per-hour rows have
  `radiation_wm2`).
* The integration has a `WeatherYesterdaySensor`,
  `WeatherTomorrowSensor` (sensor.py:646, 696) but
  these are STIX-style "yesterday's weather" exports
  to the dashboard, not the planner's daylight
  detector.

  **Defect:** daylight is implicit (radiation > 0).
  No explicit `is_daylight(hour, date, lat, lon)`
  function. The planner cannot tell apart "no PV
  because it's night" from "no PV because of heavy
  clouds".
  **R05 acceptance: introduce an explicit
  `is_daylight(hour, date, lat, lon)` helper that
  uses the astronomical sunrise/sunset calculation
  (e.g. ``astral`` library) and has unit tests for
  Kyiv EEST and EET (DST boundary).**

## Cross-references for the live deployment

| Source | Real value (read once, recorded for verification) |
|--------|---------------------------------------------------|
| Live battery_capacity_kwh | operator options — NOT read at research time; will be checked at deploy |
| Live charge_efficiency | operator options |
| Live discharge_efficiency | operator options |
| Live reserve_soc | operator options |
| Live grid_v at 192.168.1.220 | from inverter realtime endpoint (read by coordinator) |
| Live PV-input max | device config endpoint |
| Live night_charge_window | (23, 7) per source tree default |
| Live tariff_schedule | (length 24?) operator-provided |

The next block will need to read these values from
the live HA / config to confirm they are in the
expected ranges, before applying any sanitiser changes.

## Acceptance fixtures (for the production changes)

For R04, the test must:
  - run `build_planner_inputs` with each of:
    * `battery_capacity_kwh=0.4` → must be refused
      (below lower bound), `missing_fields` contains
      `battery_capacity`.
    * `battery_capacity_kwh=48000.0` → must be refused
      (above upper bound), `missing_fields` contains
      `battery_capacity`.
    * `charge_efficiency=1.5` → must be clamped to 1.0
      AND `missing_fields` must contain
      `charge_efficiency`.
    * `reserve_soc=120` → must be clamped to 100 AND
      `missing_fields` must contain `reserve_soc`.
  - integration test on `InverterCoordinator` that
    passes a `pv_max_w` in the device config and
    asserts that the planner's output never exceeds
    `pv_max_w`.
  - live-probe check (post-deploy): for a real
    10 kW inverter, ensure the max `power_w` in the
    hourly forecast ≤ inverter's PV-input max.

For R05, the test must:
  - introduce an `is_daylight(hour, date, lat, lon)`
    helper in `hems/daylight.py` (or similar).
  - unit test for Kyiv on 2026-06-21 (longest day)
    and 2026-12-21 (shortest day).
  - unit test for the DST boundary: 2026-10-25
    (DST ends in EU, EEST→EET) at hour=03:00 →
    must be off-by-one aware.
  - tariff schedule validation: 24-element list with
    one negative value → must refuse; one NaN value
    → must refuse; length 25 → must refuse.
  - night window validation: `(23, 23)` → must
    reject or treat as "no window"; `(24, 7)` → must
    reject (`24` is not a valid hour).
  - live-probe check (post-deploy): the actual
    daylight window for today (Kyiv) is consistent
    with the operator's night-charge window.

## Open questions for Юра before R04 implementation

  1. What is the actual `battery_capacity_kwh` in the
     live deployment? (Need to read from
     `.storage/core.config_entries` for the
     powmr_inverter entry.)
  2. What is the inverter's nameplate PV-input
     power? (Need to read from the device config
     endpoint response or the operator's notes.)
  3. Is the tariff schedule still in use, or has it
     been deprecated in favour of a fixed
     night-window? (Need to confirm against
     `const.py` and the options flow.)
  4. Does the inverter expose a nameplate charge
     current and discharge current, or only the
     aggregate "max power"?

Until these are answered, no production change to
the planner or telemetry sanitiser is appropriate.

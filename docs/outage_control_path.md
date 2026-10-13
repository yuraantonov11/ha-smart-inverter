# Outage Control Path — Phase 1 Analysis

**Scope.** Read-only analysis of the production control path for a future
*planned-outage preparation* feature. This document only **traces** who
controls what and which API call hits the inverter; it does **not** propose
code, UI, or deployment.

**Repository state at analysis time.**
- Branch: `develop`, HEAD = `ba0970e5cf3897fa345b0aa3f1236cfac289213b`
- Python venv: `.test-venv/bin/python` (pytest, jinja2, pyyaml,
  websocket-client, requests installed)
- HA host config entry id: `01M3XWJ8DRYDQC8A0NCPRVB53N`
- Live entity registry cross-checked via SSH on the HA host (read-only,
  `/config/.storage/core.entity_registry`); the slug `garazh_smart_solar_inverter`
  comes from HA's auto-slug of the device name.

> **Convention.** All line numbers below refer to the source files in this
> checkout. Every claim about "writes to the inverter" is backed by a
> literal `await api.set_*` call site. The Phase 3 (automatic-prep) work
> **must** reuse the same call sites through the protected dispatcher
> — see §3. Bypassing it is a contract violation.

---

## 1. Three distinct control entities (do not conflate)

The integration exposes three **independent** control surfaces that the
operator (and a future outage planner) must keep separate. They are
*not* synonyms:

| # | Live `entity_id` (on entry `01M3XWJ8DRYDQC8A0NCPRVB53N`) | Source-of-truth (HA domain) | Class (file:line) | Options / values | Where the value is written |
|---|---|---|---|---|---|
| 1 | `select.garazh_smart_solar_inverter_hems_smart_mode` | `SelectEntity` | `InverterSmartModeSelect` — `select.py:249-271` | `SMART_MODE_OPTIONS = ["Adaptive", "Arbitrage", "Storm"]` (`select.py:41`) → ints `0/1/2` (`SMART_MODE_VALUE_MAP`, `select.py:77-81`) | `coordinator.async_set_smart_mode(mode)` — `coordinator.py:720-755` — writes `coordinator.smart_mode` (int) **and** `entry.options["smart_mode"]`. **Does NOT call the inverter API.** |
| 2 | `select.garazh_smart_solar_inverter` *(predictive_mode, auto-suffixed to bare slug because two predicts collided)* | `SelectEntity` | `InverterPredictiveModeSelect` — `select.py:396-454` | `PREDICTIVE_MODE_OPTIONS = ["Off", "Shadow", "Assist"]` (`select.py:44`) → strings `"off"/"shadow"/"assist"` (`PREDICTIVE_MODE_VALUE_MAP`, `select.py:110-114`) | `coordinator.async_set_predictive_mode(value)` — `coordinator.py:660-685` — writes `self._predictive_tuning.predictive_mode` and `entry.options["predictive_mode"]`. **Does NOT call the inverter API.** |
| 3 | `switch.garazh_smart_solar_inverter_hems_auto_mode` | `SwitchEntity` | `InverterHemsAutoModeSwitch` — `switch.py:117-145` | on/off (boolean) | `coordinator.async_set_hems_auto_mode(bool)` — `coordinator.py:707-718` — writes `coordinator.hems_auto_mode` and `entry.options["hems_auto_mode"]`. **Does NOT call the inverter API.** |
| 3' | `switch.garazh_smart_solar_inverter` *(predictive_assist, auto-suffixed to bare slug)* | `SwitchEntity` | `InverterPredictiveAssistSwitch` — `switch.py:367-432` | on when `coordinator.predictive_mode in ("shadow", "assist")` (`switch.py:402`) | `coordinator.async_set_predictive_mode("assist")` on `turn_on`, `"off"` on `turn_off` (`switch.py:425, 430`). This is a **legacy mirror** of the predictive_mode select; it routes through the same canonical writer, so the two cannot drift. |

> **Note on entity slugs.** The integration creates the device with
> `name="Smart Solar Inverter"` (`__init__.py:295`). HA auto-slugifies
> that to `smart_solar_inverter`, but on the live host the entities are
> registered as `garazh_smart_solar_inverter_*` (the user renamed the
> device in HA). The integration code refers to source-of-truth via
> `entity_description.key` and `unique_id = f"{api.device_sn}_{key}"`
> (`select.py:174-176`, `switch.py:128, 391`), not the slug.
> The literal `garazh_smart_solar_inverter` strings in `__init__.py:917`,
> `coordinator.py:2073`, `hems/pv_coordinator.py:238, 254, 506` are
> **dashboard string concatenations** and do not affect control paths.

> **Live entry.options snapshot** (read once, redacted):
> `predictive_mode="shadow"`, `smart_mode=0` (Adaptive),
> no `hems_auto_mode` key (defaults to True), no `reserve_soc` key
> (defaults to 20.0 via `compute_reserve_soc`,
> `hems/options_helpers.py:225-241`).

### Three distinctions the doc MUST make explicit

1. **`select.garazh_smart_solar_inverter_hems_smart_mode` is a HEMS
   PLANNER MODE switch, not a charger setpoint.** It mutates
   `coordinator.smart_mode: int` (`coordinator.py:746`) and is consumed
   by the engine as `inputs["smart_mode"]` (`coordinator.py:1370`).
   It does **not** touch `_predictive_user_target_soc`, the inverter
   output priority, or the charger priority. Treating it as a hardware
   setpoint would be a bug.

2. **`select.garazh_smart_solar_inverter_predictive_mode` is the
   PREDICTIVE ML planner state** (Off/Shadow/Assist). It mutates
   `coordinator._predictive_tuning.predictive_mode: str`
   (`coordinator.py:671`). The actual inverter commands (output /
   charger priority) are computed inside `HemsEngine.evaluate` +
   `PredictiveControlEngine._finalize_decision`
   (`hems/predictive_control.py:301-329`) and may or may not equal the
   planner mode. It is **not** a hardware switch and never calls
   `api.set_*`.

3. **`switch.garazh_smart_solar_inverter_hems_auto_mode` is the
   MASTER HEMS ENABLE switch** (on → engine runs, off → engine
   returns `"hems_auto_off"` immediately, `coordinator.py:1152-1156`).
   It mutates `coordinator.hems_auto_mode: bool` (`coordinator.py:328,
   717`) and is consulted at the top of every `_run_hems_engine`
   call (`coordinator.py:1152`).

4. **The `output_priority` and `charger_priority` SELECTS ARE the
   hardware-bound selects** (i.e. they ARE inverter commands). They
   call `await self.coordinator.api.set_output_priority(value)`
   (`select.py:207`) and `await … .set_charger_priority(value)`
   (`select.py:240`) directly. Setting them bypasses HEMS and
   programmes the inverter. The future outage planner MUST NOT call
   these directly — it must reuse the protected dispatcher in §3.

> **Operator-intent rule** — a second recurring invariant: the operator
> enabling **Assist** (`predictive_mode = "assist"`) is an EXPLICIT
> choice written through `coordinator.async_set_predictive_mode`
> (`coordinator.py:660`). The integration does **not** auto-flip
> predictive_mode on the basis of any readiness flag — readiness is
> computed *inside* `PredictiveControlEngine._finalize_decision` and
> only gates whether a *proposal* is allowed (`eligible=False` skips
> the write, `hems/predictive_control.py:304-317`). The only places
> that auto-change the user-visible HEMS state are:
>
> - `coordinator.async_set_smart_mode` for explicit user picks
>   (`coordinator.py:720-755`),
> - the auto-Storm cause layer for `smart_mode`, which uses the two
>   `_auto_storm_*` boolean causes (weather / outage) instead of
>   overwriting `self.smart_mode` (see `coordinator.py:1043-1104`,
>   `coordinator.py:1888-1932`),
> - and the predictive_assist switch which routes through the same
>   canonical writer (`switch.py:425, 430`).
>
> Do **not** invent "≥N PV pairs" or any other heuristic for auto-
> enabling Assist; the readiness conditions are exactly the ones
> listed in §1.A below.

### §1.A — Actual Assist / readiness conditions (from production code)

The `Assist` *gating* predicate that allows the predictive proposal to
actually be applied is a conjunction of nine conditions in
`PredictiveControlEngine._finalize_decision`
(`hems/predictive_control.py:301-317`):

```python
eligible = (
    self._predictive_mode == "assist"                  # operator pick
    and self._predictive_ready                         # see block A
    and inputs["smart_mode"] in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE)
    and inputs["soc"] > floor                          # floor = max(reserve_soc+2, min_operating_soc)
    and inputs["grid_available"]
    and not self.keepalive.in_progress
    and not decision.skip
    and decision.reason not in ("hysteresis_recovery",
                                "evening_reserve_protection",
                                "emergency_low_soc_daytime",
                                "reserve_soc_protection")
    and proposal is not None and not proposal.skip
    and proposal.output_priority in (OutputPriority.USB, OutputPriority.SBU)
    and proposal.charger_priority in (ChargerPriority.SNU, ChargerPriority.OSO)
)
# A calibrated PV shortfall is a recommendation, not proof of a storm.
if eligible and getattr(self._last_predictive_hint, "storm_preemption", False) \
        and not self.predictive_storm_allowed:
    eligible = False
```

Block **A** — `self._predictive_ready` is set per-evaluation at
`hems/predictive_control.py:255-261`:

```python
self._predictive_ready = (
    math.isfinite(confidence)
    and confidence >= max(0.2, self.predictive_min_confidence)
    and samples >= 3
)
```

with `samples` and `confidence` taken from
`controller.calibrator.metrics()` — i.e. the bounded
`ForecastCalibrator` (`hems/forecast_calibration.py:38-50, 128, 190-194`).
The threshold `self.predictive_min_confidence` is the option
`predictive_min_confidence_for_assist` (default `0.2`,
`hems/predictive_control.py:22`); the `samples >= 3` constant is hard-
coded.

A second `check_assist_ready()` lives on `PvLearningCoordinatorMixin`
(`hems/pv_coordinator.py:636-641`) and uses the same
`ForecastCalibrator` metrics with **the same two thresholds**
(`sample_count >= 3 AND confidence_factor > 0.2`). It is currently
**only used for logging** ("Assist-ready" / "Auto-assist blocked") and
is not the gate that blocks writes — the gate is the
`self._predictive_ready` block above.

> **Conclusion for Phase 2/3.** The minimum production thresholds for
> "the system is willing to dispatch a Predictive proposal" are:
> `predictive_min_confidence_for_assist ≥ 0.2` (configurable),
> `calibrator.sample_count ≥ 3` (hard-coded),
> `calibrator.confidence_factor > 0.2` (hard-coded),
> `predictive_mode == "assist"` (operator pick), and the
> smart_mode / SOC / grid_available conjuncts above. There is **no**
> `≥2 PV pairs` rule anywhere in the engine code path; the doc must
> not invent one.

---

## 2. Control entities table (per operator-visible surface)

The Phase 2 planner will need to know, for each entity the operator
can move, **exactly which method on which object flips it**, and
**whether that flip ever hits the inverter API**. The table below
covers every entity the operator can write through this integration.
The two columns `protected?` and `api_call` tell Phase 3 whether the
call site is reusable as-is or whether it must be wrapped.

| `entity_id` (live) | Class (file:line) | `key` (source-of-truth) | `unique_id` (format) | write_path (file:line) | `api_call` (the actual inverter call) | protected? |
|---|---|---|---|---|---|---|
| `select.garazh_smart_solar_inverter_output_priority` | `InverterOutputPrioritySelect` — `select.py:183-213` | `output_priority` | `{device_sn}_output_priority` (`select.py:175`) | `async_select_option` — `select.py:204-213` | `await api.set_output_priority(value)` → `api.set_config_item("outputSourcePrioritySetting", value)` (`api.py:1412-1414, 1267-1410`) | **NO** — direct API call. Phase 3 must NOT use this entry point; the outage planner must go through §3. |
| `select.garazh_smart_solar_inverter_charger_priority` | `InverterChargerPrioritySelect` — `select.py:216-246` | `charger_priority` | `{device_sn}_charger_priority` | `async_select_option` — `select.py:237-246` | `await api.set_charger_priority(value)` → `api.set_config_item("chargerSourcePrioritySetting", value)` (`api.py:1416-1418`) | **NO** — same. |
| `select.garazh_smart_solar_inverter_battery_type` | `InverterBatteryTypeSelect` — `select.py:274-315` | `battery_type` | `{device_sn}_battery_type` | `async_select_option` — `select.py:299-315` | `await self.coordinator.api.set_config_item("settingBatteryType", value)` (`select.py:310`) | NO — direct API call, no gate. |
| `select.garazh_smart_solar_inverter_ac_input_range` | `InverterAcInputRangeSelect` — `select.py:318-346` | `ac_input_range` | `{device_sn}_ac_input_range` | `async_select_option` — `select.py:339-346` | `await self.coordinator.api.set_config_item("acInputRangeSetting", value)` (`select.py:341`) | NO. |
| `select.garazh_smart_solar_inverter_hems_smart_mode` | `InverterSmartModeSelect` — `select.py:249-271` | `smart_mode` | `{device_sn}_smart_mode` | `async_select_option` — `select.py:268-271` | **none** — calls `coordinator.async_set_smart_mode(mode)` (`coordinator.py:720-755`); persists to `entry.options["smart_mode"]`. | n/a — no API call. |
| `select.garazh_smart_solar_inverter_predictive_mode` *(live bare slug: `select.garazh_smart_solar_inverter`)* | `InverterPredictiveModeSelect` — `select.py:396-454` | `predictive_mode` | `{device_sn}_predictive_mode` | `async_select_option` — `select.py:449-454` | **none** — `coordinator.async_set_predictive_mode(value)` (`coordinator.py:660-685`); persists to `entry.options["predictive_mode"]`. | n/a. |
| `select.garazh_smart_solar_inverter_predictive_target_soc` | `InverterPredictiveTargetSelect` — `select.py:349-393` | `predictive_target_soc` | `{entry_id}_predictive_target_soc` (`select.py:358`) | `async_select_option` — `select.py:389-393` | **none** — `coordinator.async_predictive_feedback("modify", 30, int(option))` (`select.py:392`); mutates only `coordinator._hems._predictive_user_target_soc` and `coordinator._hems._manual_override_until`. See §2.A for the full path. | n/a — no API call. |
| `number.garazh_smart_solar_inverter_max_charging_current` | `InverterMaxChargingCurrent` — `number.py:74-101` | `max_charging_current` | `{device_sn}_max_charging_current` | `async_set_native_value` — `number.py:96-101` | `await self.coordinator.api.set_max_charging_current(amps)` → `api.set_config_item("setMaxChargingCurrent", str(amps))` (`api.py:1420-1423`) | NO — direct. |
| `number.garazh_smart_solar_inverter_max_utility_charging_current` | `InverterMaxUtilityChargingCurrent` — `number.py:104-131` | `max_utility_charging_current` | `{device_sn}_max_utility_charging_current` | `async_set_native_value` — `number.py:126-131` | `await self.coordinator.api.set_max_utility_charging_current(amps)` → iterates `("setUtilityMaxChargingCurrent", "maxUtilityChargingCurrent")` (`api.py:1425-1432`) | NO — direct. |
| `sensor.garazh_smart_solar_inverter_battery_soc` | `InverterSensor` — `sensor.py:424-451` (via `SENSORS` tuple, `sensor.py:176-185`) | `battery_soc` | `{device_sn}_battery_soc` | (read-only) | **none** — `native_value` reads `coordinator.data["batterySoc"]` via `value_fn` (`sensor.py:184`) | n/a — read-only sensor. |
| `sensor.garazh_smart_solar_inverter_battery_soc_corrected` | `InverterSensor` — `sensor.py:424-451` (via `SENSORS`, `sensor.py:186-199`) | `battery_soc_corrected` | `{device_sn}_battery_soc_corrected` | (read-only) | **none** — reads `coordinator.data["correctedSoc"]` (`sensor.py:194`); `coordinator.data["correctedSoc"]` is computed in `_async_update_data` from `get_real_soc(reported_soc, voltage, current)` (`coordinator.py:1012-1037`, `hems/soc_correction.py:40-73`). | n/a — read-only sensor. |
| `sensor.garazh_smart_solar_inverter_pv_power` | `InverterSensor` (`SENSORS`, `sensor.py:104-113`) | `pv_power` | `{device_sn}_pv_power` | (read-only) | **none** — `coordinator.data["pvPower"]` (`sensor.py:112`). | n/a. |
| `sensor.garazh_smart_solar_inverter_load_power` | `InverterSensor` (`SENSORS`, `sensor.py:134-143`) | `load_power` | `{device_sn}_load_power` | (read-only) | **none** — `coordinator.data["loadPower"]` (`sensor.py:142`). | n/a. |

### §2.A — Full path of the manual SOC target (`predictive_target_soc`)

The `select.garazh_smart_solar_inverter_predictive_target_soc` entity
is **not** a charger setpoint. Its full lifecycle in production code:

1. **Write** — `InverterPredictiveTargetSelect.async_select_option`
   (`select.py:389-393`):
   - validates `option ∈ "20".."100"` (`select.py:390-391`),
   - schedules
     `coordinator.async_predictive_feedback("modify", 30, int(option))`
     (`select.py:392`) — **note: the production call is missing
     the `await` keyword**, so this is a fire-and-forget schedule;
     the coroutine object is created and the engine attribute
     is only mutated if a different caller (the
     `predictive_feedback` HA service at
     `services/__init__.py:720`, which uses `await`) hits the
     same code path. The behavioural test
     `tests/test_outage_control_path.py` exercises the exact
     production source and asserts this asymmetry.
2. **Store (in-memory only, by design of `apply_feedback`)** — when
   the coroutine *is* awaited (e.g. by the HA service), the
   function `coordinator.async_predictive_feedback`
   (`coordinator.py:691-701`) calls
   `apply_feedback(self._hems, "modify", datetime.now(), 30, int)`
   (`hems/predictive_control.py:64-81`) which:
   - computes `until = now + timedelta(minutes=30)` (`predictive_control.py:74`),
   - bumps it forward if a longer hold is already active
     (`predictive_control.py:76-77`),
   - writes
     `engine._manual_override_until = until` (`predictive_control.py:78`)
     and
     `engine._predictive_user_target_soc = new_target_soc`
     (`predictive_control.py:79`),
   - returns a record that the coordinator copies to
     `coordinator._hems.predictive_decision_state`
     (`coordinator.py:699`,
     `predictive_control.py:144, 149, 281`).
3. **Persist (option only, NOT the value)** — the record is **not**
   written to `entry.options`; the hold is *re-armed on each reload*
   only when the operator picks it again. There is **no** key
   `predictive_feedback_override` in the live `entry.options`; the
   call site at `coordinator.py:343`
   (`restore_feedback(self._hems, entry.options.get("predictive_feedback_override", {}), datetime.now())`)
   is currently fed an empty dict at every reload.
4. **Read by the select** — `current_option` returns
   `str(coordinator._hems._predictive_user_target_soc)`
   (`select.py:362-364`); `extra_state_attributes` exposes the same
   field plus `has_manual_target`
   (`select.py:367-387`).
5. **Use by the planner** — `PredictiveControlEngine.evaluate` clears
   the field on every cycle if the hold has expired
   (`hems/predictive_control.py:120-121`):

   ```python
   if not self._manual_override_until or self._manual_override_until <= now:
       self._predictive_user_target_soc = None
   ```

   The cleared value is *published* in
   `predictive_decision_state["target_soc"]`
   (`predictive_control.py:144, 268`) for observability, but the
   engine's planning math (`HemsEngine.evaluate`, `engine.py:306+`)
   does **not** read `inputs._predictive_user_target_soc`; the
   actual SOC target is computed by the planner hint path
   (`plan_soc_targets`, `hems/predictive.py:692-775`).
6. **Suppress writes while the hold is active** — the
   `_manual_override_until` is consumed by the baseline engine as
   `Reason.MANUAL_OVERRIDE` at `hems/engine.py:512-517`:

   ```python
   elif self._manual_override_until and now < self._manual_override_until:
       if self._last_manual_override_log is None or now - self._last_manual_override_log > timedelta(minutes=5):
           remaining = (self._manual_override_until - now).total_seconds()
           _LOGGER.info("HEMS: observed command-readback mismatch hold (%.0fs remaining)", remaining)
           self._last_manual_override_log = now
       reason = _Reason.MANUAL_OVERRIDE
   ```

   i.e. the operator's "30-minute pause" is the *hold*, not the
   stored integer. Setting a value via this select pauses HEMS
   dispatch for up to 30 min, but does not directly programme the
   inverter.
7. **Expiry** — **lazy**, no `async_call_later` /
   `async_track_point_in_utc_time` is wired anywhere
   (`rg -S 'async_call_later|async_track_point_in_utc_time' hems/ coordinator.py` returns no production hits — the only `@callback` decorator is in `config_flow.py:576`). The hold is re-evaluated on the
   next `engine.evaluate(...)` call, which runs on every coordinator
   poll (5 s default, `coordinator.py:DEFAULT_POLL_INTERVAL_SEC`).
   When `now >= _manual_override_until`, the next evaluate clears
   `self._predictive_user_target_soc = None`
   (`predictive_control.py:121`).

> **Implication for the outage planner.** The
> `predictive_target_soc` is a *pause* for the HEMS engine, not a
> charger setpoint and not a planner input. The planner must compute
> its own SOC target from the inputs in §4 and either (a) use a
> private "manual hold" attribute for its own pause, or (b) reuse
> the same `_manual_override_until` mechanism with a different
> intent code. Treating `_predictive_user_target_soc` as a charger
> setpoint would be a bug.

---

## 3. Existing protected command path (must be reused by Phase 3)

The integration funnels **all** HEMS-driven inverter writes through a
single, well-instrumented function. A future *automatic* outage-prep
mode must reuse this path; calling `api.set_*` from anywhere else is
a contract violation.

### §3.1 — The single protected dispatcher

- **Entry point**: `InverterCoordinator._execute_hems_command(decision, command_id, evidence_recorded)`
  — `coordinator.py:1644-1707`.
- **What it does** (in order):
  1. Records an evidence-journal `command_scope` with a
     `command_id` UUID (`coordinator.py:1667`,
     `hems/evidence_journal.py: command_scope`).
  2. Calls
     `self.api.set_output_priority(decision.output_priority)`
     (`coordinator.py:1670`) — *only if* `decision.output_priority is not None`.
  3. Calls
     `self.api.set_charger_priority(decision.charger_priority)`
     (`coordinator.py:1679`) — *only if* `decision.charger_priority is not None`.
  4. Toggles `buzzerAlarmSetting` 0/1 for acoustic comfort
     (`coordinator.py:1687-1695`) when the engine asks.
  5. Wraps the whole thing in `try/except` and reports success /
     failure to the engine circuit-breaker
     (`coordinator.py:1697-1705`,
     `hems/engine.py:788-822`).
  6. Calls `confirm_predictive_delivery(success)` so the predictive
     state machine logs the applied/not-applied outcome
     (`coordinator.py:1706-1707`).

### §3.2 — Safety gates upstream of the dispatcher

The dispatcher is reached only through `_run_hems_engine`
(`coordinator.py:1178-1642`). Before `_execute_hems_command` is
called, the following gates fire:

- **T01 hard gate — no writes while SOC is unknown.**
  `coordinator.py:1190-1193, 1464+`: even if the engine returns a
  `decision`, the gate refuses to call `_execute_hems_command` and
  zeros out the diagnostic `hems_last_*` attributes so the dashboard
  cannot advertise a write that did not happen.
- **Manual-override hold** — see §2.A step 6.
- **Circuit breaker** — `hems/engine.py:510-511, 788-822`; consecutive
  failures arm a `_blocked_until` timestamp that suppresses writes.
- **Command dedup window + dwell lock** — `HemsEngine._apply_anti_flapping`
  (`hems/engine.py:1353-1391`). Suppresses:
  - duplicate writes of the same value within
    `tun.command_dedup_window_sec` (engine.py:1362-1373),
  - output-priority switches within `tun.min_mode_hold_min`
    *unless* the new value is `USB` (safety override; engine.py:1375-1387).
- **Telemetry-bounds guard** — `_FINITE_TELEMETRY_BOUNDS`
  (`coordinator.py:88-89, 105-189`): SOC and reserve_soc must be
  finite and in `[0, 100]`; PV / load / grid / battery power must be
  finite; an entry-option key that violates a bound is replaced with
  the documented default and a warning is recorded.
- **Auto-storm cause layer** — the `_auto_storm_weather` /
  `_auto_storm_outage` booleans (`coordinator.py:582-583, 1043-1104,
  1888-1932`) do not directly write commands; they are inputs to
  `effective_mode` (coordinator.py:1202-1225) which is what the
  engine actually sees.

### §3.3 — Rate-limit / debounce / write-coalescing helpers

The HEMS engine does **not** implement a write-coalescing queue. The
de-duplication is a *passive* gate inside `_apply_anti_flapping`
(engine.py:1353-1391). The active rate-limit is one level deeper, in
the API client:

- **API rate-limit** — `InverterApiClient._apply_rate_limit(endpoint)`
  (`api.py:279-298`):
  - `_last_request_time: dict[str, float]` keyed by endpoint URL
    (`api.py:189`).
  - Guarded by an `asyncio.Lock`
    (`_rate_limit_lock` declared near the top of the class —
    `api.py:189-192`).
  - Minimum interval `MIN_REQUEST_INTERVAL_MS = 1000` (`const.py:44`).
  - Sleeps the coroutine if the per-endpoint cooldown is not yet
    expired (`api.py:291-297`).
- **Per-endpoint cooldown** — every `set_config_item`,
  `set_mode`, `fetch_*` call funnels through
  `self._apply_rate_limit(...)` first
  (`api.py:396, 524, 636, 688, 951, 1243, 1329, 1447, 1541`).
- **Retry / backoff for `set_config_item`** — `api.py:1328-1393`:
  one automatic retry with exponential backoff (0.5 s → 1 s, capped
  at 4 s, plus up to 100 ms jitter — `api.py:1358-1364`) before
  surfacing failure. This is the only retry policy on writes.

### §3.4 — Code paths that BYPASS the protected dispatcher (Phase 3 must forbid them)

A complete enumeration of every production call site that reaches the
inverter API outside `_execute_hems_command`. These exist for the
operator's direct actions; the future outage planner must NOT add new
callers to any of them.

| File:line | Call | Triggered by |
|---|---|---|
| `select.py:207` | `await api.set_output_priority(value)` | operator picking `output_priority` select |
| `select.py:240` | `await api.set_charger_priority(value)` | operator picking `charger_priority` select |
| `select.py:310` | `await self.coordinator.api.set_config_item("settingBatteryType", value)` | operator picking `battery_type` |
| `select.py:341` | `await self.coordinator.api.set_config_item("acInputRangeSetting", value)` | operator picking `ac_input_range` |
| `number.py:98` | `await self.coordinator.api.set_max_charging_current(amps)` | operator moving `max_charging_current` |
| `number.py:128` | `await self.coordinator.api.set_max_utility_charging_current(amps)` | operator moving `max_utility_charging_current` |
| `number.py:156` | `await self.coordinator.api.set_config_item(self._setting_key, str(value))` | operator moving any `_InverterConfigNumber` (voltages, equalization, SBU return, grid-tie) |
| `switch.py:172, 182, 257, 267` | `await self.coordinator.api.set_config_item(self._setting_key, "0"/"1")` | operator toggling the `_InverterConfigSwitch` entities (grid feed-in, overload restart, equalization, dual output, etc.) |
| `switch.py:215, 225` | `await self.coordinator.api.set_output_priority("2"/"0")` | operator toggling the `backup_mode` switch |
| `services/__init__.py:465` | `await api.set_output_priority(value)` | HA service `set_output_priority` |
| `services/__init__.py:475` | `await api.set_charger_priority(value)` | HA service `set_charger_priority` |
| `services/__init__.py:605, 621, 637, 652, 668` | `await api.set_config_item(...)` (grid charging, grid feed-in, backup mode, charge limit, grid charge power) | HA services `enable_*` / `set_*` |

> **Verification command used** (production files only, tests excluded):
>
> ```bash
> rg -n -S '\b(set_output_priority|set_charger_priority|set_max_utility_charging_current|set_max_charging_current|set_config_item|set_mode)\b' \
>   -g '*.py' -g '!tests/**'
> ```
>
> Combined with a filter for `select.py|number.py|switch.py|services/__init__.py|coordinator.py` —
> the only `coordinator.py` hits are inside `_execute_hems_command`
> (which is the protected path). Everything else is operator-only.

> **No HTTP bypass.** Every `self._session.post(...)` and
> `self._session.get(...)` call lives inside `InverterApiClient` in
> `api.py` (lines 414, 528, 639, 694, 959, 965, 1251, 1348, 1453,
> 1484, 1543, 1608). No production module constructs an aiohttp
> session outside the API client. The future outage planner therefore
> cannot "accidentally" open a new HTTP path.

---

## 4. Telemetry fields the future outage planner will read

All of the following are **already** populated on
`coordinator.data` and exposed as `CoordinatorEntity` sensors.
Phase 2 may read them directly from `coordinator.data`; it does not
need to poll the inverter API.

| Field (key on `coordinator.data`) | Live `entity_id` | Where the value lands in the cache | Where the coordinator pulls it from | Code ref |
|---|---|---|---|---|
| `batterySoc` *(raw)* | `sensor.garazh_smart_solar_inverter_battery_soc` | `coordinator._async_update_data` returns `{**raw, "correctedSoc": corrected_soc, "gridAvailable": grid_available, ...}` — `coordinator.py:1158-1166` | `InverterApiClient._parse_realtime_payload` → `_first_present(raw_fields, ("batterySoc", "batteryCapacity"), default=None)` — `api.py:1203-1212` | `sensor.py:184` (raw); `coordinator.py:1012-1031` (T01 hardening — None when missing/invalid) |
| `correctedSoc` | `sensor.garazh_smart_solar_inverter_battery_soc_corrected` | same as above | computed from `get_real_soc(reported_soc, voltage, current)` in `_async_update_data` — `coordinator.py:1034-1037`; LiFePO4 16S OCV + IR-drop (`hems/soc_correction.py:40-73`) | `sensor.py:194`; `coordinator.py:1158-1166` |
| `pvPower` | `sensor.garazh_smart_solar_inverter_pv_power` | `coordinator._async_update_data` `result["pvPower"]` | API payload `pvPower` — `api.py:1170-1201` | `sensor.py:112` |
| `loadPower` | `sensor.garazh_smart_solar_inverter_load_power` | `coordinator._async_update_data` `result["loadPower"]` | API payload `loadPower` — `api.py:1170-1175, 1193-1195` | `sensor.py:142` |
| `gridPower`, `batteryPower`, `batteryVoltage`, `batteryCurrent` | `sensor.garazh_smart_solar_inverter_grid_power`, `…_battery_power`, `…_battery_voltage`, `…_battery_current` | same | `api.py:1140-1220` | `sensor.py:122, 132, 172, 242` |
| **grid-connected signal** | `binary_sensor.garazh_smart_solar_inverter_grid_available` | `coordinator.data["gridAvailable"]` (boolean) | `coordinator._evaluate_grid(grid_v)` in `_async_update_data` — `coordinator.py:1040-1041`; result published by `coordinator.data["gridAvailable"]` (`coordinator.py:1161`) | `coordinator.py:1040, 1161` |
| **grid-outage alarm** | `binary_sensor.garazh_smart_solar_inverter_problem` (key `grid_outage`) | `coordinator._grid_outage` flag | `_evaluate_grid` flips this when the transition is `"outage"` — `coordinator.py:1040-1041, 1067-1087` | `coordinator.py:1087, 1161` |
| `outputSourcePriority`, `chargerSourcePriority` | `select.…_output_priority` / `…_charger_priority` (current_option reads from cache) | `coordinator.data` after each successful write | `api.py` (re-fetched in `_async_update_data` after a `set_config_item` ACK — `coordinator.py:1417` block + `select.py:209-210, 242-243` for last-cmd timestamps) | `select.py:200-202, 234-235` |
| `deviceSettings` (dotted dict) | n/a — only consumed by `number.py` / `select.py` (battery_type, voltages, equalization) | `_async_update_data` builds `result["deviceSettings"]` — `coordinator.py:1165` | `fetch_device_configs` (batch read) — `api.py:1434-1600` | `coordinator.py:1165` |
| **forecast tomorrow (kWh)** | `sensor.garazh_smart_solar_inverter_pv_forecast_tomorrow` | `coordinator.forecast_tomorrow_kwh: float \| None` (instance attribute) | `PvLearningCoordinatorMixin._apply_forecast_to_pv_state` — `hems/pv_coordinator.py:713` (`self.forecast_tomorrow_kwh = fc.energy_kwh if fc else None`) | `coordinator.py:556, 1382` |
| **forecast today (kWh)** | (no live entity in registry; the value is `self._forecast_today_kwh`, internal only) | `hems/pv_coordinator.py:574` | `hems/pv_coordinator.py:574, 603` | `coordinator.py:368, 1383` |
| **forecast uncertainty** (calibrator) | n/a directly; surfaces as `sample_count`, `confidence_factor`, `bias_w`, `mae_w` on `sensor.garazh_smart_solar_inverter_predictive_decision_state` and `sensor.predictive_day_ahead` | `hems/forecast_calibration.py:38-50, 128, 190-194` (`CalibrationMetrics`) | `PvLearningCoordinatorMixin.check_assist_ready` and `_log_pv_calibrator_state` — `hems/pv_coordinator.py:627-641` | `hems/pv_coordinator.py:632-633` |
| **battery capacity (kWh)** | n/a — no live sensor; cached on coordinator | `coordinator._battery_capacity_kwh` | `_derive_battery_capacity(entry.options)` — `coordinator.py:2215-2276`. Inputs are `battery_capacity_ah` (default 230.0) and `nominal_voltage_v` (default 51.2). Both are validated independently (form range `[10, 2000]` Ah, `[10, 100]` V). When either is invalid `ok_for_planning=False` and `_battery_capacity_kwh = None` (`coordinator.py:388-396`). | `coordinator.py:384-396, 2291-2302` |
| **nominal voltage (V)** | n/a — no live sensor; cached on coordinator | `coordinator._nominal_voltage_v` | same `_derive_battery_capacity` | `coordinator.py:386, 2293` |
| **charge / discharge efficiency** | n/a — no live sensor; set by `HemsTunables` | `hems/engine.py:359-365` reads `self.tun.charge_efficiency` (default 0.85) and `self.tun.discharge_efficiency` (default 0.90) | `HemsTunables` defaults in `hems/tuning.py:18, 33-34, 44`. Not currently a config option; Phase 2 may want to expose it. | `hems/tuning.py:18, 33-34, 44`; `hems/engine.py:359-365` |
| **allowed charging current range** | `number.garazh_smart_solar_inverter_max_charging_current` (current value, 0–200 A in 5 A steps) and `…_max_utility_charging_current` | `coordinator.data["deviceSettings"]` | form constraints in `number.py:91-93, 121-123`; runtime guard at `api.py:1298-1303` clamps to `[0, 200]` and snaps to 5 A | `number.py:91-93, 121-123`; `api.py:1298-1312` |
| **SOC reserve floor** | n/a — no live sensor; persisted as `entry.options["reserve_soc"]` | `coordinator._run_hems_engine` reads `self._entry.options.get("reserve_soc", 20.0)` — `coordinator.py:1384`; engine uses it as the `inputs["reserve_soc"]` argument | config form in `config_flow.py:797-798` (default `DEFAULT_RESERVE_SOC`); runtime validator `_FORCE_TELEMETRY_BOUNDS["reserve_soc"] = (0.0, 100.0)` at `coordinator.py:88` | `coordinator.py:1384, 1543-1546, 1631` |
| **SOC target — operator-configurable** | `select.garazh_smart_solar_inverter_predictive_target_soc` (20-100, "pause HEMS 30 min, store the value") | `coordinator._hems._predictive_user_target_soc` (int) | `select.py:389-393 → coordinator.async_predictive_feedback` → `hems/predictive_control.py:64-81` | `select.py:389-393`; `coordinator.py:691-701`; `hems/predictive_control.py:64-81` |
| **SOC target — planner-computed** | n/a as a sensor; published on `sensor.garazh_smart_solar_inverter_predictive_decision_state.target_soc` and `sensor.predictive_hint.extra_state_attributes.target_morning_soc` / `target_evening_soc` | computed by `plan_soc_targets` — `hems/predictive.py:692-775` | `hems/predictive.py:692-775`; surfaced via `hems/predictive_control.py:268, 144, 281` | `hems/predictive.py:766-775` |
| **last-cmd observability** (helpful for "did the last write actually land?") | `sensor.garazh_smart_solar_inverter_hems_decision_reason`, `…_hems_output_command`, `…_hems_charger_command` | `coordinator.hems_last_reason`, `coordinator.hems_last_output_cmd`, `coordinator.hems_last_charger_cmd` | set in `_run_hems_engine` — `coordinator.py:1434-1436` (and zeroed by the T01 SOC gate) | `coordinator.py:1434-1436` |
| **consumption history (24×N)** | n/a | `coordinator._load_matrix` (dated `list[tuple[date, list[float], bool]]`); `coordinator._hems._consumption_history` (flat) | `hems/engine.py:1350-1358` mirrors it to the engine | `coordinator.py:1350-1358` |
| **grid tariff (day/night UAH/kWh)** | n/a | `coordinator._day_tariff_uah`, `coordinator._night_tariff_uah` | `hems/tuning.py` (`compute_adaptive_reserve_soc`) + `coordinator.apply_capacity_and_tariff_options` | `coordinator.py:1342-1343` |

> **Policy for the outage planner** (Phase 3 contract — to be
> ratified, not enforced in Phase 1):
>
> - **Missing / None / out-of-range telemetry BLOCKS automatic
>   action.** The Phase 2 planner must raise a structured "data
>   insufficient" response and the UI must display the *concrete
>   reason* (which field, what was the last value, how stale).
>   The existing T01 SOC-unknown gate (`coordinator.py:1464+`) is
>   the model.
> - **Staleness threshold.** TBD with operator; the integration's
>   own policy is `is_online=False` after 30 min without a
>   successful poll (`hems/engine.py:520-522`). Recommended
>   default for outage planning: 5 min for `batterySoc` and
>   `pvPower` / `loadPower`; 15 min for `forecast_tomorrow_kwh`;
>   "the calibrator must have at least one valid sample" for any
>   uncertainty-aware computation.
> - **Capacity sanity.** If `_battery_capacity_kwh is None` (form
>   input invalid per `coordinator._derive_battery_capacity.ok_for_planning=False`),
>   the planner must refuse and surface
>   `coordinator._capacity_input_warning` to the user.
> - **No inverter write while the grid-outage auto-storm cause is
>   active unless Phase 3 explicitly opts in.** The cause layer
>   (`coordinator._auto_storm_outage`, `coordinator.py:1087`)
>   currently runs first; the outage planner sits on top of it and
>   must decide whose output wins.

---

## 5. Open items for Phase 2 / 3 / 4

The Phase 1 analysis answers the "who controls what" question.
The questions below could not be answered from the code alone and
need either an operator decision or a Phase 2 design pass.

### For Phase 2 (event model + planner)

1. **Pluggable telemetry inputs** — is the planner allowed to read
   from the `_pv_calibrator` (a bounded history of forecast-vs-actual
   kWh pairs) for uncertainty estimates, or only from
   `coordinator.forecast_tomorrow_kwh`? The calibrator lives on
   `hems/pv_coordinator.PvLearningCoordinatorMixin._pv_calibrator`
   and exposes `sample_count`, `confidence_factor`, `bias_w`,
   `mae_w` via `metrics()` — Phase 2 may want a wrapper.
2. **What does "fresh" mean for each field?** The integration has
   no per-field freshness metadata; the planner must derive it from
   the most recent successful `_async_update_data` cycle (which is
   5 s by default; see `const.py:DEFAULT_POLL_INTERVAL_SEC`).
3. **Allowed charging current range** — Phase 2 should confirm with
   the operator whether to use the form-declared `[0, 200]` A window
   (`number.py:91-93, 121-123`) or a tighter user-configured max.
   The runtime guard at `api.py:1298-1312` already snaps to 5 A.
4. **Reserve floor in the outage context** — is
   `entry.options["reserve_soc"]` (default 20.0) the right floor
   for outage pre-charge, or should the planner require a higher
   floor (e.g. `reserve_soc + 10`)? The current code enforces
   `soc > max(reserve_soc + 2, min_operating_soc)` in
   `predictive_control.py:303` — but only for *Assist*, not for
   the rest of the engine.
5. **Insufficient-energy policy** — when the planner concludes that
   in-time charging is mathematically impossible (battery is
   already at floor, or tomorrow's deficit exceeds usable kWh),
   what does Phase 2 report? Suggested: refuse, surface the deficit
   number, the attainable target SOC, and the prep-start time as a
   best case. Never promise the house will be powered.
6. **Consumption model** — the engine reads
   `coordinator._hems._consumption_history_with_dates` (dated
   `list[tuple[date, list[float], bool]]`,
   `coordinator.py:1350-1358`) and `ConsumptionPredictor`
   (`hems/predictive.py` is large; consumption predictor lives
   near the bottom). Confirm the planner can reuse it without
   duplicating the date-filter logic.

### For Phase 3 (modes + automatic-prep dispatch)

1. **Mode state machine.** The integration already has
   `hems_auto_mode: bool` (the master enable, `switch.py:117-145`),
   `smart_mode: int` (the planner strategy, `select.py:249-271`),
   and `predictive_mode: str` (the ML planner state,
   `select.py:396-454`). The outage planner is a **fourth** surface
   (disabled / observation / automatic). Phase 3 must decide:
   - Should the outage mode live in `entry.options["outage_mode"]`
     (like `smart_mode`) and be persisted across reload?
   - Should "observation" mean "log plan but never call
     `_execute_hems_command`"? The integration already has an
     existing **observation** path: when `hems_auto_mode=False` the
     engine is skipped entirely (`coordinator.py:1152-1156`,
     `"hems_auto_off"` reason) and `_hems.invalidate_predictive` is
     called. The outage planner could reuse that pattern by
     returning a `decision` object *without* dispatching it.
2. **Automatic-prep dispatch.** The Phase 3 automatic mode **must**
   call `coordinator._execute_hems_command(decision, …)` (the
   protected path in §3) — *not* `api.set_output_priority` /
   `api.set_charger_priority` directly. A new
   `_execute_outage_command(decision)` wrapper is the natural fit
   so we can add a separate evidence-journal cause for outages.
3. **No test commands on the live inverter.** Phase 3 must not
   issue any inverter write from a test. Existing test discipline
   in `tests/test_t12_force_grid_charge_hold.py` and
   `tests/test_t13_command_ack.py` uses the ACK-aware pattern
   (`ok = await api.set_config_item(...)`) and never patches the
   live device. Phase 3 should follow the same convention.
4. **The Phase 3 dispatcher needs an `outage_intent` reason code**
   so the engine's evidence journal
   (`hems/evidence_journal.record_event` calls at
   `coordinator.py:1667-1707`) can distinguish "this was a HEMS
   decision" from "this was an outage-prep decision" without
   overloading `decision.reason`.

### For Phase 4 (notification adapter)

1. **Adapter contract.** The Phase 4 adapter is *separate* from the
   manual service. The manual service is a single
   `hass.services.async_register` call
   (e.g. `services/__init__.py:465-478`); the adapter is a polling
   loop that calls the same service with a structured payload
   (`{start, end, tz, source, received_at, status, id}`). Phase 4
   must not bypass the service and write `entry.options` or call
   the API directly.
2. **No "free text → plan" auto-commit.** The Phase 4 adapter
   should treat the outage event as a *request*, and the service
   must produce a `pre_event` state with the parsed event before
   any commit. The actual outage state then becomes
   `{…, status: "confirmed", id: <ulid>}`.
3. **Timezone discipline.** The HA host is set to `Europe/Kyiv`
   (`api.py:271`). Any ISO 8601 timestamp the adapter emits must be
   timezone-aware; the integration already raises on
   `datetime.fromisoformat(record["until"])` if the timestamp is
   naive (`hems/predictive_control.py:88-89`).

---

## 6. References

- `select.py` (455 lines) — all `SelectEntity` classes for the integration.
- `number.py` (295 lines) — all `NumberEntity` classes.
- `switch.py` (432 lines) — all `SwitchEntity` classes.
- `coordinator.py` (3443 lines) — `InverterCoordinator`, `_run_hems_engine`,
  `_execute_hems_command`, the T01 SOC-unknown gate, capacity derivation.
- `api.py` (1891 lines) — `InverterApiClient`, `_apply_rate_limit`,
  `set_config_item`, retry/backoff, `set_mode`.
- `hems/engine.py` (1400 lines) — `HemsEngine`,
  `_apply_anti_flapping`, `report_control_failure`, `_finalize_decision`.
- `hems/predictive_control.py` (339 lines) — `PredictiveControlEngine`,
  `apply_feedback`, `restore_feedback`.
- `hems/predictive.py` (1422 lines) — `plan_soc_targets`,
  `ConsumptionPredictor`, planner hint path.
- `hems/forecast_calibration.py` — `CalibrationMetrics`, `ForecastCalibrator`.
- `hems/pv_coordinator.py` — `PvLearningCoordinatorMixin`,
  `check_assist_ready`, `_log_pv_calibrator_state`.
- `hems/soc_correction.py` — `get_real_soc`, OCV lookup.
- `hems/options_helpers.py` — `compute_reserve_soc`.
- `hems/tuning.py` — `HemsTunables` defaults.
- `services/__init__.py` (36 KB) — HA services (`set_output_priority`,
  `set_charger_priority`, `enable_*`).
- `__init__.py` (2780 lines) — `async_setup_entry`, device registration,
  dashboard provisioning.

**Live evidence (read once, no live values recorded in this document):**

- `ssh root@ha 'jq -r ".data.entities[] | select(.platform==\"powmr_inverter\") | .entity_id + \"\\t\" + .unique_id" /config/.storage/core.entity_registry'` — confirms the
  `entity_id` ↔ `unique_id` mapping used in §1 / §2.
- `ssh root@ha 'jq -r ".data.entries[] | select(.entry_id==\"01M3XWJ8DRYDQC8A0NCPRVB53N\") | .options" /config/.storage/core.config_entries'` — confirms
  `predictive_mode="shadow"`, `smart_mode=0`, no `hems_auto_mode` /
  `reserve_soc` keys.

**Verification commands run during analysis (results inline above):**

```bash
.test-venv/bin/python -c "import ast; ast.parse(open('select.py').read()); ast.parse(open('coordinator.py').read()); ast.parse(open('const.py').read()); ast.parse(open('hems/engine.py').read()); print('syntax OK')"
# → "syntax OK"

rg -n -S '\b(set_output_priority|set_charger_priority|set_max_utility_charging_current|set_max_charging_current|set_config_item|set_mode)\b' \
   -g '*.py' -g '!tests/**'
# → full enumeration of bypass call sites in §3.4

rg -n -S 'async_call_later|async_track_point_in_utc_time' -g '*.py' -g '!tests/**'
# → no production hits in the integration; the only @callback decorator
#   is in config_flow.py:576 (unrelated to the predictive target expiry).
```

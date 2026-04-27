<!-- markdownlint-disable MD033 -->
# v1.4 Optimization Roadmap (HEMS v2)

> **Status:** Release Candidate  
> **Release target:** May 2026  
> **Priority:** HIGH (fundamental algorithm improvements)

---

## Executive Summary

v1.4 adds **10 optimization layers** to HEMS algorithm:
- Dynamic time windows (sunrise/sunset-based)
- Adaptive thresholds (learn from PV variance)
- Tariff forecasting (TOU / day-ahead pricing)
- Thermal load coordination (boiler/heat pump)
- Demand forecasting (learned consumption profiles)
- Battery health modeling (age/degradation)
- Grid reliability (planned outages, instability)
- Anti-flap dynamic dwell
- Mode transition delay modeling
- Optimization strategy selection (dropdown)

**Expected impact:** +15–35% overall system efficiency + UX improvements.

---

## Release Plan (phased)

### Phase 1: Foundation (v1.4-alpha, DONE ✓)
- [x] `HemsOptimizationProfile` model + data classes
- [x] `HemsTuningService` astronomical + adaptive compute
- [x] Git commit: `v1.4-alpha: add optimization infrastructure`

**Timeline:** 1 day | **Status:** ✅ COMPLETE

---

### Phase 2: Realtime Integration (v1.4-beta, DONE ✓)
**Focus:** Integrate TOP-3 with existing algorithm

#### 2a. Dynamic time windows (sunrise/sunset)
- **File:** `lib/services/hems_algorithm.dart` — replace hardcoded 07:00, 17:00, 23:00
- **Logic:** `_resolveWindows()` → uses GPS or manual config with astronomical calc
- **Impact:** +5–10% seasonal efficiency
- **Status:** ✅ DONE

```dart
// After:
final windows = _resolveWindows(useAstronomicalWindows: true, lat: ..., lon: ...);
if (currentHour >= windows.nightStart || currentHour < windows.dayStart) { /* night */ }
```

#### 2b. Adaptive PV surplus threshold + dwell
- **File:** `lib/services/hems_algorithm.dart`
- **Logic:** `_getAdaptivePvSurplusEnter()` + `_getAdaptiveDwellTime()` + `_trackSurplus()`
- **Impact:** -30% anti-flap on cloudy days + better sunny-day utilization
- **Status:** ✅ DONE

```dart
// After:
final adaptiveThreshold = _getAdaptivePvSurplusEnter();
if (surplus >= adaptiveThreshold) { /* SBU */ }
```

#### 2c. Battery health / adaptive reserve SOC
- **File:** `lib/services/hems_algorithm.dart`
- **Logic:** `_getAdaptiveReserveSoc()` → age/degradation-aware via `BatteryHealthModel`
- **Impact:** +30–50% battery lifespan + 10% more usable energy
- **Status:** ✅ DONE

```dart
// After:
final adaptiveReserve = _getAdaptiveReserveSoc();
if (soc <= adaptiveReserve + 2) { /* safety */ }
```

**Phase 2 Timeline:** 1.5 weeks | **Deliverable:** v1.4-beta ✅ COMPLETE

---

### Phase 3: Forecasting & Economics (v1.4-rc, DONE ✓)
**Focus:** Tariff + demand + grid intelligence

#### 3a. Tariff-aware charging (TOU / day-ahead)
- **File:** `lib/services/hems_algorithm.dart` — `_isChargingCheapNow()`, `_getNextCheapChargingWindow()`
- **Logic:** During night window, check if current hour is cheaper than average; defer to next cheap 2-hour block (up to 4h ahead) when expensive
- **Impact:** +15–25% cost reduction on TOU/multi-zone tariffs; graceful no-op on flat tariffs
- **Status:** ✅ DONE

#### 3b. Demand prediction
- **File:** `lib/services/hems_algorithm.dart` — `_getLoadForecastWh()`
- **Logic:** `_simulateEnergyDeficit` prefers `DemandForecastData.predictLoad(h)` (weekend/season-aware) over flat EWMA map when profile is wired
- **Impact:** +10–15% forecast precision on seasonal + weekend transitions
- **Status:** ✅ DONE

#### 3c. Grid reliability alerts
- **File:** `GridReliabilityForecast` + UI calendar for planned outages
- **Logic:** Auto-trigger Storm mode if outage within 6 hours
- **Impact:** +30% preparation for blackouts
- **Effort:** 2–3 hours

#### 3d. Thermal load optimization
- **File:** Integrate with boiler/heat pump via relay output
- **Logic:** When surplus high → redirect to heating instead of battery
- **Impact:** +10–20% solar self-consumption (no spill)
- **Effort:** 4–6 hours (hardware integration)

**Phase 3 Timeline:** 4–6 weeks | **Deliverable:** v1.4-rc + full economic stack

---

### Phase 4: Polish & Testing (v1.4 RELEASE, IN PROGRESS)
- [x] UI: Settings tab for strategy selection
- [x] UI: Parameter visualization (tunables over time)
- [x] Logs: Reason codes for every parameter change
- [x] Tests: Unit tests for new tunables + scenario playback
- [ ] Docs: Update `HEMS_MODES.md` + FAQ + migration notes

**Timeline:** 1–2 weeks | **Deliverable:** v1.4 stable release

#### Phase 4 Execution Track (from 2026-04-27)

**Scope freeze (P0, ~0.5 day)**
- Confirm `3a` + `3b` + `3c` are release scope for v1.4
- Keep `3d` thermal relay control out of v1.4 (explicitly moved to v1.5)
- **DoD:** roadmap/checklists contain no conflicting scope notes

**Task A - HEMS tunables visibility (P0, 1-1.5 days) ✅ DONE (2026-04-27)**
- **Files:** `lib/screens/settings_tab.dart`, optional helper widget in `lib/widgets/`
- Add compact diagnostics card with current adaptive values:
  - dynamic windows (day/evening/night)
  - adaptive surplus threshold
  - adaptive dwell minutes
  - adaptive reserve SOC
  - tariff mode hint (cheap/expensive window)
- **DoD:** values update live after settings changes and are visible without debug mode ✅

**Task B - Reason-coded logs (P0, ~1 day) ✅ DONE (2026-04-27)**
- **Files:** `lib/services/hems_algorithm.dart`, `lib/services/hems_tuning_service.dart`
- Standardize reason tags for every mode/parameter decision:
  - `reason=tariff_expensive_defer`
  - `reason=surplus_enter_sbu`
  - `reason=reserve_soc_protection`
  - `reason=dwell_lock`
  - `reason=grid_outage_precharge`
- **DoD:** every control write and skip path has machine-searchable `reason=` code ✅

**Task C - Test gate (P0, 1.5-2 days) ✅ DONE (2026-04-27)**
- **Files:** `test/hems_algorithm_test.dart`
- Add/finish tests for:
  - tunables sensitivity (thresholds, dwell, reserve)
  - tariff-aware defer-to-cheap-window behavior
  - outage precharge trigger and anti-flap stability
  - backward-compat default profile behavior
- **DoD:** `flutter test` green; no regressions in v1.3 baseline cases ✅

**Task D - Docs + release prep (P1, ~1 day)**
- **Files:** `HEMS_MODES.md`, `HEMS_MODES_UA.md`, `README.md`, `CHANGELOG.md`
- Add migration notes `v1.3 -> v1.4`, new strategy/tuning explanations, known limits
- **DoD:** docs and changelog match final behavior and settings UI

**Task E - Release (P0, ~0.5 day)**
- Build + smoke test + tag `v1.4` + GitHub Release assets
- **DoD:** published release with release notes and install artifacts

**Execution order:** Scope freeze -> Task A -> Task B -> Task C -> Task D -> Task E

---

## Current Architecture

```
HemsAlgorithmService (v1.3)
    │
    ├─ executeAdaptiveMode()       [hardcoded thresholds]
    ├─ executeNightArbitrage()     [fixed night window]
    └─ executeStormMode()          [no planning]

HemsAlgorithmService (v1.4)
    │
    ├─ optimizationProfile: HemsOptimizationProfile
    │   ├─ pvPeakW, batteryCapacityAh
    │   ├─ strategy: [economical/solarMaxed/batteryLife/gridReliance/hybrid]
    │   ├─ timeWindows: DailyTimeWindows [dynamic]
    │   ├─ batteryHealth: BatteryHealthModel [age-aware]
    │   ├─ thermalLoad: ThermalLoadModel [boiler coord]
    │   ├─ tariffForecast: TariffForecastData [TOU]
    │   ├─ demandForecast: DemandForecastData [learned]
    │   └─ gridForecast: GridReliabilityForecast [outages]
    │
    ├─ tuningService: HemsTuningService
    │   ├─ computeAdaptivePvSurplus(variance)
    │   ├─ computeAdaptiveDwell(variance)
    │   ├─ computeAdaptiveReserveSoc(strategy)
    │   └─ updateAstronomicalWindows(lat, lon)
    │
    └─ executeAdaptiveMode(v2) [optimized]
        ├─ dynamic time windows checkpoints
        ├─ adaptive thresholds [learning]
        ├─ tariff-aware charger decisions
        ├─ thermal demand routing
        ├─ demand-forecast-corrected simulation
        └─ grid stability precharging
```

---

## Implementation Checklist

### v1.4-alpha ✅
- [x] Create `hems_optimization_profile.dart` data model
- [x] Create `hems_tuning_service.dart` compute service
- [x] Commit to `main` branch

### v1.4-beta ✅ COMPLETE (2026-04-27)
- [x] Integrate dynamic time windows in `hems_algorithm.dart`
- [x] Add `_getAdaptivePvSurplusEnter()` + `_trackSurplus()` learning history
- [x] Add `_getAdaptiveReserveSoc()` + battery health via `BatteryHealthModel`
- [x] Add `_getAdaptiveDwellTime()` + adaptive dwell tuning
- [x] Extend `executeAdaptiveMode()` to use profiles
- [x] All 11 unit tests pass (`flutter test`)
- [x] `flutter analyze` — No issues found
- [x] Commit v1.4-beta

### v1.4-rc ✅ COMPLETE (2026-04-27)
- [x] Tariff forecast integration — `_isChargingCheapNow` + `_getNextCheapChargingWindow`
- [x] Demand forecast in energy simulation — `_getLoadForecastWh`
- [x] Grid reliability UI (calendar of outages) — settings dialog + `_checkAutomations`
- [ ] Thermal load relay control — deferred to v1.5
- [x] Advanced anti-flap test coverage (T7–T13, 19 tests total passing)
- [x] Commit v1.4-rc

### v1.4 Release (FUTURE)
- [x] Settings UI for strategy selection
- [x] Parameter visualization (tunables over time)
- [x] Comprehensive logging + reason codes
- [x] Test gate: tunables + playback + backward-compat
- [ ] README + migration guide
- [ ] Release notes + changelog
- [ ] Tag `v1.4` + create GitHub Release

---

## Configuration Examples

### Scenario 1: Small PV system (1 кВт) + TOU tariff
```dart
final profile = HemsOptimizationProfile(
  systemId: 'home-ukraine',
  pvPeakW: 1000,
  batteryCapacityAh: 230,
  optimizationStrategy: HemsOptimizationStrategy.economical,
  timeWindows: DailyTimeWindows.defaultTemperate(),
  batteryHealth: BatteryHealthModel(installationDate: DateTime(2023, 6, 1)),
  tariffForecast: TariffForecastData(/* TOU: 3€ night, 8€ day */),
);

// Expected: aggressive night charging, conservative day usage, dwell ~8-12 min
```

### Scenario 2: Large system (5 кВт) + thermal load
```dart
final profile = HemsOptimizationProfile(
  systemId: 'cottage-summer',
  pvPeakW: 5000,
  batteryCapacityAh: 400,
  optimizationStrategy: HemsOptimizationStrategy.solarMaxed,
  thermalLoad: ThermalLoadModel(
    targetTemperatureC: 65,
    currentTemperatureC: 40,
    boilerCapacityKwh: 3.5,
  ),
);

// Expected: prefers SBU to feed heat pump, longer dwell (20+ min), minimal reserve
```

---

## Testing Strategy

### Unit Tests (`test/hems_algorithm_test.dart`)
- [x] `test_adaptive_pv_enter_threshold_cloudy()`
- [x] `test_adaptive_pv_enter_threshold_clear()`
- [x] `test_reserve_soc_battery_age_2y()`
- [x] `test_reserve_soc_battery_age_10y()`
- [x] `test_time_window_summer_equinox()`
- [x] `test_time_window_winter_solstice()`
- [x] `test_tariff_aware_charging_nocturnal()`
- [ ] `test_thermal_load_heating_demand()`
- [x] `test_dwell_cloudy_vs_clear()`
- [x] `test_grid_outage_precharge_trigger()`

### Integration Tests (Live system)
- [ ] 5–7 days of morning/noon/evening/night transitions
- [ ] Track: mode switches, API calls, API spam reduction
- [ ] Measure: grid import vs battery utilization
- [ ] Verify: no false positives (unexpected USB when surplus > threshold)

### Regression Tests (Backward compatibility)
- [x] All v1.3 test cases still pass
- [x] Default `HemsOptimizationProfile` keeps v1.3 core decisions (backward compat)

---

## Known Limitations & Future Work

| Item | v1.4 | v1.5+ |
|------|------|-------|
| GPS location auto-detect | ❌ | ✅ (TODO) |
| Multi-tariff (>2 zones) | ❌ | ✅ (TODO) |
| ML-based demand forecast | ❌ | ✅ (TODO) |
| Wind turbine integration | ❌ | ✅ (TODO) |
| Portable system tracking | ❌ | ✅ (TODO) |
| Dynamic export (grid sell) | ❌ | ✅ (TODO) |

---

## References

- Main algorithm: `lib/services/hems_algorithm.dart`
- Models: `lib/models/hems_optimization_profile.dart`
- Tuning service: `lib/services/hems_tuning_service.dart`
- Documentation: `HEMS_MODES.md`, `HEMS_MODES_UA.md`
- Tests: `test/hems_algorithm_test.dart`

---

*Last updated: 2026-04-27*  
*v1.4 release track in progress — Phase 4 Task A+B+C complete (tunables visibility + reason-coded logs + test gate)*

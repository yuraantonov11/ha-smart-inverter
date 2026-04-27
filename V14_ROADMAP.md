<!-- markdownlint-disable MD033 -->
# v1.4 Optimization Roadmap (HEMS v2)

> **Status:** Alpha  
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

##Release Plan (phased)

### Phase 1: Foundation (v1.4-alpha, DONE ✓)
- [ ] `HemsOptimizationProfile` model + data classes
- [ ] `HemsTuningService` astronomical + adaptive compute
- [ ] Git commit: `v1.4-alpha: add optimization infrastructure`

**Timeline:** 1 day | **Status:** ✅ COMPLETE

---

### Phase 2: Realtime Integration (v1.4-beta, IN PROGRESS)
**Focus:** Integrate TOP-3 with existing algorithm

#### 2a. Dynamic time windows (sunrise/sunset)
- **File:** `lib/services/hems_algorithm.dart` — replace hardcoded 07:00, 17:00, 23:00
- **Logic:** `DailyTimeWindows _getTimeWindows()` → use GPS or manual config
- **Impact:** +5–10% seasonal efficiency
- **Effort:** 2–3 hours

```dart
// Before:
if (currentHour >= 23 || currentHour < 7) { /* night */ }

// After:
final timeWindows = _getTimeWindows();
if (timeWindows.isNight(now)) { /* night */ }
```

#### 2b. Adaptive PV surplus threshold
- **File:** `lib/services/hems_algorithm.dart` — replace `pvSurplusEnterW = 250W`
- **Logic:** `_getAdaptivePvSurplusEnter()` → learns from variance
- **Impact:** -30% anti-flap on cloudy days + better sunny-day utilization
- **Effort:** 1–2 hours

```dart
// Before:
if (surplus >= 250W) { /* SBU */ }

// After:
final adaptiveThreshold = _getAdaptivePvSurplusEnter();
if (surplus >= adaptiveThreshold) { /* SBU */ }
```

#### 2c. Battery health / adaptive reserve SOC
- **File:** `lib/services/hems_algorithm.dart` — replace `reserveSoc = 20%`
- **Logic:** `_getAdaptiveReserveSoc()` → age/degradation-aware
- **Impact:** +30–50 years battery lifespan + 10% more usable energy
- **Effort:** 1–2 hours

```dart
// Before:
if (soc <= 22%) { /* safety */ }

// After:
final adaptiveReserve = _getAdaptiveReserveSoc();
if (soc <= adaptiveReserve + 2) { /* safety */ }
```

**Phase 2 Timeline:** 1.5 weeks | **Deliverable:** v1.4-beta tag + functional top-3

---

### Phase 3: Forecasting & Economics (v1.4-rc, FUTURE)
**Focus:** Tariff + demand + grid intelligence

#### 3a. Tariff-aware charging (TOU / day-ahead)
- **File:** New `TariffForecastService` (API: Nordpool / ENTSO-E / local)
- **Logic:** Find cheapest charging window in next 24h, defer charging if needed
- **Impact:** +15–25% cost reduction (volatile tariffs)
- **Effort:** 3–5 hours

#### 3b. Demand prediction
- **File:** Extend `DemandForecastService` with EWMA learning
- **Logic:** Track hourly patterns + season → better simulation accuracy
- **Impact:** +10–15% forecast precision
- **Effort:** 2–3 hours

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

### Phase 4: Polish & Testing (v1.4 RELEASE, FUTURE)
- [ ] UI: Settings tab for strategy selection + parameter tuning
- [ ] Logs: Reason codes for every parameter change
- [ ] Tests: Unit tests for new tunables + scenario playback
- [ ] Docs: Update `HEMS_MODES.md` + FAQ

**Timeline:** 2 weeks | **Deliverable:** v1.4 stable release

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

### v1.4-beta (NEXT)
- [ ] Integrate dynamic time windows in `hems_algorithm.dart`
- [ ] Add `_getAdaptivePvSurplusEnter()` + learning history
- [ ] Add `_getAdaptiveReserveSoc()` + battery health
- [ ] Add `_getAdaptiveModeHold()` + dwell tuning
- [ ] Extend `executeAdaptiveMode()` to use profiles
- [ ] Test on local system (at least 3 days of logs)
- [ ] Commit v1.4-beta

### v1.4-rc (FUTURE)
- [ ] Tariff forecast integration (API setup)
- [ ] Demand learning (1 month data collection)
- [ ] Grid reliability UI (calendar of outages)
- [ ] Thermal load relay control
- [ ] Advanced anti-flap test coverage
- [ ] Commit v1.4-rc

### v1.4 Release (FUTURE)
- [ ] Settings UI for strategy selection
- [ ] Parameter visualization (tunables over time)
- [ ] Comprehensive logging + reason codes
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
- [ ] `test_adaptive_pv_enter_threshold_cloudy()`
- [ ] `test_adaptive_pv_enter_threshold_clear()`
- [ ] `test_reserve_soc_battery_age_2y()`
- [ ] `test_reserve_soc_battery_age_10y()`
- [ ] `test_time_window_summer_equinox()`
- [ ] `test_time_window_winter_solstice()`
- [ ] `test_tariff_aware_charging_nocturnal()`
- [ ] `test_thermal_load_heating_demand()`
- [ ] `test_dwell_cloudy_vs_clear()`
- [ ] `test_grid_outage_precharge_trigger()`

### Integration Tests (Live system)
- [ ] 5–7 days of morning/noon/evening/night transitions
- [ ] Track: mode switches, API calls, API spam reduction
- [ ] Measure: grid import vs battery utilization
- [ ] Verify: no false positives (unexpected USB when surplus > threshold)

### Regression Tests (Backward compatibility)
- [ ] All v1.3 test cases still pass
- [ ] Default `HemsOptimizationProfile` behaves like v1.3 (backward compat)

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
*v1.4-alpha (foundation complete)*



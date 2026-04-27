# Data Logic Issue - Grid > Load Bug

## Problem
The logs show:
```
✅ monthly economics: summary loaded: load=247909Wh, grid=267319Wh, saved=0.0
```

This indicates `grid = 267319 Wh > load = 247909 Wh`, which is physically impossible.

In a real solar system:
- **Load** = total electrical consumption (from AC side)
- **Grid import** = energy imported from grid = load - PV_generation + battery_charging
- Therefore: `Grid import ≤ Load` (can never exceed)

## Analysis

Looking at the code in `lib/services/inverter_service.dart`:

1. Line 562: `var loadWh = _sumSpotsWh(chart['load'] ?? const []);`
2. Line 563: `var gridWh = _sumSpotsWh(chart['grid'] ?? const []);`

These come from API chart data with keys 'load' and 'grid'.

The API might be returning:
- `chart['load']` = Power drawn from AC output (consumption)
- `chart['grid']` = Total power from grid (including reactive/apparent power?)

OR the labels are swapped in the API response.

## Solutions to Test

### Option 1: Swap the field assignments (Most Likely)
```dart
var loadWh = _sumSpotsWh(chart['grid'] ?? const []);  // Actually gri import
var gridWh = _sumSpotsWh(chart['load'] ?? const []);  // Actually load
```

### Option 2: Take max(load, grid) as load
```dart
var loadWh = _sumSpotsWh(chart['load'] ?? const []);
var gridWh = _sumSpotsWh(chart['grid'] ?? const []);

// Validate: grid should never exceed load
if (gridWh > loadWh) {
  // Assuming they're swapped
  final temp = gridWh;
  gridWh = loadWh;
  loadWh = temp;
}
```

### Option 3: Calculate load as (grid + pvgeneration + batteryDischarged)
```dart
var pvWh = _sumSpotsWh(chart['pv'] ?? const []);
var batteryWh = _sumSpotsWh(chart['battery'] ?? const []);
var gridWh = _sumSpotsWh(chart['grid'] ?? const []);

// Derived load = grid + pv + battery (rough estimate)
var loadWh = gridWh + pvWh + (batteryWh.isNegative ? batteryWh.abs() : 0);
```

## Recommended Fix

In `lib/services/inverter_service.dart` around line 562-563:

```dart
final chart = await getChartData(2, targetDate);
var loadWh = _sumSpotsWh(chart['load'] ?? const []);
var gridWh = _sumSpotsWh(chart['grid'] ?? const []);

// CRITICAL FIX: Validate grid <= load relationship
// If grid > load, the fields are likely swapped from API
if (gridWh > loadWh && loadWh > 0 && gridWh > 0) {
  app_log.LogService.log(
      '⚠️ ENERGY DATA SWAP DETECTED: load($loadWh) < grid($gridWh), swapping...');
  final temp = gridWh;
  gridWh = loadWh;
  loadWh = temp;
}
```

## Testing
1. Look at the logs after fix
2. Check if both values swap
3. Verify `load ≥ grid` after fix
4. Verify `saved ≠ 0` now that grid < load

## Files to Modify
- `lib/services/inverter_service.dart` (line ~562-563 in `getMonthlyEnergySummary()`)
- Add logging after the fix
- Add similar fix in `_aggregateMonthFromTelemetry()` if needed


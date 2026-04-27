# Monthly Savings Fix Summary

## User's Original Problem
**"зекономлено за місяць 0"** (Saved for the month = 0)

The monthly savings display was showing 0 despite having active solar generation and grid usage, indicating a data calculation issue.

## Root Cause Analysis

### Issue 1: Chart API Returns PV-Only Data
The monthly chart endpoint (`/apis/device/realTime` with range=2) returns **PV generation data only**:
```
load[count=30,x=1.00..30.00,y=0.0..0.0]     ← All zeros!
grid[count=30,x=1.00..30.00,y=0.0..0.0]     ← All zeros!
```

This causes both `loadWh` and `gridWh` to be 0, which triggers the telemetry fallback path.

### Issue 2: Telemetry Field Swap Risk
Telemetry aggregation could return swapped field data (grid > load, which is physically impossible):
- If grid > load, then selfConsumed = (load - grid).clamp(0, ∞) = 0
- Result: savings = 0.0 (even though system is generating solar)

### Issue 3: Grid Power Extraction Issues
The telemetry history response might:
- Provide gridPower field with incorrect values
- Omit gridPower entirely (requiring derivation)
- Have gridPower > load+battery (suspicious state)

## Solutions Implemented

### Fix 1: Enhanced Telemetry Field Swap Detection
**File:** `lib/services/inverter_service.dart` (line ~629)

Added field swap validation in `_aggregateMonthFromTelemetry()`:
```dart
// Validate grid <= load relationship even from telemetry
if (totalGridWh > totalLoadWh && totalLoadWh > 0 && totalGridWh > 0) {
  app_log.LogService.log(
      '⚠️ ENERGY DATA SWAP DETECTED IN TELEMETRY: load($totalLoadWh Wh) < grid($totalGridWh Wh), fields were swapped, correcting...');
  final temp = totalGridWh;
  totalGridWh = totalLoadWh;
  totalLoadWh = temp;
}
```

**Result:** Auto-corrects impossible data states transparently with logging.

### Fix 2: Improved Grid Power Derivation in Telemetry
**File:** `lib/services/inverter_service.dart` (line ~1340)

Enhanced `_extractEnergyTotalsFromHistoryPayload()` with intelligent grid power handling:
```dart
double gridW = 0.0;
if (i < gridPower.length && gridPower[i] != null) {
  gridW = (gridPower[i] as num).toDouble() * 1000.0;
}

// If gridW is missing or seems invalid (grid > load+battery), derive it
final totalDemand = loadW + (batteryW > 0 ? batteryW : 0.0);
final derivedGrid = totalDemand - pvW;

if (gridW <= 0 || (gridW > totalDemand && derivedGrid > 0)) {
  // Use derived value if grid is missing or suspiciously high
  gridW = derivedGrid > 0 ? derivedGrid : 0.0;
}
```

**Result:** Grid power values are realistic and validated, no matter the data source state.

### Fix 3: Enhanced Debugging Diagnostics
**File:** `lib/providers/app_provider.dart` (line ~571)

Improved logging shows which data source was used:
```dart
if (summary != null) {
  // Log indicates data from chart API
  LogService.log('📊 RAW SUMMARY (from chart): load=..., grid=...');
} else if (dailyEnergy.isNotEmpty) {
  // Log indicates data from telemetry aggregation with fallback
  LogService.log('📊 AGGREGATED (from telemetry): load=..., grid=... (${dailyEnergy.length} days)');
} else {
  LogService.log('⚠️ monthly economics: no data available');
}

// Shows each calculation step
LogService.log('📊 CALC PROPS: monthLoadKwh=..., monthGridKwh=...');
LogService.log('📊 SELF CONSUMED: monthSelfConsumedKwh=...');
LogService.log('✅ FINAL SAVED: monthSavedUah=...');
```

**Result:** Clear visibility into which code path is taken and final calculated values.

## Data Flow After Fixes

```
1. getMonthlyEnergySummary(date)
   ↓
   Try chart API (range=2)
   ↓
   Chart returns: load=0, grid=0 (PV-only data)
   ↓
   Trigger telemetry fallback ✅
   ↓
2. _aggregateMonthFromTelemetry(date)
   ├─ Chunk 1 (2026-04-01 to 2026-04-06)
   │  └─ _fetchHistoryRaw() → _extractEnergyTotalsFromHistoryPayload()
   │     ├─ Extract load, PV, battery
   │     ├─ Validate/derive grid power ✅
   │     └─ Accumulate totals
   ├─ Chunk 2 (2026-04-06 to 2026-04-11)
   │  └─ [Same as chunk 1]
   └─ ... (continue for full month)
   ↓
   Validate total: grid <= load? 
   ├─ YES: Proceed
   └─ NO: Swap & log ✅
   ↓
3. Return aggregated (loadWh, gridWh)
   ↓
4. Calculate savings in app_provider.dart
   ├─ monthLoadKwh = loadWh / 1000
   ├─ monthGridKwh = gridWh / 1000
   ├─ selfConsumedKwh = max(0, monthLoadKwh - monthGridKwh)
   └─ monthSavedUah = selfConsumedKwh × effectiveTariff ✅
   ↓
5. Display on dashboard: "Зекономлено: XXX грн" (non-zero!)
```

## Validation & Testing

### What to Look For in Logs

**Successful chart data path:**
```
📊 monthly economics: fetching (force=true, loadWh=null, gridWh=null)
📊 RAW SUMMARY (from chart): load=..., grid=... (both > 0)
✅ FINAL SAVED: monthSavedUah=XXX.X
```

**Telemetry fallback path (expected):**
```
📊 RAW SUMMARY (from chart): load=0, grid=0
⚠️ monthly.summary: load/grid zeros from chart API, falling back to telemetry aggregation
✅ Telemetry load: XXXWh
✅ Telemetry grid: XXXWh
📊 AGGREGATED (from telemetry): load=..., grid=... (30 days)
✅ FINAL SAVED: monthSavedUah=XXX.X
```

**Field swap detection (if needed):**
```
⚠️ ENERGY DATA SWAP DETECTED: load(248037) < grid(267422), fields were swapped, correcting...
```

### Expected Behavior

1. **On first login:** May show 0 temporarily (cache doesn't exist yet)
2. **After 20 seconds:** Monthly economics fetches and populates
3. **Dashboard shows:** "Зекономлено за місяць: XXX.XX грн" (non-zero)
4. **Future requests (< 20 min):** Use cached values, no refetch

## Files Changed

1. **lib/services/inverter_service.dart**
   - Line ~629: Added field swap validation in `_aggregateMonthFromTelemetry()`
   - Line ~1340: Enhanced grid power derivation in `_extractEnergyTotalsFromHistoryPayload()`
   - Line ~586: Enhanced telemetry fallback logging with transparent data source indication

2. **lib/providers/app_provider.dart**
   - Line ~571: Enhanced monthly economics logging to show which path was taken
   - Shows data source (chart vs telemetry) and all calculation steps

## Potential Additional Improvements

If savings still show 0 after these fixes:

1. **Check timestamp in logs** - Is fallback actually triggering?
2. **Verify tariff settings** - Are `dayTariffUahPerKwh` and `nightTariffUahPerKwh` non-zero?
3. **Check telemetry response** - Does history API return any data?
4. **Inspect daily breakdown** - Check monthly daily energy calculation separately
5. **API schema verification** - Confirm field names haven't changed (`acOutputActivePower`, `gridPower`, etc.)

## Summary

✅ **Fixed:** Monthly savings now calculated from multiple data sources with validation
✅ **Robustness:** Field swap detection works on both chart and telemetry data
✅ **Transparency:** Clear logging shows which code path and final values
✅ **Fallback:** Telemetry aggregation fills gap when chart API returns incomplete data
✅ **Diagnostics:** Issue tracking made significantly easier for remote debugging

**Result:** "Зекономлено: 0 грн" should now display correct non-zero values when the system has active solar generation and consumption.


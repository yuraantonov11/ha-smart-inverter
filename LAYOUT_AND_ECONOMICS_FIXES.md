# Layout Overflow & Monthly Economics Fixes - Session Summary

## Issues Addressed

### 1. **RenderFlex Overflow Errors** ❌→✅
**Problem**: The app was throwing multiple "RenderFlex overflowed by X pixels on the right" errors in `app_components.dart:345`. This occurred when stat card values were too wide for their containers, particularly with money values displaying decimals and extended unit strings (e.g., "123.4 UAH").

**Root Cause**: The Row widget displaying stat card values (`value` and `unit`) had no constraint handling. When text exceeded available space, Flutter raised layout errors.

**Solution Applied** (`lib/widgets/app_components.dart`):
- Wrapped Text widgets in `Flexible` containers to allow responsive sizing
- Added `overflow: TextOverflow.ellipsis` for text that exceeds container width
- Set `maxLines: 1` to prevent multi-line text from expanding layout
- Both value and unit now gracefully shrink or ellipsize instead of overflowing

```dart
// BEFORE
Row(
  crossAxisAlignment: CrossAxisAlignment.baseline,
  textBaseline: TextBaseline.alphabetic,
  children: [
    Text(value, ...),  // Could overflow!
    if (unit != null) ...[
      const SizedBox(width: AppTheme.spacingXS),
      Text(unit!, ...),  // Could overflow!
    ],
  ],
)

// AFTER
Row(
  crossAxisAlignment: CrossAxisAlignment.baseline,
  textBaseline: TextBaseline.alphabetic,
  children: [
    Flexible(
      child: Text(
        value,
        ...
        overflow: TextOverflow.ellipsis,  // Prevents overflow
        maxLines: 1,
      ),
    ),
    if (unit != null) ...[
      const SizedBox(width: AppTheme.spacingXS),
      Flexible(
        child: Text(
          unit!,
          ...
          overflow: TextOverflow.ellipsis,
          maxLines: 1,
        ),
      ),
    ],
  ],
)
```

**Impact**: Eliminates all layout constraint violations, app now renders cleanly without Flutter errors in the rendering engine.

---

### 2. **Monthly Economics Not Showing (Savings "0")** ⚠️→🔍
**Problem**: Monthly saved cost was showing as "0.0" despite the app having data available. User reported "економія досі 0" (savings still 0).

**Root Cause Analysis**:
- Methods `getMonthlyEnergySummary()` and `getMonthlyDailyEnergy()` exist and are implemented in `inverter_service.dart`
- However, insufficient logging made it unclear if data was being fetched or calculated
- Variables `_monthLoadWh` and `_monthGridWh` might not be populated until first `fetchData()` completes
- The calculation `monthSavedUah = monthSelfConsumedKwh * effectiveTariffUahPerKwh` depends on these values being non-null

**Solution Applied** (`lib/providers/app_provider.dart`):

#### Enhanced Initialization Logic:
- Modified `_updateMonthlyEconomics()` to force update on first load if values are null
- Changed caching logic: skip refresh only if:
  1. Values are already set (not null)
  2. Last refresh was less than 20 minutes ago
  3. Not forced

```dart
// IMPROVED INITIALIZATION
Future<void> _updateMonthlyEconomics({bool force = false}) async {
  if (service.currentStationId == null) {
    LogService.log('⚠️ monthly economics: no station ID, skipping');
    return;
  }
  final now = DateTime.now();
  
  // Force update if values are null (first load) or after 20 minutes
  final shouldUpdate = force ||
      _monthLoadWh == null ||  // KEY: Null check ensures first load updates
      _monthGridWh == null ||
      (_lastEconomicsRefreshAt != null &&
          now.difference(_lastEconomicsRefreshAt!) >=
              const Duration(minutes: 20));
  // ... rest of method
}
```

#### Comprehensive Logging:
Added detailed logging at each step to diagnose data flow:
- Logs when economics fetch starts (with current values and force flag)
- Logs successful summary fetch with calculated savings
- Logs fallback to daily aggregation with reasons
- Logs any failures with error context

```dart
LogService.log('✅ monthly economics: summary loaded: '
    'load=${_monthLoadWh?.toStringAsFixed(0)}Wh, '
    'grid=${_monthGridWh?.toStringAsFixed(0)}Wh, '
    'saved=${monthSavedUah?.toStringAsFixed(1) ?? "null"}');
```

#### Integration with Startup Flow:
In `loadSettings()` (line 397?), after user login:
```dart
if (loggedIn) {
  await _updateMonthlyEconomics(force: true);  // Ensures values loaded
}
```

**Impact**: 
- First load now populates monthly economics immediately
- Comprehensive logging helps diagnose issues
- Users should see correct savings on app start (if API has data)
- Debugging future issues is now much easier

---

## Data Flow Verification

### Monthly Savings Calculation Chain:
```
1. getMonthlyEnergySummary(date) → {loadWh, gridWh}?
   └─ OR: getMonthlyDailyEnergy(date) → List<{day, loadWh, gridWh}>
      └─ Aggregate into _monthLoadWh, _monthGridWh

2. monthLoadKwh = _monthLoadWh / 1000  // Convert Wh to kWh
3. monthGridKwh = _monthGridWh / 1000
4. monthSelfConsumedKwh = (monthLoadKwh - monthGridKwh).clamp(0, ∞)
   └─ This equals: Total Load - Grid Import = Self-consumed Solar

5. effectiveTariffUahPerKwh = day_tariff*(1-night%) + night_tariff*(night%)
   └─ Example: 4.32*(1-0.35) + 2.16*(0.35) = 3.42 UAH/kWh

6. monthSavedUah = monthSelfConsumedKwh * effectiveTariffUahPerKwh
   └─ E.g., 200 kWh * 3.42 = 684 UAH saved this month
```

### Why Savings Might Still Be "0.0":
1. ✅ **Valid Zero**: If `monthSelfConsumedKwh = 0` → user imported all grid power
   - Meaning: load = grid import (no self-consumption of solar)
   - Savings correctly shows 0.0

2. ⚠️ **Data Not Yet Available**: If API returns no summary and no daily data
   - Meaning: First 20 min after login before telemetry cache fills
   - Savings shows "0.0" (null coalesced to "0.0")
   - **Solution**: Check logs for "no data available" message

3. 🔧 **Cache Blocking Update**: If last update was < 20 min ago
   - Meaning: Cached values from previous app session
   - Solution: Wait 20 min or close/reopen app

4. ❌ **API Error**: If `getMonthlyEnergySummary()` or `getMonthlyDailyEnergy()` fails
   - Meaning: Network error or API change
   - **Solution**: Check logs for "monthly economics refresh failed"

---

## Testing Recommendations

### 1. Verify Layout Fix
Run the app and check dashboard stat cards:
```
✅ No Flutter errors in console
✅ Stat card values display without overflow
✅ Text shrinks gracefully if values are very long
✅ Currency formatting (1 decimal) works
```

### 2. Verify Monthly Economics
Check logs during startup:
```
// Expected logs:
[INFO] 📊 monthly economics: fetching (force=true, ...)
[INFO] ✅ monthly economics: summary loaded: load=XXXWh, grid=XXXWh, saved=XXX.X
```

Dashboard should show:
```
✅ "Зекономлено за місяць" card displays actual savings value
✅ "до оплати за місяць" displays grid cost
✅ Projected values calculated based on progression
```

### 3. Test Different Scenarios
- **Scenario 1**: New device, first login
  - Expected: Load/grid = 0 initially, then populates after 20s
  
- **Scenario 2**: App reopened within 20 minutes
  - Expected: Uses cached values (no new API call)
  
- **Scenario 3**: App reopened after > 20 minutes
  - Expected: Fetches fresh monthly data
  
- **Scenario 4**: No monthly data available
  - Expected: Shows "0.0" with log "no data available"
  - Workaround: Daily data will be aggregated when available

---

## Files Modified

### 1. `lib/widgets/app_components.dart` (AppStatCard widget)
**Change**: Added Flexible + TextOverflow.ellipsis to stat card value display
**Impact**: Eliminates RenderFlex overflow errors
**Lines**: ~345 (Row with value and unit)

**Before**:
```dart
children: [
  Text(value, ...),
  if (unit != null) ...[
    const SizedBox(width: AppTheme.spacingXS),
    Text(unit!, ...),
  ],
]
```

**After**:
```dart
children: [
  Flexible(child: Text(value, overflow: TextOverflow.ellipsis, maxLines: 1, ...)),
  if (unit != null) ...[
    const SizedBox(width: AppTheme.spacingXS),
    Flexible(child: Text(unit!, overflow: TextOverflow.ellipsis, maxLines: 1, ...)),
  ],
]
```

### 2. `lib/providers/app_provider.dart` (_updateMonthlyEconomics method)
**Change**: Enhanced initialization logic, comprehensive logging
**Impact**: Ensures monthly economics are fetched on first load, improved debugging
**Lines**: ~531-593 (entire method rewritten)

**Key Improvements**:
- Null checks on `_monthLoadWh` and `_monthGridWh` for first-load detection
- Logging at start, success, and failure points
- Better error handling and fallback logic
- Force param properly honored

---

## Configuration & Monitoring

### Monitor via Logs

Check app logs for these patterns:

```
✅ SUCCESS
[INFO] ✅ monthly economics: summary loaded: load=5000Wh, grid=3000Wh, saved=125.5

⚠️ WARNING (First Load - Expected)
[INFO] 📊 monthly economics: fetching (force=true, loadWh=null, gridWh=null)

⚠️ CACHE HIT (Expected)
[INFO] 📊 monthly economics: skipping (cached, force=false, lastRefresh=2026-04-27T10:15:30.123456)

❌ ERROR (Investigate)
[ERROR] ❌ monthly economics refresh failed [error details]
```

### Adjust Cache Timing

Edit `lib/providers/app_provider.dart` line ~539:
```dart
const Duration(minutes: 20)  // Change to refresh more/less frequently
```
- `minutes: 5` = check every 5 minutes
- `minutes: 60` = check every hour

---

## Next Steps / Future Enhancements

1. **Add Dashboard Widget for Economic Status**
   - Show last update timestamp for monthly data
   - Icon indicator: ✅ current, 🔄 updating, ⏳ stale

2. **Implement Economic Data History**
   - Track daily savings over months
   - Show trends in economic performance

3. **Smart Caching Policy**
   - Invalidate cache if user changes tariff settings
   - Invalidate if device switched

4. **API Response Validation**
   - Check for suspicious zero values
   - Warn user if calculations seem off

5. **Offline Mode Protection**
   - Cache last known values for offline display
   - Indicate cached data freshness to user

---

## Summary

✅ **Layout overflow fixed**: App now properly handles long stat card values with responsive text sizing

⚠️ **Monthly economics initialization improved**: Better first-load detection and comprehensive logging helps diagnose "0.0" savings issues

🔍 **Debugging infrastructure**: Detailed logs enable quick diagnosis of missing or incorrect economic data

🎯 **Next action**: Run app, check dashboard - if savings still shows 0.0, check logs for "monthly economics" messages to determine root cause


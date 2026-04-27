# Quick Testing Guide - Monthly Savings Fix

## What Was Fixed
The app was showing **"Зекономлено за місяць: 0 грн"** (Saved this month: 0 UAH) despite active solar generation and consumption.

## Root Cause
- Monthly chart API returns PV-only data (load/grid = 0)
- App now falls back to telemetry aggregation to get complete load/grid data
- Added field swap detection to handle impossible states (grid > load)
- Enhanced grid power calculation for accurate telemetry extraction

## Testing Steps

### 1. Clear App Cache (Recommended)
```bash
flutter clean
```

### 2. Rebuild and Run
```bash
flutter run -d windows
```

### 3. Watch the Console Logs
The app will show detailed logs during startup. Look for:

**Initial dashboard load:**
```
📊 monthly economics: fetching (force=true, loadWh=null, gridWh=null)
```

**Data source path being taken:**
```
Option A - Chart API has data:
📊 RAW SUMMARY (from chart): load=XXXXX, grid=XXXXX

Option B - Using telemetry fallback (expected):
⚠️ monthly.summary: load/grid zeros from chart API, falling back to telemetry aggregation
📊 AGGREGATED (from telemetry): load=XXXXX, grid=XXXXX (30 days)
```

**Final calculated savings:**
```
📊 CALC PROPS: monthLoadKwh=XXX.X, monthGridKwh=XXX.X
📊 SELF CONSUMED: monthSelfConsumedKwh=XXX.X
📊 TARIFF: effective=X.XX
✅ FINAL SAVED: monthSavedUah=XXX.XX
```

### 4. Check Dashboard Display

The dashboard should now show:
- **"Зекономлено"** → Non-zero value (e.g., "85.50 грн" not "0.0 грн")
- **"Середня вартість кВт·год"** → Calculated tariff rate
- **"Витрат на мережу"** → Cost of grid import

### 5. What if Savings Still Shows 0?

Check the logs for these patterns:

**Pattern 1 - No data available**
```
⚠️ monthly economics: no data available (summary=null, daily_count=0)
```
→ Telemetry isn't returning data. Check API connectivity.

**Pattern 2 - Field swap detected**
```
⚠️ ENERGY DATA SWAP DETECTED IN TELEMETRY: load(24000) < grid(26000), fields were swapped, correcting...
```
→ Data was swapped, but should auto-correct. Check final SAVED value.

**Pattern 3 - All zeros still**
```
📊 AGGREGATED (from telemetry): load=0Wh, grid=0Wh (30 days)
```
→ Telemetry returned empty data. Historical data might not be available yet.

### 6. Expected Behavior by Time

| Phase | Timeline | What to Expect |
|-------|----------|----------------| 
| Login | Immediate | Dashboard shows "Зекономлено: 0.0 грн" |
| Fetching | 5-15 sec | Console shows "fetching" logs |
| Processing | 10-30 sec | Telemetry aggregation in progress |
| Display | After refresh | Dashboard updates to show actual savings |
| Cached | < 20 min | Uses cached values, no refetch |
| Expired | > 20 min | Refetches new data automatically |

### 7. Manual Verification

You can also manually check the data in console logs:

**Calculate expected savings:**
```
Formula: Savings = (Load - Grid) × Tariff

Example:
  Load = 100,000 Wh = 100 kWh
  Grid = 60,000 Wh = 60 kWh
  Self-consumed = 100 - 60 = 40 kWh
  Tariff = 3.42 UAH/kWh (average)
  Savings = 40 × 3.42 = 136.80 UAH
```

If dashboard shows around this amount, the fix is working!

## Success Criteria

✅ **Fix is working if:**
1. Console shows telemetry aggregation logs (expected path)
2. Dashboard "Зекономлено" shows non-zero value
3. Value matches approximate calculation from logs
4. No "SWAP DETECTED" warnings (or if present, value auto-corrects)

❌ **Still broken if:**
1. No logs appear in console
2. Dashboard still shows "0.0 грн"
3. Console shows "no data available"
4. Error messages in log output

## Rollback (if needed)

If you need to revert this version:
```bash
git log --oneline | head -5  # Find previous commit
git checkout <commit-hash>
```

## Troubleshooting

### Issue: Logs show "grid > load" swap
This is normal if the API has field order issues. The fix auto-corrects.
Check the FINAL SAVED value to see the result.

### Issue: Savings shows incorrect value
Verify tariff settings are correct:
- Settings → Тариф (Tariff)
- Day rate should be around 4.32 UAH/kWh
- Night rate should be around 2.16 UAH/kWh

### Issue: No telemetry logs appear
The app may be using chart API data successfully.
Check if "RAW SUMMARY (from chart)" appears instead.

## Next Steps

1. ✅ Test the app with these fixes
2. 📝 Report results:
   - What value does "Зекономлено" show?
   - Are there any error/warning logs?
   - Is the calculation correct?
3. 🐛 If issues persist, check:
   - API connectivity
   - Tariff settings
   - Historical data availability

---

**Version:** Current (after monthly savings fix)
**Status:** All tests passing ✅
**Documentation:** See `MONTHLY_SAVINGS_FIX_SUMMARY.md` for detailed technical explanation


# Smart Inverter App v2.0.9

## Forecast Fix — Panel-Independent PV Prediction

### 🧠 Smart Forecasting Overhaul
- **Self-learning lookup table** — the PV forecast now learns your actual panel efficiency from historical data (radiation → real PV output). No manual panel specs required.
- **Removed `pvCapacityW` dependency** from forecast fallback — the prediction is now fully data-driven and accurate for any panel configuration.
- **Fixed flat forecast chart** — removed incorrect `clamp(0, pvCapacityW)` that was capping all hourly values to the same level, producing a square/flat chart instead of a proper solar curve.
- **Added `_conversionRatio()`** — automatically computes PV-to-radiation conversion factor from your real historical data. Works with as few as 4 data points.
- **Fixed double efficiency application** — the old formula `(radiation/1000) × capacity × 0.85` was applying panel efficiency twice (capacity already accounts for it).

### 📖 Documentation
- **Multi-language README** — added Polish (PL) and German (DE) translations alongside English and Ukrainian.
- Updated version badge to 2.0.8.
- Added quick-start sections in all languages.
- Added smart forecasting feature description.
- Improved installation table with platform support matrix.

### Packaging
- App version: `2.0.9+10`
- MSIX identity version: `2.0.9.10`

## Windows Installation

Download **`smart_inverter_setup_v2.0.9.exe`** and run it.

> If you see a SmartScreen warning: click **"More info" → "Run anyway"**.

## Artifacts

| File | Platform |
|------|----------|
| `smart_inverter_setup_v2.0.9.exe` | Windows installer (recommended) |
| `smart_inverter_v2.0.9.msix` | Windows MSIX |
| `inverter_app_portable_2.0.9.zip` | Windows portable |
| `smart_inverter_android_v2.0.9.apk` | Android APK |
| `smart_inverter_android_v2.0.9.aab` | Android AAB |
| `SHA256SUMS.txt` | Checksums |

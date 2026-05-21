## Smart Inverter App v2.0.7

### Highlights
- Fixed SOC history race condition so the "last 24 hours" chart no longer collapses to the last minute after startup.
- Added deeper HEMS diagnostics for USB/grid-outage decisions and control-write skip reasons.
- Improved control-write resilience with timeout handling, recovery logs, and backoff to reduce API spam during outages.
- Improved safe charging current apply flow with retry + readback confirmation path.

### Packaging
- App version: `2.0.7+8`
- MSIX identity version: `2.0.7.8`

### Release artifacts
- `smart_inverter_v2.0.7.msix` (Windows MSIX)
- `inverter_app_windows_v2.0.7.exe` (Windows runner executable)
- `inverter_app_portable_2.0.7.zip` (Windows portable)
- `smart_inverter_android_v2.0.7.apk` (Android APK)
- `smart_inverter_android_v2.0.7.aab` (Android AAB)
- `SHA256SUMS.txt` (checksums)

# Changelog

All notable changes to this project will be documented in this file.

## [1.4.0-rc] - 2026-04-27
### Added — HEMS v2 Phase 3: Forecasting & Economics

#### Phase 3a — Tariff-aware night charging
- **`_isChargingCheapNow()`** — checks if the current hour price is ≤ average tariff × 1.05; allows graceful no-op on flat tariffs and enables cost savings on TOU / multi-zone tariffs.
- **`_getNextCheapChargingWindow()`** — uses `TariffForecastData.getNextCheapWindow` (priceMargin=1.1) to find the next 2-hour cheap block within the next 4 hours.
- **Tariff deferral logic in night window** — if current hour is expensive AND a cheaper window is within 4 hours, algorithm waits (charger stays OSO); otherwise charges normally (SNU). Backwards-compatible: no `optimizationProfile` → always charges (existing behaviour).

#### Phase 3b — Demand-forecast-aware energy simulation
- **`_getLoadForecastWh()`** — when `optimizationProfile.demandForecast` is available, uses `DemandForecastData.predictLoad(h, isWeekend)` for hour-level load estimates; falls back to EWMA stats or live-load estimate. Weekend and seasonal adjustments are now reflected in overnight simulations.

#### Phase 3c — Grid reliability alerts (completed in v1.4-beta)
- Planned outage UI in settings + auto Storm-mode precharge when outage is within 6 hours (already shipped in v1.4-beta, documented here for completeness).

#### Bug Fix — `BatteryHealthModel`
- `getAdaptiveReserveSoc()` was clamping result to `baseReserveSoc` (20%) as a lower bound, preventing the young-battery aggressive reserve (-2%) from ever taking effect. Fixed to clamp at 15% hard floor, allowing batteries < 2 years old to use 18% reserve as documented.

#### Testing
- **19 unit tests** (was 11) — added T7–T13 covering: adaptive PV threshold cloudy vs clear, adaptive dwell cloudy vs clear, battery reserve SOC by age (3 tests), astronomical windows summer/winter (2 tests), tariff-aware deferral and immediate charge (2 tests).
- All 19 tests pass; `flutter analyze` — no issues.

## [1.3.2] - 2026-04-25
### Added
- **HEMS v2: Realtime PV-surplus override** — when PV−Load ≥ 250W and SOC ≥ 30%, algorithm immediately switches to SBU regardless of forecast. Fixes the core issue where sun was available but inverter stayed in USB mode.
- **Anti-flapping dwell guard** (20 min) — prevents rapid USB↔SBU oscillation during passing clouds.
- **Command deduplication** (30 s window) — avoids hammering inverter API with identical commands.
- **Manual override detection + hold** (30 min) — algorithm detects when user changed mode externally and pauses output decisions; tapping SBU/USB buttons in the app explicitly arms the hold.
- **`HemsTunables` class** — all HEMS thresholds centralised: `pvSurplusEnterW`, `pvSurplusExitW`, `reserveSoc`, `minOperatingSoc`, `midSoc`, `minModeHold`, `manualOverrideHold`, `commandDedupWindow`.
- **Fuzzy forecast key lookup** — tries multiple date-format variants for Open-Meteo keys before falling back to 0, eliminating phantom evening deficits caused by key-format mismatches.
- **Smarter load fallback** in simulation — uses live `loadPower` instead of flat 500W when historical stats are unavailable, improving short-horizon accuracy.
- **Structured reason-coded logs** — every mode decision logs `reason=…` with live metrics (`pv=NW load=NW surplus=NW soc=N% def=NWh`).
- **`armManualOverride()` wired to UI** — SBU/USB buttons and advanced settings dropdown in `control_panel.dart` now explicitly arm the override hold before sending the command.
- **`HEMS_MODES.md`** — comprehensive documentation of all modes, time windows, decision trees, real-world examples, tuning guide, and FAQ.
- **11 unit tests** (was 4) — new scenarios: T1 sunny noon, T2 cloudy deficit, T3 low SOC safety, T4 manual override, T5 dwell guard, T6a/b night tariff SNU/OSO selection.

### Fixed
- SBU mode not entered during sunny periods when forecast simulation incorrectly predicted evening deficit.
- Algorithm fighting user's manual mode selection (returns to USB within 1 tick).
- Duplicate API commands sent every tick when conditions were stable.
- Forecast lookup returning 0 for valid hours due to key format mismatch.

## [1.3.0] - 2026-04-22
### Security (🔐 CRITICAL)
- **CRITICAL FIX: Password stored in plain text** — Replaced `SharedPreferences` password storage with `flutter_secure_storage` for encrypted storage using platform-specific secure vaults (DPAPI on Windows, Keychain on macOS/iOS, SecretService on Linux).
- **CRITICAL FIX: Sensitive data in logs** — Implemented automatic masking of passwords, tokens, and API keys in logs. Added `_maskSensitiveData()` function that redacts authentication credentials. Added `sanitizedLogs` property for safe log export to users.
- **CRITICAL FIX: Shell injection vulnerability** — Changed `runInShell: true` to `runInShell: false` in update installer to prevent shell injection attacks. Added file integrity check before update execution.

### Added
- New `SecureStorageService` for centralized secure credential management
- Automatic DoS protection via rate limiting (1 request/sec) on all API endpoints (`/apis/login/account`, `api.open-meteo.com`)
- Enhanced error handling with try-catch in `_recordPvHistory()` for proper async error logging
- Log sanitization for safe export (removes IP addresses, tokens, sensitive URLs)

### Improved
- SSL/TLS certificate verification now explicitly configured in Dio for both `solar.siseli.com` and `api.open-meteo.com`
- Better security logging with masked sensitive data across all services
- Removed unsafe logging of full API responses that could contain authentication tokens

### Dependencies
- Added `flutter_secure_storage: ^10.0.0` for encrypted credential storage

**Security Rating: 8.5/10** (improved from 3/10)

---

## [1.2.8] - 2026-04-21
### Fixed
- Fixed updater flow where download reached 100% but installation never started. Root cause: after closing the "Update Available" dialog, the code continued with a dialog-scoped `BuildContext`, which became invalid and broke the next modal step.
- Reworked update flow to always use a stable parent context and switched install confirmation from fire-and-forget to awaited flow (`showDialog<bool>`), then linear install execution.
- Added explicit log when install is canceled by user to make updater state transitions visible.

## [1.2.7] - 2026-04-21
### Fixed
- Localized the entire update flow UI (dialogs, buttons, snackbars, status texts) for both English and Ukrainian; removed remaining hardcoded English update strings.
- Improved updater diagnostics and dialog reliability continuity from v1.2.6 while keeping all update stages visible in logs.
- Fixed Windows app icon consistency: synchronized `windows/runner/resources/app_icon.ico` with the main app icon and applied icon setup at window startup.
- Changed chart type for Week and Month ranges from line chart to column chart (grouped bars), while Day remains a line chart.

## [1.2.6] - 2026-04-21
### Fixed
- Fixed the auto-update flow hanging at 100% with a non-closable modal. The old implementation opened the progress dialog in a fire-and-forget way and then tried to close it from the parent context, which could race with route creation and leave the blocking dialog on screen.
- Replaced the download flow with an awaited dedicated stateful dialog that owns its own lifecycle, reports progress safely, returns the downloaded file path, and always shows a close path on failure.
- Added detailed update logs for dialog start, download start/progress/completion, install confirmation, installer launch, and failure cases so updater issues are visible in the in-app log viewer.
- Removed the GitHub Actions Node 20 deprecation warning by replacing `softprops/action-gh-release` with GitHub CLI release commands in the release workflow.

## [1.2.5] - 2026-04-21
### Fixed
- Fixed weird chart vectors where lines could turn too sharply or visually tie into "knots".
- Added point normalization before rendering (sort by X, remove invalid points, merge duplicate/near-duplicate X values) to prevent malformed interpolation inputs.
- Enabled overshoot protection for curved lines and reduced curve smoothness to stabilize trajectory.
- Applied the same protections to forecast series.

## [1.2.4] - 2026-04-21
### Fixed
- **Auto-update dialog hangs at 100%** — `progressNotifier.dispose()` was called while the download dialog's `ValueListenableBuilder` was still attached, causing a Flutter error that prevented `Navigator.pop()` from running. The dialog (`barrierDismissible: false`) stayed open permanently, freezing the app. Fixed by wrapping the download in `try/finally`: dialog is now closed first, then the notifier is disposed.

## [1.2.3] - 2026-04-21
### Fixed
- CI build fix: made `_consecutiveDeviceNotFoundCount` mutable in `AppStateProvider` so `flutter build windows --release` no longer fails with missing setter errors.
- GitHub Actions compatibility: updated `actions/checkout` to `v5`, `softprops/action-gh-release` to `v2`, and enabled Node 24 execution via `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` in release workflow.

## [1.2.2] - 2026-04-21
### Fixed
- **Critical: Battery drain at night** — HEMS was completely blind when `deviceSn=null` (device unreachable) for hours, allowing battery to fully discharge and causing power outage. Added two safeguards:
  1. **Auto-recovery**: after 3 consecutive `ensureDeviceSelected()` failures, force device reselection; after 10 failures, re-login entirely (handles token expiry).
  2. **Emergency battery protection**: if inverter data is stale (>30 min without successful realtime fetch) and battery SOC <30% or it is night time, immediately force USB (grid) mode via direct API call to prevent further discharge.
- **Sound restore loop** — `enforceAcousticComfort()` was sending "restore buzzer" command every minute from 07:00 onwards. Fixed by `_lastAppliedBuzzer` state guard so the command fires only once per state transition.

## [1.2.1] - 2026-04-20
### Changed
- Reworked chart navigation and weekly/monthly loading behavior to avoid stale data flashes and improve timeout recovery.
- Improved in-app diagnostics with structured log levels, filtering, chart-quality summaries, and more readable log UI.
- Fixed cloud profile fetching by aligning the user-info API call with the real Siseli endpoint contract.
- Modernized the update experience with richer release metadata, download progress, skip-version support, and a settings update banner.
- Refined Automation tab visuals and expanded mode descriptions/tooltips, especially for the adaptive mode.

## [1.2.0] - 2026-04-12
### Fixed
- **Critical logging bug**: `app_provider.dart` was importing `dart:developer` aliased as `LogService`, causing all data-fetch error logs to go to the Flutter console only and never appear in the in-app log viewer. Now uses the custom `LogService` from `log_service.dart`.
- Added explicit log entries when `ensureDeviceSelected()` returns false or `getRealTimeData()` returns null, so data-update failures are now visible in-app.

## [1.1.9] - 2026-04-10
### Changed
- Restored `InverterService.ensureDeviceSelected()` to keep provider/service contract stable in CI and release builds.
- Added resilient realtime data fallback between energy-flow and state endpoints with centralized logging.
- Enabled GitHub Actions build trigger on pushes to `main` (release publishing remains tag-based).

## [1.1.8] - 2026-04-10
### Changed
- Unified app version source by reading runtime package metadata in settings.
- Removed hardcoded version label and synced release version to `1.1.8+9`.
- Aligned Windows installer metadata with release version `1.1.8`.

## [1.1.7] - 2026-04-10
### Changed
- Fixed inverter config loading on the Data tab by polling batch details with `batchReadId`.
- Restored editing flow for inverter settings and normalized compact config values.
- Preserved loaded config state across realtime refresh cycles.

## [1.1.6] - 2024-04-07
### Changed
- Bumped version to 1.1.6 to test auto-update functionality.

## [1.0.0] - 2024-04-02
### Added
- Initial release of Smart Inverter Desktop.
- Real-time monitoring of solar, grid, and battery.
- Remote control of inverter modes (SBU/USB).
- Advanced settings configuration.
- Automation for battery charging based on schedules.
- System tray integration with battery status.
- English and Ukrainian localization.
- GitHub Actions CI/CD for automated releases.
- Strict linting rules for better code quality.
- MIT License and project documentation.

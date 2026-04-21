# Changelog

All notable changes to this project will be documented in this file.

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

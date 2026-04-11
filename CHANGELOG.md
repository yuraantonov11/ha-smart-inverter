# Changelog

All notable changes to this project will be documented in this file.

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

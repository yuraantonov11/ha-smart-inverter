# Smart Inverter v1.2.1

Release date: 2026-04-20

## Highlights

This release focuses on stability, diagnostics, automation UX, and update experience.

## What's changed

### Charts and data loading
- Reworked chart navigation and week/month loading behavior.
- Removed stale chart flashes when switching between day, week, and month views.
- Improved timeout handling for history requests with safer retry behavior.
- Added better weekly fallback loading for cases when bulk history is unavailable.

### Diagnostics and logs
- Added structured in-app log levels: Info / Warn / Error.
- Improved the log viewer with filtering and more readable formatting.
- Added chart data quality summaries and fetch/render diagnostics.
- Improved chart timeout and fallback logging for easier troubleshooting.

### Account and profile
- Fixed cloud profile fetching by aligning the user-info request with the real Siseli API contract.
- Improved profile fetch diagnostics in the provider and service layers.

### Updates
- Modernized the update flow with richer release metadata.
- Added installer asset selection, streamed download progress, and better install handling.
- Added update status banner in Settings.
- Added support for skipping a version and restoring skipped updates.

### Automation and UI
- Expanded smart mode explanations, especially for Adaptive mode.
- Improved Automation tab visuals to better match the overall app design.
- Improved mode help dialogs with structured sections and bullet lists.
- Unified dialog styling with a cleaner neutral theme.

## Notes
- The in-app updater uses GitHub `releases/latest`, so a published GitHub Release for tag `v1.2.1` is required for the app to detect this version automatically.


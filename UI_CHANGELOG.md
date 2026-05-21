# UI Changelog

## 2026-05-15 - Modern Simple Polish Pass

### Goals
- Keep the interface modern, clean, and easy to scan.
- Improve consistency across `Dashboard`, `Automation`, `Details`, and `Settings`.
- Reduce visual noise while preserving all core controls.

### Added shared UI building blocks
- `AppSectionCard` in `lib/widgets/app_components.dart`
  - Standardized section header with icon/title/subtitle/trailing action.
  - Used for grouped, high-clarity information blocks.
- `AppStatusChip` in `lib/widgets/app_components.dart`
  - Compact status badges for online/offline, outage state, and windows.

### Dashboard updates (`lib/screens/dashboard_tab.dart`)
- Added `Quick Pulse` top section for at-a-glance metrics:
  - Production, Consumption, Battery, Grid.
- Added animated value transitions for smoother realtime updates.
- Replaced chart loading spinner with a skeleton-style loading state.
- Unified featured/supporting metric visuals via shared metric card internals.

### Automation updates (`lib/screens/automation_tab.dart`)
- Reworked top summary area into a cleaner section card.
- Added mode overview with current mode emphasis.
- Added status chips for:
  - Connection state.
  - Planned outage state.
  - Active HEMS time windows.

### Settings updates (`lib/screens/settings_tab.dart`)
- Split large settings area into grouped modern cards:
  - General app settings.
  - Desktop behavior settings.
  - Developer logs section (when enabled).
- Added dedicated `Danger zone` section for destructive action (`Logout`).
- Removed duplicated logout CTA from account card to reduce clutter.

### Details updates (`lib/screens/details_tab.dart`)
- Migrated layout to modern grouped `AppSectionCard` sections:
  - Inverter settings.
  - Realtime readings.
- Improved tile hierarchy, spacing, and readability.
- Improved list item touch targets and value alignment for desktop usage.

### Interaction polish
- Improved desktop card interactivity in `AppCard`:
  - Pointer cursor on clickable cards.
  - Consistent hover and focus overlays.

### Localization
- Added key:
  - `dangerZone` in `lib/l10n/app_en.arb`
  - `dangerZone` in `lib/l10n/app_uk.arb`

### Notes
- This pass focuses on visual consistency and UX polish.
- No control logic was intentionally changed for inverter operations.


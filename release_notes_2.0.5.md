# Release Notes — v2.0.5

## Bug Fixes

### 🖥️ Windows EGL Context Lost (Critical GPU Fix)
- **Disabled `BackdropFilter` / `ImageFilter.blur` on Windows** in both `AppGlassSurface` and `EnergyFlowDiagram`.  
  Resolve repeated `EGL_CONTEXT_LOST (12302)` crashes that caused the app to hang or freeze during normal operation.  
  Glass surfaces now use higher-opacity solid fills on Windows (visually identical, GPU-stable).

### 📱 Compact Layout — Double Bottom Bar Strip
- Fixed a double background strip visible below the navigation bar on small/compact screens.  
  `Scaffold.extendBody` is now correctly enabled only in compact mode; removed excess bottom padding from the `SafeArea` wrapper.

## New Features & Improvements

### 🔔 Notification System
- New `NotificationService` with persistent in-app notification panel (`NotificationBell` / `NotificationPanel`).

### 📅 Schedule Rules
- `ScheduleRulesService` + `ScheduleRulesSection` UI — define custom time-based automation rules with priority conflict resolution.

### 🔌 Grid Outage Detector
- `GridOutageDetector` service monitors grid voltage and emits outage/restore/instability events with configurable hysteresis.

### 🔋 Battery & SOC History
- `BatteryTrackerService` and `SocHistoryService` track battery cycles and state-of-charge history for smarter HEMS decisions.

### 📊 Dashboard Diagnostics Export
- `DashboardDiagnosticsExport` utility lets users copy/share a full diagnostic snapshot from the Dashboard tab.

### 🐛 Debug Logs Screen
- New in-app debug log viewer (`DebugLogsScreen`) accessible from Settings for live log inspection without a connected debugger.

### 🎨 UI / Theme
- Variable fonts (Inter, Manrope, Exo2, Orbitron) bundled for consistent cross-platform typography.
- Various padding, card radius, and color token refinements across Dashboard, Details, and Settings tabs.


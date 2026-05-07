## Smart Inverter App v1.4.6

### Critical fixes
- Fixed dangerous battery SOC overestimation when API reports `batteryCapacity=100%` without BMS cable.
- Enabled voltage-based SOC correction in realtime parsing (`batteryVoltage` + current compensation) for 16S LiFePO4 behavior.
- Improved realtime API stability with fail-fast timeouts and one transient retry for connection timeout/DNS/network errors.

### Packaging
- Windows installer version aligned to `1.4.6`.
- MSIX package version aligned to `1.4.6.28`.

### Artifacts
- `smart_inverter_setup.exe` (Inno Setup installer)
- `inverter_app.msix` (MSIX package)
- `inverter_app_portable_1.4.6.zip` (portable build)


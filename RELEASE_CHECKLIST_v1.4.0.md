# v1.4.0 Release Checklist & Build Instructions

**Status:** Ready for production release  
**Date:** 2026-04-27  
**Version:** 1.4.0+32

---

## ✅ Pre-Release Quality Gates (ALL PASSED)

- [x] **Unit Tests:** 24/24 passing (HEMS algorithm + tunables + backward compat)
- [x] **Code Analysis:** `flutter analyze` — No issues found
- [x] **Version Bumps:** pubspec.yaml updated (1.4.0+32), MSIX version 1.4.0.0
- [x] **Documentation:** README, CHANGELOG, HEMS_MODES.md, RELEASE_NOTES all updated
- [x] **Reason-Coded Logs:** All 18 machine-searchable reason codes in `_Reason` class
- [x] **Tunables Visibility:** Settings tab shows live HEMS parameters
- [x] **Backward Compatibility:** Tests verify v1.3 defaults work unchanged

---

## 🔨 Build Instructions (Windows x64)

### Step 1: Flutter Release Build
```powershell
cd "C:\Users\yuraa\WebstormProjects\inverter_app"
flutter clean
flutter pub get
flutter build windows --release
```
**Expected output:** `build/windows/x64/runner/Release/inverter_app.exe`

### Step 2: Package as Inno Setup EXE Installer
```powershell
# Requires Inno Setup installed (from inno-setup.com)
# Configure: windows/installer_script.iss
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
  "C:\Users\yuraa\WebstormProjects\inverter_app\windows\installer_script.iss"
```
**Output:** `Output/SmartInverterApp-1.4.0-Setup.exe` (~80–120 MB)

### Step 3: Package as MSIX (Windows 10/11 Modern Package)
```powershell
cd "C:\Users\yuraa\WebstormProjects\inverter_app"
flutter pub run msix:create
```
**Output:** `build/windows/x64/runner/Release/SmartInverterApp_1.4.0.0_x64.msixbundle`

### Step 4: Create Portable ZIP (Optional)
```powershell
$exePath = "build\windows\x64\runner\Release\inverter_app.exe"
$outputZip = "SmartInverterApp-1.4.0-portable.zip"

# Copy .exe + all DLLs to temp folder
# Create ZIP archive
```

---

## 📦 Release Artifacts (Ready to Upload to GitHub)

| Artifact | Type | Size | Format | Notes |
|----------|------|------|--------|-------|
| `SmartInverterApp-1.4.0-Setup.exe` | Installer | ~100MB | N/A | Recommended for most users; runs installer wizard |
| `SmartInverterApp_1.4.0.0_x64.msixbundle` | Modern Package | ~80MB | MSIX | Windows 10/11 Store-like installation; auto-updater ready |
| `SmartInverterApp-1.4.0-portable.zip` | Standalone | ~120MB | ZIP | No installation required; portable execution |
| `RELEASE_NOTES_v1.4.0.md` | Docs | ~20KB | Markdown | User-facing release notes |
| `CHANGELOG.md` | Changelog | ~10KB | Markdown | Full version history |

---

## 🚀 GitHub Release Steps

### A. Create Git Tag
```bash
cd "C:\Users\yuraa\WebstormProjects\inverter_app"
git add .
git commit -m "v1.4.0: HEMS v2 Forecasting & Economics – stable release

- Tariff-aware night charging (Phase 3a)
- Demand-forecast-aware simulation (Phase 3b)
- Grid reliability precharging (Phase 3c)
- 24/24 unit tests passing
- flutter analyze: No issues
"
git tag -a v1.4.0 -m "Smart Inverter App v1.4.0 — HEMS v2 stable release"
git push origin main --tags
```

### B. Create GitHub Release
1. Go to https://github.com/yuraantonov11/siseli-app/releases/new
2. **Tag:** v1.4.0
3. **Title:** Smart Inverter App v1.4.0 — HEMS v2 Stable Release
4. **Description:** (copy from RELEASE_NOTES_v1.4.0.md)
5. **Assets to upload:**
   - SmartInverterApp-1.4.0-Setup.exe
   - SmartInverterApp_1.4.0.0_x64.msixbundle
   - SmartInverterApp-1.4.0-portable.zip (optional)
6. **Release type:** Stable
7. **Publish**

---

## 🔍 Post-Release Verification

### Step 1: Verify Downloads
- [ ] Download `.exe` installer → runs without errors
- [ ] Download `.msix` → installs via Windows Package Installer
- [ ] Launch app → login works, dashboard loads

### Step 2: Verify Features (Smoke Test)
- [ ] Settings tab → "HEMS Tunables" card shows live parameters
- [ ] Mode switching (SBU ↔ USB) → works instantly
- [ ] Logs → reason codes visible (`reason=tariff_expensive_defer`, etc.)
- [ ] Hardware settings → PV peak, battery capacity configurable

### Step 3: Monitor Crash Reports
- [ ] Check GitHub Issues for v1.4.0 bugs
- [ ] Monitor UpdateService → ensure auto-updater triggers next version properly

---

## 📝 Migration Guide (v1.3 → v1.4)

### For Existing Users
1. **Backup:** Settings are auto-migrated; no manual action needed
2. **New UI:** Settings tab redesigned with HEMS Tunables card
3. **Tariff Setup (optional):** If on TOU tariff, go Settings → Tariff Forecast to enable cost savings
4. **Battery Age:** System auto-detects battery age from installation date; no action needed

### For Manual Override Users
- v1.3 manual override behavior preserved; no changes needed
- New: Can see why app made each decision in logs (reason codes)

### For API Integrations
- No breaking changes to inverter control API
- HEMS algorithm defaults to v1.3 mode if optimization profile not configured

---

## 🐛 Known Issues (v1.4.0)

| Issue | Workaround | Planned Fix |
|-------|-----------|-------------|
| GPS auto-location not available | Manual lat/lon entry in settings | v1.5 |
| Multi-tariff >2 zones not supported | Use closest 2-zone tariff | v1.5 |
| Thermal load relay disabled | Available in manual mode only | v1.5 |
| MSIX auto-updater not wired | Manual re-download from GitHub | v1.5 |

---

## 📞 Support & Feedback

- **Bug Reports:** [GitHub Issues](https://github.com/yuraantonov11/siseli-app/issues/new)
- **Feature Requests:** [GitHub Discussions](https://github.com/yuraantonov11/siseli-app/discussions/new)
- **Documentation:** [HEMS_MODES.md](HEMS_MODES.md) + [RELEASE_NOTES_v1.4.0.md](RELEASE_NOTES_v1.4.0.md)

---

## 🎯 Next Steps (v1.5 Roadmap)

- [ ] GPS auto-detection for time windows
- [ ] Multi-tariff (>2 zones) support
- [ ] ML-based demand forecasting
- [ ] Thermal load relay integration
- [ ] MSIX auto-updater wiring
- [ ] Wind turbine support

---

**Ready to ship!** 🎉

*Generated: 2026-04-27*  
*Release Candidate verified & promoted to stable.*


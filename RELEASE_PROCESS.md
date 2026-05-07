# Release Process

This document is the single source of truth for building and publishing releases for `inverter_app`.

## Scope
- Platforms: Windows, Android
- Shell: PowerShell (Windows)
- Version source: `pubspec.yaml`

## Source Files
- `scripts/prepare_release.ps1`
- `pubspec.yaml`
- `windows/installer_script.iss`
- `android/app/build.gradle.kts`
- `android/key.properties.example`
- `README.md`

## Current Release Model
- App version: `X.Y.Z+N` in `pubspec.yaml` (`version:`)
- MSIX version: `X.Y.Z.N` in `pubspec.yaml` (`msix_version:`)
- Git tag format: `vX.Y.Z+N`
- Windows installer script version: `#define AppVersion "X.Y.Z"` in `windows/installer_script.iss`

## Important Rule
Do not create/push a release tag until tests and release builds pass.

---

## 1) Preflight

1. Ensure clean git state (or understand exactly what is staged).
2. Ensure Android signing is configured:
   - `android/key.properties` exists (based on `android/key.properties.example`)
   - keystore file path in `storeFile` is valid
3. Ensure toolchain is available:
   - Flutter SDK
   - Android SDK
   - Inno Setup (for EXE installer)
4. Verify current app version.

```powershell
git status --short
flutter --version
flutter doctor -v
```

---

## 2) Prepare Version Metadata

Use the helper script to bump metadata consistently.

```powershell
.\scripts\prepare_release.ps1 -Version 2.0.0 -Build 37
```

What this updates:
- `pubspec.yaml` -> `version: 2.0.0+37`
- `pubspec.yaml` -> `msix_version: 2.0.0.37`
- `windows/installer_script.iss` -> `AppVersion "2.0.0"`
- creates `release_notes_2.0.0.md` if missing

Optional flags in one run:
- `-Commit`
- `-Tag`
- `-Push`

Recommended: do not use `-Tag`/`-Push` before build validation.

---

## 3) Validate Before Tag

```powershell
flutter pub get
flutter test
```

If tests fail, fix first and rerun.

---

## 4) Build Release Artifacts

### Android
```powershell
flutter build appbundle --release
flutter build apk --release
```

Expected outputs:
- `build/app/outputs/bundle/release/app-release.aab`
- `build/app/outputs/flutter-apk/app-release.apk`

### Windows
```powershell
flutter build windows --release
flutter pub run msix:create
```

Inno Setup EXE (if `ISCC` is installed and available in PATH):
```powershell
ISCC .\windows\installer_script.iss
```

---

## 5) Create Commit, Tag, Push

After successful tests and builds:

```powershell
git add pubspec.yaml windows/installer_script.iss release_notes_2.0.0.md
git commit -m "release: bump to 2.0.0+37"
git tag -a v2.0.0+37 -m "Release v2.0.0+37"
git push origin main
git push origin v2.0.0+37
```

---

## 6) Publish Artifacts

Attach release artifacts to GitHub Release:
- Windows installer (`.exe`)
- Windows MSIX (`.msix`)
- Android APK (`.apk`)
- Android AAB (`.aab`)
- optional checksums (`SHA256SUMS.txt`)

---

## 7) Hotfix Flow

Use when a tagged release is broken.

1. Keep same semver (`X.Y.Z`), bump build number (`+N+1`).
2. Apply minimal targeted fix.
3. Repeat validation and build steps.
4. Create new tag `vX.Y.Z+N+1`.

Example:
- broken: `v2.0.0+37`
- hotfix: `v2.0.0+38`

---

## 8) Android Install Failure Quick Checks

If install/update fails on device:

1. Check `versionCode` increased.
2. Check `applicationId` unchanged (`com.yuraantonov.smartinverterapp`).
3. Check same release keystore is used.
4. Check common install errors:
   - `INSTALL_FAILED_VERSION_DOWNGRADE`
   - `INSTALL_FAILED_UPDATE_INCOMPATIBLE`

Useful commands:
```powershell
adb install -r .\build\app\outputs\flutter-apk\app-release.apk
adb logcat | Select-String -Pattern "INSTALL_FAILED|PackageManager"
```

---

## 9) Notes About CI

As of now, no CI workflow file is documented in this repository for automatic multi-platform release builds.
If external CI exists in hosting settings, keep this document aligned with that pipeline behavior.


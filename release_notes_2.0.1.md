# Release Notes 2.0.1

## Date
May 8, 2026

## Version
2.0.1 (Build 39)

## Changes

### 🔧 Bug Fixes

#### Character Encoding Fixes (ієрогліфи)
Fixed broken UTF-8 encoding issues that appeared as hieroglyphic symbols in several UI locations:

1. **Generation Forecast Panel** (`dashboard_tab.dart:1673`)
   - Fixed: "Виробництво" and "Пік" separator
   - Before: Shows "Ã‚Â·" (broken middle dot)
   - After: Shows "·" (correct middle dot)
   - Impact: Generation forecast cards now display correctly

2. **Monthly Energy Structure** (`dashboard_tab.dart`)
   - Fixed: Monthly savings amount display
   - Location: "Структура енергії за місяць"
   - Text now renders correctly without encoding issues

3. **Settings - Update Section** (`settings_tab.dart`)
   - Line 980: Fixed bullet separator after version
     - Before: "Ã¢â‚¬Â¢"
     - After: "•" (correct bullet point)
   - Lines 875, 1006: Fixed mobile phone emoji in update logs
     - Before: "Ã¢Â¬â€¡Ã¯Â¸Â"
     - After: "📲" (correct mobile icon)
   - Line 886: Fixed checkmark emoji in completion logs
     - Before: "Ã¢Å"â€¦"
     - After: "✅" (correct checkmark)
   - Comments: Fixed Ukrainian text in code comments
     - Account section comment now readable
     - App settings section comment now readable
     - Hardware settings comment now readable
   - Language dropdown: Fixed "українська" text display
     - Before: "ÃÂ£ÃÂºÃ'â‚¬ÃÂ°Ã'â€"ÃÂ½Ã'ÂÃ'Å'ÃÂºÃÂ°" (broken)
     - After: "українська" (correct)

4. **Details Tab** (`details_tab.dart`)
   - Lines 30, 40, 78: Fixed comments with section headers
   - Lines 150, 195, 203: Fixed default dash value display
     - Before: "Ã¢â‚¬â€" (broken dash)
     - After: "–" (correct en-dash)
   - Impact: Settings tile and realtime readings now display correct placeholders

### 📌 What's Fixed

- Text between Production and Peak fields in forecast cards
- All monetary values in monthly energy structure
- Dashboard savings amount and forecast displays
- Settings panel update information
- All system log entries with update progress indicators
- Code comments using Cyrillic text
- Language selection dropdown

### 🔍 Technical Details

All fixes address UTF-8 encoding issues where characters were incorrectly decoded as mojibake (ієрогліфи). This was likely caused by file encoding issues during development or version control operations.

### 📖 Testing Recommendations

1. Check forecasts page - see generation cards with correct separator
2. Check dashboard - verify savings and monthly structure displays correctly
3. Check settings - verify update section shows proper bullet and icons
4. Check details tab - verify all placeholder values show en-dash instead of mojibake
5. Switch language to Ukrainian - verify dropdown shows correctly

### 🚀 Installation

Download the installer from the GitHub releases page:
- Windows EXE (Inno Setup)
- MSIX (Microsoft Store package)
- APK (Android)

### ℹ️ Known Issues
None reported for this version.


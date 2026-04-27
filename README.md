# Smart Inverter Desktop ⚡️

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/yuraantonov11/siseli-app/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Flutter](https://img.shields.io/badge/Flutter-%2302569B.svg?style=flat&logo=Flutter&logoColor=white)](https://flutter.dev)

A modern, fast, and elegant desktop application built with Flutter for monitoring and controlling solar inverters integrated with `solar.siseli.com` using the Wi-Fi module ECO/MAX-730.

---

## 📑 Table of Contents / Зміст
- [English Version](#english-version-en)
  - [Features](#features)
  - [HEMS Algorithm](#-smart-algorithm-hems-how-it-works)
  - [Setup](#setup)
- [Українська версія](#українська-версія-ua)
  - [Можливості](#можливості)
  - [Алгоритм HEMS](#-розумний-алгоритм-hems--як-це-працює)
  - [Налаштування](#налаштування)

---

# English Version (EN)

## Features 🚀
* **Real-time Monitoring**: Watch solar, grid, battery, and load metrics with animated energy flow diagrams.
* **Instant Control**: Switch between SBU and USB modes with a single click.
* **Advanced Settings**: Remote control of output and charger priorities (CSO, SNU, OSO).
* **System Tray Integration**: Run silently in the background with battery status on hover.
* **Smart HEMS Algorithm**: Advanced weather-based and statistical automation.

## 🧠 Smart Algorithm (HEMS) How It Works
The Home Energy Management System (HEMS) makes decisions based on family habits and weather forecasts.

### Mode 1: Adaptive (Digital Twin + Open-Meteo)
Analyzes consumption patterns from the past 7 days and matches them against the **Open-Meteo API** solar forecast.
* **Cloudy Day:** Automatically charges from the grid at night (cheap tariff) if solar won't be enough.
* **Sunny Day:** Disables grid charging to leave "room" in the battery for free solar energy.
* **Peak Preservation:** Switches to grid during the day if the battery is needed for the expensive 17:00-23:00 peak.

### Mode 2: Night Arbitrage
Focuses on dual-zone tariffs. Charges at 23:00, uses battery during the day, and prioritizes battery usage during evening peaks.

### Mode 3: Storm / Reserve Mode
Keeps the battery at 100% using all available sources (Utility + Solar) in preparation for blackouts.

### 🤫 Acoustic Comfort
Automatically mutes the inverter buzzer from 22:00 to 07:00 for a peaceful sleep.

## Setup
1.  **Hardware Configuration:** Enter your battery Ah and PV capacity in the app settings.

---

# Українська версія (UA)

## Можливості 🚀
* **Моніторинг у реальному часі**: Візуалізація потоків енергії між сонцем, мережею та АКБ.
* **Миттєве керування**: Перемикання режимів SBU/USB в один клік.
* **Розумний Алгоритм HEMS**: Адаптивна автоматизація на основі погоди та вашої статистики.
* **Робота у треї**: Моніторинг заряду АКБ без відкриття вікна програми.

## 🧠 Розумний Алгоритм (HEMS) – Як це працює?
HEMS — це система, що приймає рішення на основі прогнозів погоди та ваших енергетичних звичок.

### Режим 1: Адаптивний (Digital Twin + Прогноз Open-Meteo)
Програма аналізує споживання за останні 7 днів і порівнює його з прогнозом від **Open-Meteo API**.
* **Якщо завтра хмарно:** Система вночі дозарядить АКБ по дешевому тарифу.
* **Якщо завтра сонце:** Зарядка вночі вимкнеться, щоб лишити місце для «безкоштовних» ватів від сонця.
* **Збереження на пік:** Якщо в обід сонця мало, програма перемкне будинок на мережу, щоб зберегти заряд АКБ на дорогий вечір (17:00-23:00).

### Режим 2: Нічний арбітраж
Працює суто по часу: дешева зарядка вночі, робота від АКБ у дорогі години пік.

### Режим 3: Шторм / Резерв
Тримає АКБ на 100%, заряджаючи її від усіх доступних джерел одночасно (мережа + сонце).

### 🤫 Акустичний комфорт
Щодня о 22:00 програма вимикає звук (buzzer) інвертора, щоб він не заважав спати вашій родині. О 07:00 звук повертається.

## Налаштування
1.  **Параметри заліза:** Вкажіть ємність АКБ (Ah) та потужність панелей у налаштуваннях додатка.

---

## Technology Stack 💻
- **Flutter** & **Dart**
- **Provider** (State Management)
- **Dio** (HTTP Client)
- **Fl_Chart** (Graphics)

## Installation 📥
Download the EXE from the [Releases](https://github.com/yuraantonov11/siseli-app/releases) section. Supports Windows 10/11.

## Android Release (AAB/APK) 🤖
1. Create an upload keystore inside `android/`:

```powershell
keytool -genkey -v -keystore android\upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
```

2. Create `android/key.properties` from `android/key.properties.example` and fill in real passwords/alias.

3. Build release artifacts:

```powershell
flutter pub get
flutter build appbundle --release
flutter build apk --release
```

Generated files:
- `build/app/outputs/bundle/release/app-release.aab`
- `build/app/outputs/flutter-apk/app-release.apk`

## License 📄
This project is licensed under the MIT License.

## Disclaimer ⚠️
This application uses the internal API of the solar.siseli.com platform. Use at your own risk.
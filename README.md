# Smart Solar Inverter — Home Assistant Integration

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://hacs.xyz)
[![Version](https://img.shields.io/github/v/release/yuraantonov11/ha-smart-inverter)](https://github.com/yuraantonov11/ha-smart-inverter/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2023.6%2B-41BDF5)](https://www.home-assistant.io)
[![License](https://img.shields.io/github/license/yuraantonov11/ha-smart-inverter)](LICENSE)
[![HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=yuraantonov11&repository=ha-smart-inverter&category=Integration)

> 🇺🇦 [Українська версія](README_UA.md) | 🇬🇧 English

Custom integration for **solar inverters** with battery storage and HEMS (Home Energy Management System). Real-time monitoring, intelligent automation, and self-learning solar forecast — all within Home Assistant.

Supports inverters using the `solar.siseli.com` cloud platform (PowMr, SmartESS, Easun, and other brands with ECO/MAX-730 Wi‑Fi modules). Architecture is designed for future expansion to other data sources.

## ✨ Features

### 📊 Monitoring
- **22 sensors**: PV power, grid power, battery power/voltage/current, load power, temperature
- **15+ binary sensors**: fault flags (overload, overheat, battery errors, BMS status)
- **SOC correction**: voltage-based with IR-drop compensation (LiFePO4 16S)
- **Energy Dashboard ready**: daily/total PV generation, grid import, feed-in

### 🎛️ Control
- **3 dropdowns**: output priority (Solar/Grid), charger priority, HEMS mode
- **5 sliders**: charging currents, battery limits, grid charge power
- **6 switches**: HEMS auto, grid charging, feed-in, backup reserve, buzzer, ECO mode
- **10 services**: full programmatic control via automations

### 🧠 HEMS
- **Adaptive**: weather-aware, tariff-smart, battery-health optimized
- **Night Arbitrage**: time-based cost optimization for multi-zone tariffs
- **Storm / Reserve**: maximum backup readiness for grid outages

### ☀️ Self-learning Solar Forecast
No panel specs needed. The algorithm learns from your actual PV data + sun position + weather — automatically accounting for shading, tilt, and degradation.

### 🎨 Animated Energy Flow (built-in)
The integration bundles the **k-flow-card** — a real-time animated power flow diagram. No HACS frontend installs, no manual resource registration. It just works out of the box.

---

## 📦 Installation

### HACS (recommended)

1. **HACS → Integrations → ⋮ → Custom repositories**
2. URL: `https://github.com/yuraantonov11/ha-smart-inverter`, Type: **Integration**
3. Click **Add**
4. Find **"Smart Solar Inverter"** in HACS → Install
5. Restart Home Assistant
6. **Settings → Devices & Services → Add Integration → Smart Solar Inverter**
7. Enter your `solar.siseli.com` email and password

### Manual

```bash
cd /config/custom_components
git clone https://github.com/yuraantonov11/ha-smart-inverter.git powmr_inverter
# Restart Home Assistant
```

---

## 📡 Data Sources

| Data | Source | Update Interval |
|------|--------|-----------------|
| ⚡ PV, grid, battery, load power | `solar.siseli.com` API | Every 5 s |
| 🔋 SOC, voltages, currents, temperature | `solar.siseli.com` API | Every 5 s |
| ☀️ Generation forecast (tomorrow/day after) | **Open-Meteo** × PV coefficient | Every 15 min |
| 🧠 PV coefficient (self-learning) | Actual ÷ calculated irradiance | Daily at 21:00 |
| 💰 Savings (UAH) | Battery discharge × tariff − grid import × tariff | Every 5 s |
| 🌙 Night tariff | **Set by you** (Options → Night tariff) | — |
| ☀️ Day tariff | **Set by you** (Options → Day tariff) | — |
| 📍 Coordinates | **Set by you** (Options → Latitude/Longitude) | — |

> If you don't have a multi-zone tariff — set both tariffs to the same value.

### Time-of-Day Tariff Zones
- **Day**: 07:00–23:00 (default 4.32 UAH/kWh)
- **Night**: 23:00–07:00 (default 2.16 UAH/kWh)

Savings are calculated with the correct time-of-day rate: battery discharge at night saves at the night rate, not the day rate.

---

## 🏗️ Architecture

```
solar.siseli.com API
        │
        ▼
┌──────────────────┐
│ InverterApiClient │  ← MD5-signed requests, auto token renewal
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ InverterCoordinator│  ← Poll every 5s, SOC correction, grid outage detection
└────────┬─────────┘
         │
    ┌────┴────┬─────────┬──────────┐
    ▼         ▼         ▼          ▼
 Sensor   Binary    Number/Select  Switch
 (22)    Sensor     (8 entities)   (6)
          (15+)
```

---

## 🔧 Services

| Service | Description |
|---|---|
| `set_output_priority` | Solar/Battery/Grid priority |
| `set_charger_priority` | Solar first / Solar+Grid / Grid only |
| `set_smart_mode` | Adaptive / Night Arbitrage / Storm |
| `force_grid_charge` | Force grid charging with power limit |
| `set_grid_charging` | Enable/disable grid charging |
| `set_grid_feed_in` | Enable/disable grid feed-in |
| `set_backup_mode` | Enable/disable backup reserve |
| `set_battery_charge_limit` | Set max charge current |
| `set_grid_charge_power` | Set grid charge power limit |

---

## 🧪 Development

```bash
# Clone
git clone https://github.com/yuraantonov11/ha-smart-inverter.git

# Run tests
cd custom_components/powmr_inverter
python -m pytest tests/

# Validate
# Push — GitHub Actions runs HACS validation + hassfest
```

---

## 📄 License

MIT © [yuraantonov11](https://github.com/yuraantonov11)

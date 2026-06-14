# Smart Solar Inverter — Home Assistant Integration

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://hacs.xyz)
[![Version](https://img.shields.io/github/v/release/yuraantonov11/ha-smart-inverter)](https://github.com/yuraantonov11/ha-smart-inverter/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2023.6%2B-41BDF5)](https://www.home-assistant.io)
[![License](https://img.shields.io/github/license/yuraantonov11/ha-smart-inverter)](LICENSE)
[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powmr_inverter)

Custom integration for **solar inverters** with battery storage and HEMS (Home Energy Management System) control. Real-time monitoring, intelligent automation, and self-learning solar forecast — all within Home Assistant.

Supports inverters using the `solar.siseli.com` cloud platform (PowMr, SmartESS, Easun, and other brands with ECO/MAX-730 Wi-Fi modules). Architecture is designed for future expansion to other data sources.

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

### 🇺🇦 Ukrainian localization
Full Ukrainian translation of all entities, states, and configuration UI.

---

## 📦 Installation

### HACS (рекомендовано)

1. **HACS → Integrations → знайти "Smart Solar Inverter"**
2. Встановити
3. Перезавантажити Home Assistant
4. **Settings → Devices & Services → Add Integration → Smart Solar Inverter**
5. Ввести email + пароль від `solar.siseli.com`

Або встановити прямо за посиланням (після HACS):

### Manual

```bash
cd /config/custom_components
git clone https://github.com/yuraantonov11/ha-smart-inverter.git powmr_inverter
# Restart Home Assistant
```

---

## ⚙️ Configuration

After adding the integration, configure these optional settings:

| Setting | Default | Description |
|---|---|---|
| Poll interval | 5 seconds | Data refresh rate |
| Grid tariff | 4.32 UAH/kWh | Used for savings calculations |
| Feed-in tariff | 0.00 | Feed-in revenue rate |

### Dashboard & Automations

The `homeassistant/` folder contains ready-to-use YAML packages:
- `powmr_native_package.yaml` — template sensors, 14+ automations
- `powmr_dashboard.yaml` — Lovelace dashboard (Power + HEMS views)

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

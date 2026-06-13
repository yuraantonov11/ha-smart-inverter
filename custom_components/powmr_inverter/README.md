# Smart Solar Inverter — Home Assistant Integration

[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://hacs.xyz)
[![Version](https://img.shields.io/github/v/release/yuraantonov11/ha-smart-inverter)](https://github.com/yuraantonov11/ha-smart-inverter/releases)
![HA](https://img.shields.io/badge/Home%20Assistant-2023.6%2B-41BDF5)
[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powmr_inverter)

Custom integration for **solar inverters** with battery storage and HEMS control.
Currently supports the `solar.siseli.com` cloud platform (Inverter, SmartESS, Easun, and other brands using ECO/MAX-730 Wi-Fi modules).
Architecture is designed to support additional data sources in the future (direct inverter connection, other cloud platforms).

## ✨ Features

### Monitoring

- ⚡ 22 sensors: PV, grid, battery, load power, voltages, current, temperature
- 🔋 SOC correction (LiFePO4 16S, voltage-based with IR-drop compensation)
- 📊 Energy Dashboard ready (daily/total PV, grid import, feed-in)
- 🔍 15+ binary fault flags (overload, overheat, battery errors, BMS status)

### Control

- 🎛️ 3 dropdowns: output priority, charger priority, HEMS mode
- 🔢 5 sliders: charging currents, battery limits, grid charge power
- 🔘 6 switches: HEMS auto, grid charging, feed-in, backup, buzzer, ECO

### HEMS

- 🧠 Adaptive mode: weather-aware, tariff-smart, battery-health optimized
- 🌙 Night Arbitrage: strict time-based cost optimization
- ⛈️ Storm/Reserve: maximum backup readiness
- ☀️ Self-learning solar forecast (no panel specs needed)

### Services

`set_output_priority`, `set_charger_priority`, `set_smart_mode`, `force_grid_charge`,
`set_grid_charging`, `set_grid_feed_in`, `set_backup_mode`,
`set_battery_charge_limit`, `set_grid_charge_power`

## 📦 Installation

### HACS

1. HACS → Integrations → Custom repositories → `yuraantonov11/ha-smart-inverter`
2. Install "Smart Solar Inverter"
3. Restart HA → Settings → Devices → Add → Smart Solar
4. Enter `solar.siseli.com` email + password

### Manual

```bash
cd /config/custom_components
git clone https://github.com/yuraantonov11/ha-smart-inverter.git powmr_inverter
```

Restart HA, then add the integration from the UI.

### HA Package

Copy `homeassistant/Inverter_native_package.yaml` → `/config/packages/`  
Copy `homeassistant/Inverter_dashboard.yaml` → `/config/`

## 🔧 Requirements

- Home Assistant 2023.6+
- Active account at [solar.siseli.com](https://solar.siseli.com)
- Wi-Fi module ECO/MAX-730 connected to inverter

## 🚫 Zero dependencies

No external API keys, no REST sensors, no cloud services needed beyond the Siseli portal. Solar forecast uses built-in `sun.sun` + your weather entity. Self-learning ratio adapts automatically.

## 📄 License

MIT

| `Inverter_inverter.set_smart_mode` | Set HEMS mode (adaptive/arbitrage/storm) |
| `Inverter_inverter.force_grid_charge` | Force battery charge from grid (duration in minutes) |

## HEMS Automations

Copy the YAML files from `automations/` to your Home Assistant automations:

1. `hems_adaptive.yaml` — Adaptive mode (PV surplus + forecast)
2. `hems_arbitrage.yaml` — Night arbitrage (cheap night charging)
3. `hems_storm.yaml` — Storm/reserve mode
4. `grid_outage_alert.yaml` — Grid outage notifications + auto-storm
5. `battery_keepalive.yaml` — Periodic battery activity
6. `low_battery_alert.yaml` — Low battery notification

Also copy `configuration_template.yaml` sections to your `configuration.yaml` for input helpers, template sensors, and utility meters.

## SOC Correction (LiFePO4 16S)

The integration includes a voltage-based SOC correction algorithm for 16S LiFePO4 batteries (48V nominal) that:

- Compensates for IR-drop under load (0.0128 V/A)
- Overrides the inverter-reported SOC when it's stuck at 100% (common BMS bug without BMS cable)
- Uses a 14-point OCV lookup table calibrated for LFP chemistry

This is the same algorithm used in the Flutter app (`InverterData.getRealSoc()`).

## Development

```bash
# Clone HA dev container
git clone https://github.com/home-assistant/core.git
cd core
script/setup

# Link custom component
ln -s /path/to/this/repo config/custom_components/Inverter_inverter

# Run tests
pytest custom_components/Inverter_inverter/tests/
```

## Requirements

- Home Assistant 2024.1+
- Python 3.12+
- `aiohttp` ≥ 3.9.0
- `pycryptodome` ≥ 3.20.0

## License

MIT License — see [LICENSE](../LICENSE)

## Credits

Ported from the [Smart Inverter App](https://github.com/yuraantonov11/siseli-app) (Flutter/Dart) by Yura Antonov.

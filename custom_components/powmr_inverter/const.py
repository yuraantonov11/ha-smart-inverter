"""Constants for Smart Solar Inverter integration."""

DOMAIN = "powmr_inverter"

# API
BASE_URL = "https://solar.siseli.com"
APP_ID = "rBrTRfAPXz"
ENCRYPTED_APP_SECRET = "I4D0KRr2339z3pQ/at91V9BpFAOe54DaTafwSm6suIQ="

# Endpoints
ENDPOINT_LOGIN = "/apis/login/account"
ENDPOINT_DEVICE_LIST = "/apis/device/list"
ENDPOINT_REALTIME = "/apis/deviceState/simple/energy/flow/v1"
ENDPOINT_REALTIME_FALLBACK = "/apis/deviceState/simple/state/latest/v1"
ENDPOINT_DEVICE_CONTROL = "/apis/device/control"
ENDPOINT_DEVICE_CONFIG = "/apis/remote/device/config/write"
ENDPOINT_DEVICE_CONFIGS_READ = "/apis/remote/device/configs/read"
ENDPOINT_HISTORY = "/apis/device/history"

# Polling
DEFAULT_POLL_INTERVAL_SEC = 5
MIN_POLL_INTERVAL_SEC = 3
MAX_POLL_INTERVAL_SEC = 30

# API rate limit
MIN_REQUEST_INTERVAL_MS = 1000

# Config entry keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# ─── Select / Control constants ─────────────────────────────────────────────

OUTPUT_USB = "0"
OUTPUT_SBU = "2"

CHARGER_CSO = "0"
CHARGER_SNU = "1"
CHARGER_OSO = "2"
CHARGER_UTO = "3"

SMART_MODE_ADAPTIVE = 0
SMART_MODE_ARBITRAGE = 1
SMART_MODE_STORM = 2

# ─── Boolean display values for binary sensor classification ────────────────

EASUN_BOOLEAN_ON_DISPLAYS: frozenset[str] = frozenset({
    "On", "ON", "On Grid",
    "Open",
    "Yes",
    "Enable",
    "Active",
    "Light",
    "Alarm",
})

_EASUN_BOOLEAN_OFF_DISPLAYS: frozenset[str] = frozenset({
    "Off", "OFF", "Off Grid",
    "Close", "Closed",
    "No",
    "Disable", "Disabled",
    "Inactive",
    "Stop",
    "Standby",
    "Normal",
})

EASUN_BOOLEAN_ALL_DISPLAYS: frozenset[str] = (
    EASUN_BOOLEAN_ON_DISPLAYS | _EASUN_BOOLEAN_OFF_DISPLAYS
)

# ─── Numeric units for auto-classification ──────────────────────────────────

EASUN_NUMERIC_UNITS: frozenset[str] = frozenset({
    "W", "kW", "VA", "kVA",
    "V", "mV",
    "A", "mA", "Ah",
    "Hz",
    "%",
    "°C", "℃",
    "Wh", "kWh", "MWh",
    "min", "h", "s", "ms", "d", "day",
})

# ─── Easun sensor metadata (binary + enum overrides) ────────────────────────

EASUN_SENSOR_META: dict[str, dict] = {
    "inputVoltageTooHigh":          {"kind": "binary", "device_class": "problem"},
    "inputVoltageTooLow":           {"kind": "binary", "device_class": "problem"},
    "abnormalTemperatureSensor":    {"kind": "binary", "device_class": "problem"},
    "abnormalFanSpeed":             {"kind": "binary", "device_class": "problem"},
    "abnormalLowPVPower":           {"kind": "binary", "device_class": "problem"},
    "eepromDataAbnormality":        {"kind": "binary", "device_class": "problem"},
    "eepromReadWriteException":     {"kind": "binary", "device_class": "problem"},
    "machineOverTemperature":       {"kind": "binary", "device_class": "problem"},
    "overLoaderd":                  {"kind": "binary", "device_class": "problem"},
    "batteryNotConnected":          {"kind": "binary", "device_class": "problem"},
    "batteryVoltageHigher":         {"kind": "binary", "device_class": "problem"},
    "batteryVoltageLower":          {"kind": "binary", "device_class": "problem"},
    "batteryOpenCircuit":           {"kind": "binary", "device_class": "problem"},
    "bmsTemperatureTooHighFlag":    {"kind": "binary", "device_class": "problem"},
    "bmsLowTemperatureFlag":        {"kind": "binary", "device_class": "problem"},
    "bmsLowPowerFaultFlag":         {"kind": "binary", "device_class": "problem"},
    "bmsLowBatteryAlarmFlag":       {"kind": "binary", "device_class": "problem"},
    "bmsDischargeOvercurrentFlag":  {"kind": "binary", "device_class": "problem"},
    "bmsChargingOvercurrentSign":   {"kind": "binary", "device_class": "problem"},
    "lowBatteryAlarm":              {"kind": "binary", "device_class": "problem"},
    "outputShortCircuit":           {"kind": "binary", "device_class": "problem"},
    "pvInputShortCircuit":          {"kind": "binary", "device_class": "problem"},
    "busSoftStartFailed":           {"kind": "binary", "device_class": "problem"},
    "inverterSoftStartFailed":      {"kind": "binary", "device_class": "problem"},
    "overCurrentFault":             {"kind": "binary", "device_class": "problem"},
    "dcDcOverCurrent":              {"kind": "binary", "device_class": "problem"},
    "phaseLoss":                    {"kind": "binary", "device_class": "problem"},
    "bmsAllowChargingFlag":         {"kind": "binary", "device_class": "running"},
    "bmsAllowDischargeFlag":        {"kind": "binary", "device_class": "running"},
    "bmsCommunicationStatus":       {"kind": "binary", "device_class": "running"},
    "bmsCommunicationNormal":       {"kind": "binary", "device_class": "running"},
    "fan1Status":                   {"kind": "binary", "device_class": "running", "icon": "mdi:fan"},
    "fan2Status":                   {"kind": "binary", "device_class": "running", "icon": "mdi:fan"},
    "powerSupplyFromPVToLoadInACState": {"kind": "binary", "device_class": "running"},
    "doesTheMachineHaveAnOutput":   {"kind": "binary", "device_class": "running"},
    "mainOutputRelayStatus":        {"kind": "binary", "device_class": "running"},
    "chargingMainSwitch":           {"kind": "binary", "device_class": "running"},
    "solarChargingSwitch":          {"kind": "binary", "device_class": "running"},
    "acChargingSwitch":             {"kind": "binary", "device_class": "running"},
    "pvChargingMark":               {"kind": "binary", "device_class": "running"},
    "eco":                          {"kind": "binary", "device_class": "running"},
    "buzzerFunction":               {"kind": "binary", "device_class": "sound"},
    "dualOutputMode":               {"kind": "binary", "device_class": "running"},
    "parallelMode":                 {"kind": "binary", "device_class": "running"},
    "mpptConstantTemperatureMode":  {"kind": "binary", "device_class": "running"},
    "gridConnectionFunction":       {"kind": "binary", "device_class": "running"},
    "batteryEqualizationMode":      {"kind": "binary", "device_class": "running"},
    "overloadRestartFunction":      {"kind": "binary", "device_class": "running"},
    "overloadToBypassFunction":     {"kind": "binary", "device_class": "running"},
    "bmsCommunicationControlFunction": {"kind": "binary", "device_class": "running"},
    "liBatteryActivationFunctionSwitch": {"kind": "binary", "device_class": "running"},
    "CTFunctionSwitch":             {"kind": "binary", "device_class": "running"},
    "mainsLightStatus":             {"kind": "binary", "device_class": "light"},
    "chargingLightStatus":          {"kind": "binary", "device_class": "light"},
    "warningLightStatus":           {"kind": "binary", "device_class": "light"},
    "inverterLightStatus":          {"kind": "binary", "device_class": "light"},
    "lcdBackLighting":              {"kind": "binary", "device_class": "light"},
    "gridConnectionSign":           {"kind": "binary", "device_class": "connectivity"},
    "mainsStatus":                  {"kind": "binary", "device_class": "connectivity"},
    "workingMode": {
        "kind": "enum", "options": ["SUB", "SBU", "SUF", "PEC"], "icon": "mdi:cog",
    },
    "chargingPriorityOrder": {
        "kind": "enum", "options": ["CSO", "SNU", "OSO", "SOR"], "icon": "mdi:battery-charging",
    },
    "batteryType": {
        "kind": "enum",
        "options": ["AGM", "FLD", "USE", "LIA", "PYL", "TQF", "GRO", "LIB", "LIC", "FEL"],
        "icon": "mdi:battery",
    },
    "mainsInputRange": {"kind": "enum", "options": ["UPS", "APL"]},
    "pvEnergyFeedingPriority": {"kind": "enum", "options": ["BLU", "PVU"]},
    "mode": {
        "kind": "enum",
        "options": ["Battery Mode", "Grid Mode", "Line Mode", "Standby Mode", "Fault Mode"],
        "icon": "mdi:state-machine",
    },
    "batteryStatus": {
        "kind": "enum", "options": ["Standby", "Charge", "Discharge"], "icon": "mdi:battery",
    },
    "parallelRole": {"kind": "enum", "options": ["Switching", "Standby", "Master", "Slave"]},
    "liBatteryActivationProcess": {
        "kind": "enum", "options": ["Stop", "Activating", "Finished"],
    },
    "mainsCurrentFlowDirection": {
        "kind": "enum", "options": ["Mains To Inverter", "Inverter To Mains", "No Flow"],
    },
}

# Device info
DEVICE_MANUFACTURER = "Inverter"
DEVICE_MODEL = "SmartESS Inverter"

# Output priorities (inverter API values)
OUTPUT_USB = "0"  # Grid first
OUTPUT_SBU = "2"  # Solar/Battery first

# Charger priorities
CHARGER_CSO = "0"  # Solar first
CHARGER_SNU = "1"  # Solar + Utility
CHARGER_OSO = "2"  # Solar only
CHARGER_UTO = "3"  # Utility only

# Smart modes
SMART_MODE_ADAPTIVE = "0"
SMART_MODE_ARBITRAGE = "1"
SMART_MODE_STORM = "2"

# HEMS tunables (from HemsTunables in Dart)
DEFAULT_RESERVE_SOC = 20.0
DEFAULT_MIN_OPERATING_SOC = 30.0
DEFAULT_MID_SOC = 50.0
DEFAULT_PV_SURPLUS_ENTER_W = 250.0
DEFAULT_PV_SURPLUS_EXIT_W = 50.0
DEFAULT_MIN_MODE_HOLD_MIN = 20
DEFAULT_MANUAL_OVERRIDE_HOLD_MIN = 30
DEFAULT_COMMAND_DEDUP_WINDOW_SEC = 30

# LiFePO4 16S constants
LFP_CELLS = 16
LFP_IR_PER_CELL_MOHM = 8  # mΩ internal resistance per cell
LFP_IR_TOTAL_V_PER_A = 0.0128  # 16 × 8 mΩ = 0.128 V drop per 10A

# SOC correction (from InverterData.getRealSoc)
LFP_OCV_TABLE: list[tuple[float, float]] = [
    (54.4, 100.0),  # 3.40 V/cell
    (53.6, 95.0),   # 3.35
    (53.2, 90.0),   # 3.325
    (52.8, 80.0),   # 3.30
    (52.5, 70.0),
    (52.2, 60.0),
    (52.0, 50.0),   # LFP plateau
    (51.7, 40.0),
    (51.4, 30.0),
    (51.0, 20.0),
    (50.4, 15.0),
    (49.6, 10.0),
    (48.8, 5.0),
    (48.0, 2.0),
]

# Battery keepalive
KEEPALIVE_INTERVAL_HOURS = 2
KEEPALIVE_DURATION_SEC = 90
KEEPALIVE_MIN_SOC = 22.0

# Battery tracker
BATTERY_RATED_CYCLE_LIFE = 2000
BATTERY_LOW_THRESHOLD = 30.0
BATTERY_HIGH_THRESHOLD = 80.0

# Grid outage detection (from GridOutageDetector)
GRID_OUTAGE_VOLTAGE_THRESHOLD = 90.0
GRID_RESTORE_VOLTAGE_THRESHOLD = 130.0
GRID_CONSECUTIVE_SAMPLES = 2

# Carbon emission factor (kg CO2 per kWh) - Ukraine grid mix
CARBON_EMISSION_FACTOR = 0.42

# Storm risk
STORM_RISK_HIGH_THRESHOLD = 0.6
STORM_RISK_CLEAR_THRESHOLD = 0.4

# EWMA forecast
EWMA_ALPHA = 0.25

# Storage keys
STORAGE_ACCESS_TOKEN = "access_token"
STORAGE_USER_ID = "user_id"
STORAGE_DEVICE_SN = "device_sn"
STORAGE_STATION_ID = "station_id"
STORAGE_CURRENT_MODE = "current_mode"
STORAGE_BATTERY_CYCLES = "battery_cycles"
STORAGE_BATTERY_IN_LOW = "battery_in_low_state"
STORAGE_LOAD_PROFILE = "load_profile_ewma"
STORAGE_PV_HISTORY = "pv_history"
STORAGE_SOC_HISTORY = "soc_history"
STORAGE_LAST_CMD_OUTPUT = "last_cmd_output"
STORAGE_LAST_CMD_CHARGER = "last_cmd_charger"
STORAGE_LAST_CMD_OUTPUT_AT = "last_cmd_output_at"
STORAGE_LAST_CMD_CHARGER_AT = "last_cmd_charger_at"
STORAGE_LAST_OUTPUT_SWITCH_AT = "last_output_switch_at"
STORAGE_MANUAL_OVERRIDE_UNTIL = "manual_override_until"
STORAGE_LAST_BATTERY_ACTIVITY = "last_battery_activity"

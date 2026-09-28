"""LiFePO4 16S SOC correction via voltage.

Ported from Dart `InverterData.getRealSoc()`.
Compensates for IR-drop under load using battery current.
"""

from __future__ import annotations

# IR-drop compensation: 0.0128 V per A (16 cells × 8 mΩ internal resistance)
LFP_IR_TOTAL_V_PER_A = 0.0128

# ── OCV → SOC lookup table (16S LiFePO4, 48V nominal) ────────────
# Format: (voltage_threshold_V, soc_percent)
_OCV_TABLE: list[tuple[float, float]] = [
    (54.4, 100.0),  # 3.40 V/cell — full
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


def voltage_to_soc(voltage: float) -> float:
    """Convert battery voltage to SOC using the OCV lookup table."""
    for v_threshold, soc in _OCV_TABLE:
        if voltage >= v_threshold:
            return soc
    return 0.0  # < 48V — deep discharge


def get_real_soc(
    reported_soc: float,
    voltage: float,
    battery_current: float = 0.0,
) -> float:
    """Calculate real SOC using voltage with IR-drop compensation.

    When the inverter reports a static 100% (BMS bug for 16S LiFePO4
    without BMS cable), this function corrects it based on actual
    battery voltage.

    Args:
        reported_soc: SOC reported by inverter (0–100%).
        voltage: Battery pack voltage (V).
        battery_current: Battery current in A (>0 = charging, <0 = discharging).

    Returns:
        Corrected SOC (0–100%), clamped.
    """
    if voltage <= 10.0:
        return max(0.0, min(100.0, reported_soc))

    # IR-drop compensation
    compensated_v = voltage - battery_current * LFP_IR_TOTAL_V_PER_A

    soc_from_voltage = voltage_to_soc(compensated_v)

    reported = max(0.0, min(100.0, reported_soc))
    delta = abs(reported - soc_from_voltage)

    # If within ±10%, trust BMS; otherwise use voltage-based SOC
    if delta <= 10.0:
        return reported
    return soc_from_voltage

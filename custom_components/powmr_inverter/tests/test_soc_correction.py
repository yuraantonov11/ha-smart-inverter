"""Tests for Inverter Inverter SOC correction (LiFePO4 16S).

Mirrors test/hems_algorithm_test.dart from the Flutter app.
"""

import pytest

from custom_components.Inverter_inverter.hems.soc_correction import (
    get_real_soc,
    voltage_to_soc,
)


class TestVoltageToSoc:
    """Test OCV → SOC lookup table."""

    def test_full_battery(self):
        assert voltage_to_soc(54.4) == 100.0
        assert voltage_to_soc(55.0) == 100.0

    def test_plateau(self):
        """LFP plateau at ~3.25V/cell → 52.0V."""
        assert voltage_to_soc(52.0) == 50.0
        assert voltage_to_soc(52.1) == 50.0

    def test_low_battery(self):
        assert voltage_to_soc(50.4) == 15.0
        assert voltage_to_soc(49.6) == 10.0
        assert voltage_to_soc(48.8) == 5.0
        assert voltage_to_soc(48.0) == 2.0

    def test_deep_discharge(self):
        """Below 48V → 0%."""
        assert voltage_to_soc(47.5) == 0.0
        assert voltage_to_soc(40.0) == 0.0


class TestGetRealSoc:
    """Test SOC correction logic."""

    def test_voltage_invalid_falls_back_to_reported(self):
        """If voltage ≤ 10V, use reported SOC."""
        assert get_real_soc(85.0, 5.0, 0.0) == 85.0

    def test_reported_and_voltage_agree(self):
        """If BMS and voltage agree within ±10%, trust BMS."""
        # 54.4V → 100% from OCV. BMS says 95% → delta=5 ≤10.
        assert get_real_soc(95.0, 54.4, 0.0) == 95.0

    def test_bms_bug_100_percent_at_low_voltage(self):
        """BMS reports 100% but voltage is 52.0V → corrected to 50%."""
        # 52.0V → 50%. Delta = |100-50| = 50 > 10 → use voltage.
        result = get_real_soc(100.0, 52.0, 0.0)
        assert result == 50.0  # voltage-based, not inflated

    def test_ir_drop_compensation_discharge(self):
        """Discharging at 50A causes ~0.64V sag, compensated."""
        # At 52.0V with -50A discharge:
        # compensated = 52.0 - (-50 * 0.0128) = 52.0 + 0.64 = 52.64V
        # 52.64V → between 52.8 (80%) and 52.5 (70%) → 70%
        result = get_real_soc(100.0, 52.0, -50.0)
        assert result < 100.0  # Should be corrected down

    def test_ir_drop_compensation_charge(self):
        """Charging at 30A raises voltage, compensated."""
        # At 53.0V with +30A charge:
        # compensated = 53.0 - (30 * 0.0128) = 53.0 - 0.384 = 52.616V
        result = get_real_soc(100.0, 53.0, 30.0)
        assert result < 100.0  # Real SOC lower than reported

    def test_normal_operation_no_correction_needed(self):
        """Normal case: BMS says 60% at 52.2V → agrees."""
        # 52.2V → 60%. Delta = 0 → use reported.
        assert get_real_soc(60.0, 52.2, 0.0) == 60.0

    def test_soc_clamped(self):
        """SOC never goes outside 0-100%."""
        assert get_real_soc(150.0, 54.4, 0.0) == 100.0
        assert get_real_soc(-10.0, 52.0, 0.0) == 0.0


class TestOcvTableConsistency:
    """Ensure the OCV table is monotonically decreasing."""

    def test_thresholds_are_descending(self):
        from custom_components.Inverter_inverter.hems.soc_correction import _OCV_TABLE
        for i in range(len(_OCV_TABLE) - 1):
            assert _OCV_TABLE[i][0] > _OCV_TABLE[i + 1][0], (
                f"Threshold {_OCV_TABLE[i][0]} should be > {_OCV_TABLE[i+1][0]}"
            )

    def test_soc_values_are_descending(self):
        from custom_components.Inverter_inverter.hems.soc_correction import _OCV_TABLE
        for i in range(len(_OCV_TABLE) - 1):
            assert _OCV_TABLE[i][1] > _OCV_TABLE[i + 1][1], (
                f"SOC {_OCV_TABLE[i][1]} should be > {_OCV_TABLE[i+1][1]}"
            )

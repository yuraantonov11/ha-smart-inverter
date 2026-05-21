import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/models/inverter_data.dart';
import 'package:inverter_app/providers/app_provider.dart';
import 'package:inverter_app/utils/dashboard_diagnostics_export.dart';
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('buildDashboardDiagnosticsSnapshot includes key dashboard details', () {
    final provider = AppStateProvider()
      ..statusMessage = 'Connected to inverter'
      ..smartMode = 1
      ..useAstronomicalWindows = false
      ..manualDayStartHour = 6
      ..manualEveningStartHour = 18
      ..manualNightStartHour = 23
      ..dayTariffUahPerKwh = 4.32
      ..nightTariffUahPerKwh = 2.16
      ..nightEnergySharePercent = 35.0
      ..batteryRoundTripEfficiencyPercent = 92.0;
    final data = InverterData(
      pvPower: 1240,
      gridPower: 0,
      batteryPower: -380,
      loadPower: 860,
      batterySoc: 73.5,
      pvVoltage: 58.1,
      gridVoltage: 230.0,
      batteryVoltage: 52.4,
      loadPercentage: 41.2,
      workingMode: 'Line Mode',
      deviceSn: 'SN-12345',
      currentModeStr: 'SBU',
      rawFields: {
        'outputSourcePriority': {'value': '2'},
        'chargerSourcePriority': {'value': '1'},
      },
    );
    final text = buildDashboardDiagnosticsSnapshot(provider, data);
    expect(text, contains('Smart Inverter diagnostics snapshot'));
    expect(text, contains('Connected to inverter'));
    expect(text, contains('Device SN: SN-12345'));
    expect(text, contains('Working mode: Line Mode'));
    expect(text, contains('Current mode: SBU'));
    expect(text, contains('Control raw: output=2 charger=1'));
    expect(text, contains('Power: PV 1.2 kW'));
    expect(text, contains('Battery: SOC 73.5%'));
    expect(text, contains('HEMS: mode Night arbitrage'));
    expect(
      text,
      contains('HEMS windows: day 6:00 | evening 18:00 | night 23:00'),
    );
    expect(text, contains('Tariff forecast: active'));
  });
}

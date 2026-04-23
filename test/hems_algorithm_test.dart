import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/models/inverter_data.dart';
import 'package:inverter_app/providers/app_provider.dart';
import 'package:inverter_app/services/hems_algorithm.dart';

class _FakeAppStateProvider extends AppStateProvider {
  final List<int> setModeCalls = [];
  final List<MapEntry<String, String>> changeSettingCalls = [];

  @override
  Future<void> setMode(int mode) async {
    setModeCalls.add(mode);
  }

  @override
  Future<void> changeSetting(String key, String value) async {
    changeSettingCalls.add(MapEntry(key, value));
  }
}

InverterData _buildData({
  required double soc,
  required String outputPriority,
  required String chargerPriority,
}) {
  return InverterData(
    pvPower: 0,
    gridPower: 0,
    batteryPower: -300,
    loadPower: 500,
    batterySoc: soc,
    pvVoltage: 0,
    gridVoltage: 230,
    batteryVoltage: 51.2,
    loadPercentage: 20,
    workingMode: 'Line',
    deviceSn: 'test-sn',
    currentModeStr: 'Adaptive',
    rawFields: {
      'outputSourcePriority': {'value': outputPriority},
      'chargerSourcePriority': {'value': chargerPriority},
    },
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('HemsAlgorithmService adaptive evening reserve protection', () {
    test(
        'switches to USB in evening when SOC is near reserve even if availableEnergyWh is slightly above zero',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider);

      await service.executeAdaptiveMode(
        data: _buildData(soc: 20.2, outputPriority: '2', chargerPriority: '2'),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 1, 1, 18),
      );

      expect(provider.setModeCalls, contains(0));
    });

    test('does not immediately switch back to SBU in evening near reserve',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider);

      await service.executeAdaptiveMode(
        data: _buildData(soc: 23.0, outputPriority: '0', chargerPriority: '2'),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 1, 1, 18),
      );

      expect(provider.setModeCalls, isNot(contains(2)));
    });

    test('switches to SBU in evening when SOC is safely above reserve',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider);

      await service.executeAdaptiveMode(
        data: _buildData(soc: 40.0, outputPriority: '0', chargerPriority: '2'),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {
          18: 200,
          19: 200,
          20: 200,
          21: 200,
          22: 200,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 1, 1, 18),
      );

      expect(provider.setModeCalls, contains(2));
    });

    test('switches to USB in evening when projected deficit exists', () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider);

      await service.executeAdaptiveMode(
        data: _buildData(soc: 40.0, outputPriority: '2', chargerPriority: '2'),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {
          18: 800,
          19: 800,
          20: 800,
          21: 800,
          22: 800,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 1, 1, 18),
      );

      expect(provider.setModeCalls, contains(0));
    });
  });
}

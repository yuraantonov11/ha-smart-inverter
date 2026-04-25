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
  double pvPower = 0,
  double loadPower = 500,
  double batteryPower = -300,
}) {
  return InverterData(
    pvPower: pvPower,
    gridPower: 0,
    batteryPower: batteryPower,
    loadPower: loadPower,
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

// Fast tunables for testing: no dwell, tiny dedup window.
const _testTun = HemsTunables(
  minModeHold: Duration.zero,
  commandDedupWindow: Duration(milliseconds: 1),
  manualOverrideHold: Duration(seconds: 3),
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // ----------------------------------------------------------------
  // Original evening reserve tests (unchanged behaviour)
  // ----------------------------------------------------------------
  group('HemsAlgorithmService adaptive evening reserve protection', () {
    test(
        'switches to USB in evening when SOC is near reserve even if availableEnergyWh is slightly above zero',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

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
      final service = HemsAlgorithmService(provider, tun: _testTun);

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
      final service = HemsAlgorithmService(provider, tun: _testTun);

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
      final service = HemsAlgorithmService(provider, tun: _testTun);

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

  // ----------------------------------------------------------------
  // T1: Sunny noon — realtime surplus → SBU
  // ----------------------------------------------------------------
  group('T1: sunny noon realtime surplus', () {
    test('switches to SBU when PV surplus >= pvSurplusEnterW and SOC is ok',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 70,
          outputPriority: '0',
          chargerPriority: '2',
          pvPower: 2500,
          loadPower: 900,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );

      // Realtime surplus = 1600W > 250W threshold → must command SBU
      expect(provider.setModeCalls, contains(2));
      expect(provider.setModeCalls, isNot(contains(0)));
    });
  });

  // ----------------------------------------------------------------
  // T2: Cloudy day forecast deficit — should stay USB, not flap
  // ----------------------------------------------------------------
  group('T2: cloudy day with evening deficit', () {
    test('stays USB when surplus below exit threshold and SOC < midSoc',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 35,
          outputPriority: '0',
          chargerPriority: '2',
          pvPower: 300,
          loadPower: 900,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        // No forecast at all → big deficit expected
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 14),
      );

      // surplus = -600W < exit threshold AND soc < midSoc → USB
      expect(provider.setModeCalls, contains(0));
    });
  });

  // ----------------------------------------------------------------
  // T3: Low SOC hard safety floor
  // ----------------------------------------------------------------
  group('T3: low SOC safety', () {
    test('forces USB+SNU when SOC <= reserveSoc + 2', () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 21.5, // ≤ 22 (reserveSoc+2)
          outputPriority: '2',
          chargerPriority: '2',
          pvPower: 3000,
          loadPower: 800,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 13),
      );

      expect(provider.setModeCalls, contains(0)); // forced USB
      final chargerChange = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      expect(chargerChange, isNotEmpty); // forced SNU
    });
  });

  // ----------------------------------------------------------------
  // T4: Manual override — algorithm must not fight user
  // ----------------------------------------------------------------
  group('T4: manual override hold', () {
    test('algorithm skips output change while override is armed', () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      // User arms override (simulates tapping SBU in UI)
      service.armManualOverride(const Duration(minutes: 30));

      // Even though algorithm would prefer USB (low SOC, high load, no PV)
      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 55,
          outputPriority: '2', // user left it at SBU
          chargerPriority: '2',
          pvPower: 100,
          loadPower: 1200,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: {
          for (var h = 12; h < 23; h++) h: 1200.0,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );

      // No setMode call should have been made
      expect(provider.setModeCalls, isEmpty);
    });
  });

  // ----------------------------------------------------------------
  // T5: Dwell guard — no rapid flapping
  // ----------------------------------------------------------------
  group('T5: dwell guard prevents rapid flapping', () {
    test('second switch within minModeHold is suppressed', () async {
      final provider = _FakeAppStateProvider();
      // Use real minModeHold to test anti-flap
      const tun = HemsTunables(
        minModeHold: Duration(minutes: 20),
        commandDedupWindow: Duration(milliseconds: 1),
      );
      final service = HemsAlgorithmService(provider, tun: tun);

      // First call: surplus → SBU
      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 70,
          outputPriority: '0',
          chargerPriority: '2',
          pvPower: 2500,
          loadPower: 900,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );
      expect(provider.setModeCalls, contains(2));

      provider.setModeCalls.clear();

      // Second call immediately after: cloud transient → would want USB but dwell blocks
      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 70,
          outputPriority: '2',
          chargerPriority: '2',
          pvPower: 50, // cloud
          loadPower: 900,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12, 1), // 1 minute later
      );

      // Dwell active → no USB switch
      expect(provider.setModeCalls, isNot(contains(0)));
    });
  });

  // ----------------------------------------------------------------
  // T6: Night tariff — charges from grid when forecast deficit
  // ----------------------------------------------------------------
  group('T6: night tariff charger selection', () {
    test('enables SNU at night when tomorrow will have deficit', () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 40,
          outputPriority: '0',
          chargerPriority: '2',
          pvPower: 0,
          loadPower: 0,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: const {}, // no solar forecast → deficit
        avgHourlyConsumptionStats: {
          for (var h = 7; h < 23; h++) h: 900.0,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 2), // 02:00 night
      );

      expect(provider.setModeCalls, contains(0)); // USB night
      final chargerChange = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      expect(chargerChange, isNotEmpty); // SNU to charge
    });

    test('uses OSO at night when tomorrow has enough sun', () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      // Build rich solar forecast for tomorrow
      final forecast = <String, double>{};
      for (var h = 7; h < 19; h++) {
        forecast['2026-06-02 ${h.toString().padLeft(2, '0')}:00'] = 2000.0;
      }

      await service.executeAdaptiveMode(
        data: _buildData(
          soc: 80,
          outputPriority: '0',
          chargerPriority: '1',
          pvPower: 0,
          loadPower: 0,
        ),
        batteryCapacityAh: 230,
        hourlyForecast: forecast,
        avgHourlyConsumptionStats: {
          for (var h = 7; h < 23; h++) h: 400.0,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 23, 30), // 23:30 night
      );

      final chargerChange = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '2');
      expect(chargerChange, isNotEmpty); // OSO — no grid charge needed
    });
  });
}

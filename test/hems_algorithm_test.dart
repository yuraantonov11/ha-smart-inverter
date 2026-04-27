import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/models/hems_optimization_profile.dart';
import 'package:inverter_app/models/inverter_data.dart';
import 'package:inverter_app/providers/app_provider.dart';
import 'package:inverter_app/services/hems_algorithm.dart';
import 'package:inverter_app/services/hems_tuning_service.dart';

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

  // ----------------------------------------------------------------
  // T7: Adaptive PV surplus threshold — cloudy vs clear
  // ----------------------------------------------------------------
  group('T7: adaptive PV surplus threshold (HemsTuningService)', () {
    test('cloudy surplus history yields higher threshold than clear sky', () {
      final profile = HemsOptimizationProfile(
          systemId: 'test', pvPeakW: 3000, batteryCapacityAh: 230);
      final tuning = HemsTuningService(profile);

      // Highly variable surplus (cloudy day: strong patches alternating with clouds)
      final cloudyHistory = <double>[];
      for (var i = 0; i < 15; i++) {
        cloudyHistory.addAll([1200.0, -250.0]);
      }
      final thresholdCloudy = tuning.computeAdaptivePvSurplus(cloudyHistory);

      // Stable surplus (clear sky)
      final clearHistory = List.generate(30, (_) => 500.0);
      final thresholdClear = tuning.computeAdaptivePvSurplus(clearHistory);

      expect(thresholdCloudy, greaterThan(thresholdClear));
    });
  });

  // ----------------------------------------------------------------
  // T8: Adaptive dwell time — cloudy vs clear
  // ----------------------------------------------------------------
  group('T8: adaptive dwell time (HemsTuningService)', () {
    test('cloudy surplus history yields shorter dwell than clear sky', () {
      final profile = HemsOptimizationProfile(
          systemId: 'test', pvPeakW: 3000, batteryCapacityAh: 230);
      final tuning = HemsTuningService(profile);

      // High variance (cloudy) → stdDev > 300 → 8 min dwell (fast reaction)
      final cloudyHistory = <double>[];
      for (var i = 0; i < 15; i++) {
        cloudyHistory.addAll([1200.0, -250.0]);
      }
      final dwellCloudy = tuning.computeAdaptiveDwell(cloudyHistory);

      // Low variance (clear) → stdDev ≈ 0 → 25 min dwell (stable hold)
      final clearHistory = List.generate(30, (_) => 500.0);
      final dwellClear = tuning.computeAdaptiveDwell(clearHistory);

      expect(dwellCloudy.inMinutes, lessThan(dwellClear.inMinutes));
      expect(dwellCloudy.inMinutes, equals(8));
      expect(dwellClear.inMinutes, equals(25));
    });
  });

  // ----------------------------------------------------------------
  // T9–T10: Reserve SOC by battery age (BatteryHealthModel)
  // ----------------------------------------------------------------
  group('T9-T10: reserve SOC by battery age', () {
    test('new battery (<2y) gets lower reserve than old battery (>8y)', () {
      final youngBattery = BatteryHealthModel(
          installationDate: DateTime.now().subtract(const Duration(days: 365)));
      final oldBattery = BatteryHealthModel(
          installationDate:
              DateTime.now().subtract(const Duration(days: 365 * 9)));

      final youngReserve = youngBattery.getAdaptiveReserveSoc();
      final oldReserve = oldBattery.getAdaptiveReserveSoc();

      // <2y: agePenalty = -2 → reserve = 18%
      expect(youngReserve, lessThan(20.0));
      // >8y: agePenalty = +8 → reserve = 28%
      expect(oldReserve, greaterThanOrEqualTo(25.0));
      expect(oldReserve, greaterThan(youngReserve));
    });

    test('mid-age battery (3y) returns base reserve SOC (20%)', () {
      final midBattery = BatteryHealthModel(
          installationDate:
              DateTime.now().subtract(const Duration(days: 365 * 3)));
      final reserve = midBattery.getAdaptiveReserveSoc();
      expect(reserve, equals(20.0)); // 2–5y → agePenalty=0 → exactly base
    });
  });

  // ----------------------------------------------------------------
  // T11–T12: Astronomical time windows (summer vs winter)
  // ----------------------------------------------------------------
  group('T11-T12: astronomical time windows', () {
    test('summer 14:30 with astro windows is daytime (high surplus → SBU)',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      // Jun 21, 14:30 — summer, eveningStart ≈ 17 → 14:30 is daytime
      await service.executeAdaptiveMode(
        data: _buildData(
            soc: 70,
            outputPriority: '0',
            chargerPriority: '2',
            pvPower: 2000,
            loadPower: 500),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 21, 14, 30),
        useAstronomicalWindows: true,
        latitude: 50.45,
        longitude: 30.52,
      );

      // Realtime surplus 1500W > adaptive threshold, SOC=70 → must switch SBU
      expect(provider.setModeCalls, contains(2));
    });

    test(
        'winter 14:30 with astro windows is evening (early sunset → reserve → USB)',
        () async {
      final provider = _FakeAppStateProvider();
      final service = HemsAlgorithmService(provider, tun: _testTun);

      // Dec 21, 14:30 — winter, eveningStart ≈ 14 → 14:30 is in evening window
      // SOC near reserve: 24% with high consumption → deficit → USB safety
      await service.executeAdaptiveMode(
        data: _buildData(
            soc: 24.0,
            outputPriority: '2',
            chargerPriority: '2',
            pvPower: 200,
            loadPower: 400),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 12, 21, 14, 30),
        useAstronomicalWindows: true,
        latitude: 50.45,
        longitude: 30.52,
      );

      // Evening + forecast deficit + SOC tight → reserve protection → USB
      expect(provider.setModeCalls, contains(0));
    });
  });

  // ----------------------------------------------------------------
  // T13: Tariff-aware night charging (Phase 3a)
  // ----------------------------------------------------------------
  group('T13: tariff-aware night charging (Phase 3a)', () {
    test(
        'defers grid charge when current hour is expensive & cheap window is upcoming',
        () async {
      final provider = _FakeAppStateProvider();

      // Build custom tariff: midnight is expensive, 02:00–04:00 is cheap
      final priceMap = <DateTime, double>{
        DateTime(2026, 6, 1, 0): 4.50,
        DateTime(2026, 6, 1, 1): 4.50,
        DateTime(2026, 6, 1, 2): 1.50,
        DateTime(2026, 6, 1, 3): 1.50,
        DateTime(2026, 6, 1, 4): 1.50,
      };
      final profile = HemsOptimizationProfile(
        systemId: 'test',
        pvPeakW: 3000,
        batteryCapacityAh: 230,
        tariffForecast: TariffForecastData(pricePerKwh: priceMap),
      );
      final tuning = HemsTuningService(profile);
      final service = HemsAlgorithmService(provider,
          tun: _testTun, optimizationProfile: profile, tuningService: tuning);

      // SOC=50%, no PV, high consumption → deficit → would charge grid
      // but tariff is expensive right now → should defer
      await service.executeAdaptiveMode(
        data: _buildData(
            soc: 50,
            outputPriority: '0',
            chargerPriority: '2',
            pvPower: 0,
            loadPower: 0),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: {
          for (var h = 7; h < 23; h++) h: 900.0,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 0), // midnight, expensive tariff hour
      );

      // Must NOT activate SNU now (defer grid charge)
      final snuCalls = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      expect(snuCalls, isEmpty,
          reason: 'Grid charging should be deferred to cheap window at 02:00');

      // Must stay on OSO (solar-only / defer)
      final osoCalls = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '2');
      expect(osoCalls, isNotEmpty,
          reason: 'Should use OSO while deferring to cheap window');
    });

    test('charges from grid immediately when current tariff is cheap',
        () async {
      final provider = _FakeAppStateProvider();

      // Build custom tariff: 02:00–04:00 all cheap
      final priceMap = <DateTime, double>{
        DateTime(2026, 6, 1, 2): 1.50,
        DateTime(2026, 6, 1, 3): 1.50,
        DateTime(2026, 6, 1, 4): 1.50,
      };
      final profile = HemsOptimizationProfile(
        systemId: 'test',
        pvPeakW: 3000,
        batteryCapacityAh: 230,
        tariffForecast: TariffForecastData(pricePerKwh: priceMap),
      );
      final tuning = HemsTuningService(profile);
      final service = HemsAlgorithmService(provider,
          tun: _testTun, optimizationProfile: profile, tuningService: tuning);

      await service.executeAdaptiveMode(
        data: _buildData(
            soc: 50,
            outputPriority: '0',
            chargerPriority: '2',
            pvPower: 0,
            loadPower: 0),
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: {
          for (var h = 7; h < 23; h++) h: 900.0,
        },
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 2), // 2am, cheap tariff hour
      );

      // Current hour IS cheap → charge from grid now
      final snuCalls = provider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      expect(snuCalls, isNotEmpty,
          reason: 'Cheap tariff at 02:00 → SNU should be enabled');
    });
  });

  // ----------------------------------------------------------------
  // T14: Tunables sensitivity (threshold / reserve / dwell)
  // ----------------------------------------------------------------
  group('T14: tunables sensitivity', () {
    test('higher pvSurplusEnterW prevents SBU for borderline surplus',
        () async {
      final providerLoose = _FakeAppStateProvider();
      final providerStrict = _FakeAppStateProvider();

      const looseTun = HemsTunables(
        pvSurplusEnterW: 250,
        minModeHold: Duration.zero,
        commandDedupWindow: Duration(milliseconds: 1),
      );
      const strictTun = HemsTunables(
        pvSurplusEnterW: 500,
        minModeHold: Duration.zero,
        commandDedupWindow: Duration(milliseconds: 1),
      );

      final loose = HemsAlgorithmService(providerLoose, tun: looseTun);
      final strict = HemsAlgorithmService(providerStrict, tun: strictTun);

      final data = _buildData(
        soc: 70,
        outputPriority: '0',
        chargerPriority: '2',
        pvPower: 1100,
        loadPower: 800, // surplus = 300W
      );

      await loose.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );
      await strict.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );

      expect(providerLoose.setModeCalls, contains(2));
      expect(providerStrict.setModeCalls, isNot(contains(2)));
    });

    test('higher reserveSoc triggers earlier USB+SNU protection', () async {
      final providerLowReserve = _FakeAppStateProvider();
      final providerHighReserve = _FakeAppStateProvider();

      const lowReserveTun = HemsTunables(
        reserveSoc: 20,
        minModeHold: Duration.zero,
        commandDedupWindow: Duration(milliseconds: 1),
      );
      const highReserveTun = HemsTunables(
        reserveSoc: 30,
        minModeHold: Duration.zero,
        commandDedupWindow: Duration(milliseconds: 1),
      );

      final lowReserve =
          HemsAlgorithmService(providerLowReserve, tun: lowReserveTun);
      final highReserve =
          HemsAlgorithmService(providerHighReserve, tun: highReserveTun);

      final data = _buildData(
        soc: 28,
        outputPriority: '2',
        chargerPriority: '2',
        pvPower: 2000,
        loadPower: 500,
      );

      await lowReserve.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 13),
      );
      await highReserve.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 13),
      );

      final highReserveSnu = providerHighReserve.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      final lowReserveSnu = providerLowReserve.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');

      expect(highReserveSnu, isNotEmpty,
          reason: 'Higher reserve SOC should trigger protection at SOC=28%');
      expect(lowReserveSnu, isEmpty,
          reason: 'Lower reserve SOC should not trigger protection at SOC=28%');
    });
  });

  // ----------------------------------------------------------------
  // T15: Storm mode precharge trigger + idempotency
  // ----------------------------------------------------------------
  group('T15: storm precharge', () {
    test('executeStormMode sets USB + SNU and dedups immediate repeats',
        () async {
      final provider = _FakeAppStateProvider();
      const tun = HemsTunables(
        commandDedupWindow: Duration(minutes: 1),
      );
      final service = HemsAlgorithmService(provider, tun: tun);

      final data = _buildData(
        soc: 65,
        outputPriority: '2',
        chargerPriority: '2',
        pvPower: 1000,
        loadPower: 700,
      );

      await service.executeStormMode(data);
      await service.executeStormMode(data);

      final usbWrites = provider.setModeCalls.where((m) => m == 0).length;
      final snuWrites = provider.changeSettingCalls
          .where(
              (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1')
          .length;

      expect(usbWrites, equals(1));
      expect(snuWrites, equals(1));
    });
  });

  // ----------------------------------------------------------------
  // T16: Regression backward-compat (default profile baseline)
  // ----------------------------------------------------------------
  group('T16: backward compatibility with default profile', () {
    test('default profile keeps core daytime-surplus behavior (SBU)', () async {
      final legacyProvider = _FakeAppStateProvider();
      final profProvider = _FakeAppStateProvider();

      final legacy = HemsAlgorithmService(legacyProvider, tun: _testTun);

      final profile = HemsOptimizationProfile(
        systemId: 'compat',
        pvPeakW: 3000,
        batteryCapacityAh: 230,
      );
      final tuning = HemsTuningService(profile);
      final profiled = HemsAlgorithmService(
        profProvider,
        tun: _testTun,
        optimizationProfile: profile,
        tuningService: tuning,
      );

      final data = _buildData(
        soc: 70,
        outputPriority: '0',
        chargerPriority: '2',
        pvPower: 2200,
        loadPower: 700,
      );

      await legacy.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );
      await profiled.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 12),
      );

      expect(legacyProvider.setModeCalls, contains(2));
      expect(profProvider.setModeCalls, contains(2));
    });

    test('default profile keeps low-SOC safety protection behavior', () async {
      final legacyProvider = _FakeAppStateProvider();
      final profProvider = _FakeAppStateProvider();

      final legacy = HemsAlgorithmService(legacyProvider, tun: _testTun);

      final profile = HemsOptimizationProfile(
        systemId: 'compat',
        pvPeakW: 3000,
        batteryCapacityAh: 230,
      );
      final tuning = HemsTuningService(profile);
      final profiled = HemsAlgorithmService(
        profProvider,
        tun: _testTun,
        optimizationProfile: profile,
        tuningService: tuning,
      );

      final data = _buildData(
        soc: 19.5,
        outputPriority: '2',
        chargerPriority: '2',
        pvPower: 1800,
        loadPower: 700,
      );

      await legacy.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 13),
      );
      await profiled.executeAdaptiveMode(
        data: data,
        batteryCapacityAh: 230,
        hourlyForecast: const {},
        avgHourlyConsumptionStats: const {},
        productionCoefficient: 0.85,
        nowOverride: DateTime(2026, 6, 1, 13),
      );

      final legacySnu = legacyProvider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');
      final profSnu = profProvider.changeSettingCalls.where(
          (e) => e.key == 'chargerSourcePrioritySetting' && e.value == '1');

      expect(legacyProvider.setModeCalls, contains(0));
      expect(profProvider.setModeCalls, contains(0));
      expect(legacySnu, isNotEmpty);
      expect(profSnu, isNotEmpty);
    });
  });
}

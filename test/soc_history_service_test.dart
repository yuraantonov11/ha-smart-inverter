import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/services/soc_history_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('SocHistoryService', () {
    test(
        'keeps samples sorted when a live sample is added before persisted load',
        () async {
      final now = DateTime(2026, 5, 21, 12, 0);
      final persisted = [
        {
          't': now.subtract(const Duration(hours: 2)).millisecondsSinceEpoch,
          's': 60.0,
          'p': 500.0,
          'l': 800.0,
          'b': -120.0,
        },
        {
          't': now.subtract(const Duration(hours: 1)).millisecondsSinceEpoch,
          's': 63.0,
          'p': 650.0,
          'l': 780.0,
          'b': -80.0,
        },
      ];

      SharedPreferences.setMockInitialValues({
        'soc_history_v1': json.encode(persisted),
      });

      final service = SocHistoryService.instance;
      service.debugResetForTests();

      // Simulate startup race: fetchData() writes a fresh sample before load().
      final accepted = service.addSample(
        soc: 66.0,
        pvPower: 700.0,
        loadPower: 760.0,
        batteryPower: 40.0,
        at: now,
      );
      expect(accepted, isTrue);

      await service.load();

      final samples = service.samples;
      expect(samples.length, 3);
      expect(samples.first.timestamp, now.subtract(const Duration(hours: 2)));
      expect(samples[1].timestamp, now.subtract(const Duration(hours: 1)));
      expect(samples.last.timestamp, now);

      // Throttle must still treat the newest timestamp as the latest sample.
      final throttled = service.addSample(
        soc: 67.0,
        pvPower: 710.0,
        loadPower: 750.0,
        batteryPower: 30.0,
        at: now.add(const Duration(minutes: 1)),
      );
      expect(throttled, isFalse);
    });
  });
}

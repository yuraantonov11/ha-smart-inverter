import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/models/hems_optimization_profile.dart';
import 'package:inverter_app/models/inverter_data.dart';
void main() {
  group('TariffForecastData.getNextCheapWindow', () {
    test('returns a future cheap window relative to provided from timestamp', () {
      final base = DateTime(2026, 5, 15, 0);
      final prices = <DateTime, double>{};
      for (var h = 0; h < 24; h++) {
        final dt = DateTime(base.year, base.month, base.day, h);
        final isNight = h >= 19 && h <= 22;
        prices[dt] = isNight ? 2.16 : 4.32;
      }
      final forecast = TariffForecastData(pricePerKwh: prices);
      final next = forecast.getNextCheapWindow(
        const Duration(hours: 2),
        1.0,
        from: DateTime(2026, 5, 15, 12, 9),
      );
      expect(next, isNotNull);
      expect(next, DateTime(2026, 5, 15, 19));
    });
    test('requires contiguous cheap slots for minimum duration', () {
      final base = DateTime(2026, 5, 15, 0);
      final prices = <DateTime, double>{
        DateTime(base.year, base.month, base.day, 12): 1.0,
        DateTime(base.year, base.month, base.day, 14): 1.0,
        DateTime(base.year, base.month, base.day, 15): 4.0,
      };
      final forecast = TariffForecastData(pricePerKwh: prices);
      final next = forecast.getNextCheapWindow(
        const Duration(hours: 2),
        1.0,
        from: DateTime(2026, 5, 15, 11),
      );
      expect(next, isNull);
    });
  });
  group('InverterData current mode fallback', () {
    test('uses outputSourcePriority when explicit mode is empty', () {
      final data = InverterData.fromJson(
        {
          'deviceAttributeState': {
            'fields': {
              'outputSourcePriority': {'value': '0'},
              'workingStates': {'valueDisplay': 'Line Mode', 'value': '4'},
            }
          }
        },
        'sn-test',
        '',
      );
      expect(data.currentModeStr, 'USB');
    });
  });
}

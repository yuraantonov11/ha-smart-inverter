import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

class WeatherService {
  final Dio _dio = Dio();
  final double lat = 49.7132;
  final double lon = 23.9056;

  // Базовий коефіцієнт (якщо масив історії порожній або панелі вчора встановили)
  // 3.5 приблизно відповідає 4.5кВт системі з урахуванням втрат
  double _dynamicConversionRatio = 3.5;

  Future<Map<String, double>> fetchDynamicForecast(
      Map<String, double> historicalPvData) async {
    // ЗАПРОШУЄМО І МАЙБУТНЄ, І МИНУЛЕ (щоб було з чим порівнювати)
    final url = 'https://api.open-meteo.com/v1/forecast'
        '?latitude=$lat&longitude=$lon'
        '&hourly=shortwave_radiation'
        '&timezone=Europe%2FKyiv'
        '&past_days=3' // <--- 3 дні історії погоди
        '&forecast_days=2';

    var radiationMap = <String, double>{};
    var forecast = <String, double>{};

    try {
      final response = await _dio.get(url);
      if (response.statusCode == 200) {
        final hourly = response.data['hourly'];
        final times = hourly['time'] as List<dynamic>;
        final rads = hourly['shortwave_radiation'] as List<dynamic>;

        for (var i = 0; i < times.length; i++) {
          var timeKey = times[i].toString(); // Формат "YYYY-MM-DDTHH:00"
          radiationMap[timeKey] = (rads[i] as num).toDouble();
        }

        // =======================================================
        // АНАЛІЗ ТА НАВЧАННЯ (Зіставлення історії)
        // =======================================================
        var sumRealPvWatts = 0.0;
        var sumWeatherRad = 0.0;

        historicalPvData.forEach((timeKey, pvWatts) {
          if (radiationMap.containsKey(timeKey)) {
            var rad = radiationMap[timeKey]!;

            // Беремо лише години з яскравим сонцем, щоб уникнути похибок світанку/заходу
            if (rad > 100 && pvWatts > 50) {
              sumRealPvWatts += pvWatts;
              sumWeatherRad += rad;
            }
          }
        });

        // Якщо маємо успішні збіги, вираховуємо реальний коефіцієнт вашої станції!
        if (sumWeatherRad > 0) {
          _dynamicConversionRatio = sumRealPvWatts / sumWeatherRad;
          debugPrint(
              '🧠 Динамічний прогноз навчився! Коефіцієнт: ${_dynamicConversionRatio.toStringAsFixed(3)} (за ${historicalPvData.length} годинами)');
        }

        // =======================================================
        // ФОРМУВАННЯ ПРОГНОЗУ НА МАЙБУТНЄ
        // =======================================================
        radiationMap.forEach((timeKey, radW) {
          // Множимо прогноз радіації на наш реальний вивчений коефіцієнт
          forecast[timeKey] = radW * _dynamicConversionRatio;
        });
      }
    } catch (e) {
      debugPrint('Помилка погоди: $e');
    }
    return forecast;
  }
}

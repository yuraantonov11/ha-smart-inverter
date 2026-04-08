import 'package:dio/dio.dart';
import 'log_service.dart'; // Імпортуємо ваш сервіс логів

class WeatherService {
  // Налаштовуємо Dio з таймаутами
  final Dio _dio = Dio(BaseOptions(
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 10),
  ));

  final double lat = 49.7132;
  final double lon = 23.9056;

  double _dynamicConversionRatio = 3.5;

  Future<Map<String, double>> fetchDynamicForecast(
      Map<String, double> historicalPvData) async {
    final url = 'https://api.open-meteo.com/v1/forecast'
        '?latitude=$lat&longitude=$lon'
        '&hourly=shortwave_radiation'
        '&timezone=Europe%2FKyiv'
        '&past_days=3'
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
          final timeKey = (times[i] as String).replaceFirst('T', ' ');
          final radValue = (rads[i] as num).toDouble();
          radiationMap[timeKey] = radValue;
        }

        // --- АНАЛІЗ ТА НАВЧАННЯ ---
        var sumRealPvWatts = 0.0;
        var sumWeatherRad = 0.0;

        historicalPvData.forEach((timeKey, pvWatts) {
          if (radiationMap.containsKey(timeKey)) {
            var rad = radiationMap[timeKey]!;
            if (rad > 100 && pvWatts > 50) {
              sumRealPvWatts += pvWatts;
              sumWeatherRad += rad;
            }
          }
        });

        if (sumWeatherRad > 0) {
          _dynamicConversionRatio = sumRealPvWatts / sumWeatherRad;
          // Використовуємо LogService замість debugPrint
          LogService.log(
              '🧠 Динамічний прогноз навчився! Коефіцієнт: ${_dynamicConversionRatio.toStringAsFixed(3)} (за ${historicalPvData.length} годинами)');
        }

        // --- ФОРМУВАННЯ ПРОГНОЗУ ---
        radiationMap.forEach((timeKey, radW) {
          forecast[timeKey] = radW * _dynamicConversionRatio;
        });
      }
    } on DioException catch (e) {
      // Спеціальна обробка для помилок мережі (502, 504, таймаут)
      LogService.log('Помилка API погоди: ${e.type}', error: e.message);
    } catch (e, stack) {
      LogService.log('Непередбачена помилка в WeatherService',
          error: e, stack: stack);
    }

    return forecast;
  }
}

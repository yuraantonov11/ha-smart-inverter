import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'log_service.dart';

class WeatherService {
  final Dio _dio = Dio(BaseOptions(
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 10),
  ));

  /// Отримує прогноз від Solcast з жорстким кешуванням на 1 день
  Future<Map<String, double>> fetchSolcastForecast(
      String apiKey, String resourceId) async {
    final prefs = await SharedPreferences.getInstance();

    // Створюємо унікальний ключ кешу для поточного дня (наприклад: solcast_2026-04-10)
    final todayStr = DateTime.now().toIso8601String().substring(0, 10);
    final cacheKey = 'solcast_cache_$todayStr';

    // 1. ПЕРЕВІРКА КЕШУ
    final cachedData = prefs.getString(cacheKey);
    if (cachedData != null) {
      LogService.log('☀️ Беремо прогноз Solcast з кешу (економимо API ліміт)');
      return _parseSolcastData(json.decode(cachedData));
    }

    // 2. ЯКЩО КЕШУ НЕМАЄ АБО ДЕНЬ ЗМІНИВСЯ - РОБИМО ЗАПИТ
    if (apiKey.isEmpty || resourceId.isEmpty) {
      LogService.log(
          '⚠️ Solcast API Key або Resource ID не вказано в налаштуваннях!');
      return {};
    }

    final url =
        'https://api.solcast.com.au/rooftop_sites/$resourceId/forecasts?format=json';

    try {
      final response = await _dio.get(
        url,
        options: Options(
          headers: {'Authorization': 'Bearer $apiKey'},
        ),
      );

      if (response.statusCode == 200) {
        final responseData = response.data;

        // Зберігаємо новий прогноз у пам'ять
        await prefs.setString(cacheKey, json.encode(responseData));

        // Очищаємо старі кеші (за вчорашні дні), щоб не забивати пам'ять пристрою
        _clearOldCaches(prefs, cacheKey);

        LogService.log('✅ Успішно завантажено новий прогноз Solcast!');
        return _parseSolcastData(responseData);
      }
    } on DioException catch (e) {
      if (e.response?.statusCode == 429) {
        LogService.log('❌ Ліміт Solcast API вичерпано (429 Too Many Requests)');
      } else {
        LogService.log('Помилка API Solcast: ${e.response?.statusCode}',
            error: e.message);
      }
    } catch (e, stack) {
      LogService.log('Непередбачена помилка Solcast', error: e, stack: stack);
    }

    return {};
  }

  /// Парсер даних Solcast (перетворює kW за 30 хв у Wh)
  Map<String, double> _parseSolcastData(Map<String, dynamic> data) {
    var forecast = <String, double>{};
    final forecastsList = data['forecasts'] as List<dynamic>? ?? [];

    for (var item in forecastsList) {
      // Solcast повертає час в форматі: "2024-07-18T01:30:00.0000000Z"
      final String periodEnd = item['period_end'];

      // Відрізаємо зайве, залишаємо ключ формату "YYYY-MM-DD HH:mm" (для сумісності з вашим старим кодом)
      final timeKey = periodEnd.replaceFirst('T', ' ').substring(0, 16);

      // pv_estimate повертається в кВт (kW).
      final pvEstimateKW = (item['pv_estimate'] as num).toDouble();

      // Оскільки період PT30M (30 хвилин або 0.5 години),
      // Енергія в кВт⋅год = Потужність(кВт) * Час(год) => pvEstimateKW * 0.5
      // Енергія в Вт⋅год (Wh) = кВт⋅год * 1000 => pvEstimateKW * 500
      final pvEstimateWh = pvEstimateKW * 500.0;

      forecast[timeKey] = pvEstimateWh;
    }

    return forecast;
  }

  void _clearOldCaches(SharedPreferences prefs, String currentCacheKey) {
    final keys = prefs.getKeys();
    for (var key in keys) {
      if (key.startsWith('solcast_cache_') && key != currentCacheKey) {
        prefs.remove(key);
      }
    }
  }
}

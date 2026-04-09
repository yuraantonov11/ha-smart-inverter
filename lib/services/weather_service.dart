import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';
import 'log_service.dart';

class WeatherService {
  final Dio _dio = Dio(BaseOptions(
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 10),
  ));

  // Кеш на 3 години
  static const int cacheValidDurationHours = 3;

  /// 1. АВТОМАТИЧНИЙ РОЗРАХУНОК ККД (Калібрування)
  /// Порівнює історичну радіацію Open-Meteo з фактичною генерацією інвертора
  Future<double> calculateDynamicEfficiency({
    required Map<String, double> historicalPvData,
    required double pvCapacityW,
    double lat = 49.7115, // Пустомити
    double lon = 23.9060, // Пустомити
    double fallbackEfficiency = 0.85,
  }) async {
    // Якщо ми ще не зібрали історію генерації, або потужність 0
    if (historicalPvData.isEmpty || pvCapacityW <= 0) {
      return fallbackEfficiency;
    }

    // Запитуємо історію сонця за останні 7 днів (без прогнозу на майбутнє)
    final url = 'https://api.open-meteo.com/v1/forecast'
        '?latitude=$lat&longitude=$lon'
        '&hourly=shortwave_radiation'
        '&timezone=auto&past_days=7&forecast_days=0';

    try {
      LogService.log(
          '📊 Аналізуємо історичні дані сонця для розрахунку ККД панелей...');
      final response = await _dio.get(url);

      if (response.statusCode == 200) {
        final data = response.data;
        final List<dynamic> times = data['hourly']['time'] ?? [];
        final List<dynamic> radiationList =
            data['hourly']['shortwave_radiation'] ?? [];

        var totalTheoreticalWh = 0.0;
        var totalRealWh = 0.0;

        // Нормалізуємо ключі вашої історії у формат "Рік-Місяць-День-Година"
        // щоб легко порівнювати їх без прив'язки до мілісекунд чи хвилин
        final normalizedHistory = <String, double>{};
        historicalPvData.forEach((key, value) {
          try {
            // Парсимо ключ (напр. "2026-04-09T13:00")
            final dt = DateTime.parse(key.replaceAll(' ', 'T'));
            final normKey = '${dt.year}-${dt.month}-${dt.day}-${dt.hour}';
            normalizedHistory[normKey] = value;
          } catch (e) {
            // Ігноруємо биті ключі
          }
        });

        // Порівнюємо історію
        for (var i = 0; i < times.length; i++) {
          final timeLocal = DateTime.parse(times[i]);
          final normKey =
              '${timeLocal.year}-${timeLocal.month}-${timeLocal.day}-${timeLocal.hour}';

          final radiationWm2 = (radiationList[i] as num).toDouble();

          // Якщо в цю годину було сонце і ми маємо запис від вашого інвертора
          if (radiationWm2 > 0 && normalizedHistory.containsKey(normKey)) {
            final realWh = normalizedHistory[normKey]!;
            final theoreticalWh = (radiationWm2 / 1000.0) * pvCapacityW;

            // Важливий момент: Якщо акумулятор був заряджений на 100% і не було
            // споживання в будинку, інвертор міг "зрізати" генерацію сонця.
            // Щоб це не псувало нам статистику, беремо до уваги лише суттєву генерацію.
            if (realWh > 10.0) {
              totalRealWh += realWh;
              totalTheoreticalWh += theoreticalWh;
            }
          }
        }

        // Розрахунок підсумкового ККД
        if (totalTheoreticalWh > 0) {
          var calculatedEfficiency = totalRealWh / totalTheoreticalWh;

          // Захист від аномалій (ККД не може бути менше 10% або більше 100%)
          if (calculatedEfficiency < 0.1) calculatedEfficiency = 0.1;
          if (calculatedEfficiency > 1.0) calculatedEfficiency = 1.0;

          LogService.log(
              '✅ Успішно розраховано реальний ККД: ${(calculatedEfficiency * 100).toStringAsFixed(1)}%');
          return calculatedEfficiency;
        }
      }
    } catch (e, stack) {
      LogService.log('❌ Помилка розрахунку ККД', error: e, stack: stack);
    }

    // Якщо щось пішло не так (немає інтернету), віддаємо дефолт
    return fallbackEfficiency;
  }

  /// 2. ОТРИМАННЯ ПРОГНОЗУ НА МАЙБУТНЄ
  Future<Map<String, double>> fetchLocalForecast({
    double lat = 49.7115,
    double lon = 23.9060,
    required double pvCapacityW,
    double efficiency = 0.85,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    const cacheDataKey = 'openmeteo_cache_data';
    const cacheTimeKey = 'openmeteo_cache_timestamp';

    final cachedData = prefs.getString(cacheDataKey);
    final cacheTimestampStr = prefs.getString(cacheTimeKey);

    if (cachedData != null && cacheTimestampStr != null) {
      final cacheTimestamp = DateTime.parse(cacheTimestampStr);
      final now = DateTime.now();

      if (now.difference(cacheTimestamp).inHours < cacheValidDurationHours) {
        LogService.log('☀️ Беремо прогноз Open-Meteo з кешу');
        return _parseOpenMeteoData(
            json.decode(cachedData), pvCapacityW, efficiency);
      }
    }

    final url = 'https://api.open-meteo.com/v1/forecast'
        '?latitude=$lat&longitude=$lon'
        '&hourly=shortwave_radiation'
        '&timezone=auto&forecast_days=2';

    try {
      LogService.log('🔄 Завантажуємо свіжий прогноз радіації...');
      final response = await _dio.get(url);

      if (response.statusCode == 200) {
        final responseData = response.data;
        await prefs.setString(cacheDataKey, json.encode(responseData));
        await prefs.setString(cacheTimeKey, DateTime.now().toIso8601String());

        LogService.log('✅ Успішно завантажено прогноз Open-Meteo!');
        return _parseOpenMeteoData(responseData, pvCapacityW, efficiency);
      }
    } catch (e, stack) {
      LogService.log('❌ Помилка Open-Meteo API', error: e, stack: stack);
      if (cachedData != null) {
        return _parseOpenMeteoData(
            json.decode(cachedData), pvCapacityW, efficiency);
      }
    }

    return {};
  }

  Map<String, double> _parseOpenMeteoData(
      Map<String, dynamic> data, double pvCapacityW, double efficiency) {
    var forecast = <String, double>{};

    final List<dynamic> times = data['hourly']['time'] ?? [];
    final List<dynamic> radiationList =
        data['hourly']['shortwave_radiation'] ?? [];

    final formatter = DateFormat('yyyy-MM-dd HH:mm');

    for (var i = 0; i < times.length; i++) {
      final timeLocal = DateTime.parse(times[i]);
      final timeKey = formatter.format(timeLocal);

      final radiationWm2 = (radiationList[i] as num).toDouble();

      if (radiationWm2 > 0) {
        // Застосовуємо розрахований нами точний ККД
        final rawPowerW = (radiationWm2 / 1000.0) * pvCapacityW;
        final realPowerWh = rawPowerW * efficiency;

        forecast[timeKey] = realPowerWh;
      }
    }

    return forecast;
  }
}

import 'dart:convert';
import 'dart:math';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';
import 'log_service.dart';

// Бін радіації зі статистикою
class _RadiationBin {
  final List<double> _values = [];

  void add(double value) => _values.add(value);
  int get count => _values.length;

  double get average =>
      _values.isEmpty ? 0 : _values.reduce((a, b) => a + b) / _values.length;

  // Медіана стійкіша до аномальних днів
  double get median {
    if (_values.isEmpty) return 0;
    final sorted = List<double>.from(_values)..sort();
    final mid = sorted.length ~/ 2;
    return sorted.length.isOdd
        ? sorted[mid]
        : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  // Стандартне відхилення для виявлення аномалій
  double get stdDev {
    if (_values.length < 2) return 0;
    final avg = average;
    final variance =
        _values.map((v) => pow(v - avg, 2).toDouble()).reduce((a, b) => a + b) /
            _values.length;
    return sqrt(variance);
  }

  /// Повертає true якщо значення є аномальним (виходить за 2σ від середнього)
  bool isAnomaly(double value) {
    if (_values.length < 4) return false; // Замало даних для статистики
    final avg = average;
    final sd = stdDev;
    if (sd < 1.0) return false; // Занадто мала дисперсія
    return (value - avg).abs() > 2.0 * sd;
  }
}

class DailySolarForecast {
  final DateTime date;
  final double energyWh;
  final double peakPowerW;

  const DailySolarForecast({
    required this.date,
    required this.energyWh,
    required this.peakPowerW,
  });
}

class WeatherService {
  final Dio _dio = Dio(BaseOptions(
    baseUrl: 'https://api.open-meteo.com',
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 10),
  ));

  // БЕЗПЕКА: Rate limiting
  DateTime? _lastRequestTime;
  static const _minRequestIntervalMs = 1000; // 1 запит за секунду мінімум

  WeatherService() {
    // БЕЗПЕКА: Dio автоматично перевіряє SSL сертифікати
    // Open-Meteo використовує legitim Let's Encrypt сертифікати
  }

  /// БЕЗПЕКА: Rate limiting для запобігання DoS атакам
  Future<void> _applyRateLimit() async {
    final now = DateTime.now();
    if (_lastRequestTime != null) {
      final elapsed = now.difference(_lastRequestTime!).inMilliseconds;
      if (elapsed < _minRequestIntervalMs) {
        final delayMs = _minRequestIntervalMs - elapsed;
        LogService.log(
            '⏱️ Rate limit applied for Open-Meteo, delay=${delayMs}ms');
        await Future.delayed(Duration(milliseconds: delayMs));
      }
    }
    _lastRequestTime = DateTime.now();
  }

  double? _toDoubleOrNull(dynamic value) {
    if (value == null) return null;
    if (value is num) return value.toDouble();
    return double.tryParse(value.toString());
  }

  // Динамічний кеш: вночі можна довше, вдень — частіше
  static int get _cacheValidDurationHours {
    final hour = DateTime.now().hour;
    return (hour >= 22 || hour < 6) ? 6 : 1;
  }

  Future<Map<String, dynamic>?> _loadOpenMeteoPayload({
    required double lat,
    required double lon,
    required int forecastDays,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final cacheDataKey = 'openmeteo_cache_data_v2_$forecastDays';
    final cacheTimeKey = 'openmeteo_cache_timestamp_v2_$forecastDays';

    final cachedData = prefs.getString(cacheDataKey);
    final cacheTimestampStr = prefs.getString(cacheTimeKey);
    if (cachedData != null && cacheTimestampStr != null) {
      final cacheTimestamp = DateTime.parse(cacheTimestampStr);
      if (DateTime.now().difference(cacheTimestamp).inHours <
          _cacheValidDurationHours) {
        LogService.log('☀️ Прогноз з кешу (TTL: $_cacheValidDurationHoursг)');
        return json.decode(cachedData) as Map<String, dynamic>;
      }
    }

    final url = 'https://api.open-meteo.com/v1/forecast'
        '?latitude=$lat&longitude=$lon'
        '&hourly=shortwave_radiation'
        '&timezone=auto'
        '&past_days=14'
        '&forecast_days=$forecastDays';

    try {
      LogService.log(
          '🔄 Завантажуємо прогноз Open-Meteo (past=14, future=$forecastDays)...');

      // БЕЗПЕКА: Rate limiting
      await _applyRateLimit();

      final response = await _dio.get(url);
      if (response.statusCode == 200) {
        final responseData = response.data as Map<String, dynamic>;
        await prefs.setString(cacheDataKey, json.encode(responseData));
        await prefs.setString(cacheTimeKey, DateTime.now().toIso8601String());
        LogService.log('✅ Open-Meteo завантажено успішно');
        return responseData;
      }
    } catch (e, stack) {
      LogService.log('❌ Помилка Open-Meteo', error: e, stack: stack);
      if (cachedData != null) {
        return json.decode(cachedData) as Map<String, dynamic>;
      }
    }

    return null;
  }

  Map<int, _RadiationBin>? _buildLookupOrNull({
    required Map<String, double> historicalPvData,
    required List<String> times,
    required List<dynamic> radiationList,
  }) {
    final totalHistoricalPoints = historicalPvData.length;
    if (totalHistoricalPoints < 8) {
      LogService.log('ℹ️ Мало історичних даних ($totalHistoricalPoints точок). '
          'Використовуємо формульний прогноз.');
      return null;
    }

    final lookup = _buildRadiationLookup(
      historicalPvData: historicalPvData,
      times: times,
      radiationList: radiationList,
    );
    final validBins = lookup.values.where((b) => b.count >= 2).length;
    if (validBins < 3) {
      LogService.log(
          '⚠️ Lookup має лише $validBins валідних бінів. Використовуємо fallback.');
      return null;
    }
    return lookup;
  }

  // ─────────────────────────────────────────────────────────
  // 1. ПОБУДОВА LOOKUP-ТАБЛИЦІ (radiation → realWh)
  //    Повністю data-driven: не потребує характеристик панелей
  // ─────────────────────────────────────────────────────────
  Map<int, _RadiationBin> _buildRadiationLookup({
    required Map<String, double> historicalPvData,
    required List<String> times,
    required List<dynamic> radiationList,
  }) {
    const binSize = 50; // Крок бінування: 0–50, 50–100, ... Вт/м²
    final bins = <int, _RadiationBin>{};
    final normalizedHistory = _normalizeHistoryKeys(historicalPvData);

    var anomalyCount = 0;
    var usedCount = 0;

    for (var i = 0; i < times.length; i++) {
      final dt = DateTime.parse(times[i]).toLocal();
      final normKey = _toNormKey(dt);
      if (i >= radiationList.length) continue;
      final radiationWm2 = _toDoubleOrNull(radiationList[i]);
      if (radiationWm2 == null) continue;

      if (radiationWm2 <= 0) continue;
      if (!normalizedHistory.containsKey(normKey)) continue;

      final realWh = normalizedHistory[normKey]!;
      if (realWh <= 0) continue;

      // ── ФІЛЬТР "ЗРІЗАННЯ" ІНВЕРТОРОМ ──────────────────────────────
      // Коли АКБ заряджений на 100%, інвертор обрізає генерацію.
      // Визначаємо це відносно: якщо realWh < 25% від теоретичного максимуму
      // при суттєвій радіації — це скоріше за все "зрізання", а не реальна
      // ефективність. Такі точки псують lookup-таблицю.
      // Порогове значення теоретичного максимуму (без знання потужності панелей)
      // ми не можемо порахувати точно, але можемо виявити аномально низькі значення
      // відносно поточного біну після накопичення даних.
      final binKey = ((radiationWm2 / binSize).floor() * binSize).toInt();
      bins.putIfAbsent(binKey, () => _RadiationBin());

      // ── ВИЯВЛЕННЯ АНОМАЛІЙ (після накопичення ≥4 точок у бінів) ───
      if (bins[binKey]!.isAnomaly(realWh)) {
        anomalyCount++;
        LogService.log(
            '⚠️ Аномалія в бін $binKey W/m²: realWh=${realWh.toInt()} '
            'vs avg=${bins[binKey]!.average.toInt()} '
            '± ${bins[binKey]!.stdDev.toInt()}. Пропускаємо.');
        continue; // Не додаємо аномальну точку в бін
      }

      bins[binKey]!.add(realWh);
      usedCount++;
    }

    LogService.log('📊 Lookup побудовано: ${bins.length} бінів, '
        '$usedCount точок використано, $anomalyCount аномалій відфільтровано.');

    return bins;
  }

  // ─────────────────────────────────────────────────────────
  // 2. ПРОГНОЗ З LOOKUP + ІНТЕРПОЛЯЦІЯ
  // ─────────────────────────────────────────────────────────
  double _predictFromLookup(
    double radiationWm2,
    Map<int, _RadiationBin> lookup, {
    double fallbackEfficiency = 0.85,
    double pvCapacityW = 0,
  }) {
    if (lookup.isEmpty) {
      return pvCapacityW > 0
          ? (radiationWm2 / 1000.0) * pvCapacityW * fallbackEfficiency
          : 0;
    }

    const binSize = 50;
    final binKey = ((radiationWm2 / binSize).floor() * binSize).toInt();

    // Точне попадання в бін з достатньою кількістю даних
    // Використовуємо медіану (стійкіша до аномальних днів, ніж середнє)
    if (lookup.containsKey(binKey) && lookup[binKey]!.count >= 3) {
      return lookup[binKey]!.median;
    }

    // Знаходимо сусідні біни з достатньою кількістю точок
    final validKeys = lookup.keys.where((k) => lookup[k]!.count >= 2).toList()
      ..sort();

    if (validKeys.isEmpty) {
      return pvCapacityW > 0
          ? (radiationWm2 / 1000.0) * pvCapacityW * fallbackEfficiency
          : 0;
    }

    // Лінійна інтерполяція між двома сусідніми бінами
    int? lower, upper;
    for (final key in validKeys) {
      if (key <= binKey) lower = key;
      if (key > binKey && upper == null) upper = key;
    }

    if (lower == null) return lookup[validKeys.first]!.median;
    if (upper == null) return lookup[validKeys.last]!.median;

    final lowerVal = lookup[lower]!.median;
    final upperVal = lookup[upper]!.median;
    final t = (radiationWm2 - lower) / (upper - lower);
    return lowerVal + (upperVal - lowerVal) * t;
  }

  // ─────────────────────────────────────────────────────────
  // 3. ОСНОВНИЙ МЕТОД: ПРОГНОЗ НА МАЙБУТНЄ
  // ─────────────────────────────────────────────────────────
  Future<Map<String, double>> fetchLocalForecast({
    double lat = 49.7115,
    double lon = 23.9060,
    required double pvCapacityW,
    double efficiency = 0.85,
    Map<String, double> historicalPvData = const {},
    DateTime? targetDate, // Якщо null — повертаємо тільки майбутнє
  }) async {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final desiredDay = targetDate == null
        ? today
        : DateTime(targetDate.year, targetDate.month, targetDate.day);
    final dayDiff = desiredDay.difference(today).inDays;
    // Keep minimum of 2 days, but expand horizon for selected future date.
    final forecastDays = (dayDiff + 1).clamp(2, 16);

    final payload = await _loadOpenMeteoPayload(
      lat: lat,
      lon: lon,
      forecastDays: forecastDays,
    );
    if (payload == null) return {};
    return _buildForecast(
      payload,
      historicalPvData: historicalPvData,
      pvCapacityW: pvCapacityW,
      fallbackEfficiency: efficiency,
      targetDate: targetDate,
    );
  }

  Future<List<DailySolarForecast>> fetchDailyForecast({
    double lat = 49.7115,
    double lon = 23.9060,
    required double pvCapacityW,
    double efficiency = 0.85,
    Map<String, double> historicalPvData = const {},
    int daysAhead = 4,
  }) async {
    final forecastDays = daysAhead.clamp(2, 16);
    final payload = await _loadOpenMeteoPayload(
      lat: lat,
      lon: lon,
      forecastDays: forecastDays,
    );
    if (payload == null) return const [];

    final times = (payload['hourly']['time'] as List).cast<String>();
    final List<dynamic> radiationList =
        payload['hourly']['shortwave_radiation'];
    final lookup = _buildLookupOrNull(
      historicalPvData: historicalPvData,
      times: times,
      radiationList: radiationList,
    );

    final now = DateTime.now();
    final startDay = DateTime(now.year, now.month, now.day);
    final endDay = startDay.add(Duration(days: forecastDays - 1));
    final daily = <DateTime, List<double>>{};

    for (var i = 0; i < times.length; i++) {
      final dt = DateTime.parse(times[i]).toLocal();
      final day = DateTime(dt.year, dt.month, dt.day);
      if (day.isBefore(startDay) || day.isAfter(endDay)) continue;

      if (i >= radiationList.length) continue;
      final radiationWm2 = _toDoubleOrNull(radiationList[i]);
      if (radiationWm2 == null) continue;
      if (radiationWm2 <= 0) continue;

      double predicted;
      if (lookup != null) {
        predicted = _predictFromLookup(
          radiationWm2,
          lookup,
          fallbackEfficiency: efficiency,
          pvCapacityW: pvCapacityW,
        );
      } else {
        predicted = (radiationWm2 / 1000.0) * pvCapacityW * efficiency;
      }

      if (pvCapacityW > 0) {
        predicted = predicted.clamp(0.0, pvCapacityW).toDouble();
      }
      daily.putIfAbsent(day, () => <double>[]).add(predicted);
    }

    final result = daily.entries.map((entry) {
      final values = entry.value;
      final energyWh =
          values.fold<double>(0, (sum, value) => sum + value).toDouble();
      final peakPowerW = values.isEmpty
          ? 0.0
          : values.reduce((a, b) => a > b ? a : b).toDouble();
      return DailySolarForecast(
        date: entry.key,
        energyWh: energyWh,
        peakPowerW: peakPowerW,
      );
    }).toList()
      ..sort((a, b) => a.date.compareTo(b.date));

    return result;
  }

  // ─────────────────────────────────────────────────────────
  // 4. ПОБУДОВА ПРОГНОЗУ З ДАНИХ API
  // ─────────────────────────────────────────────────────────
  Map<String, double> _buildForecast(
    Map<String, dynamic> data, {
    required Map<String, double> historicalPvData,
    required double pvCapacityW,
    required double fallbackEfficiency,
    DateTime? targetDate,
  }) {
    final times = (data['hourly']['time'] as List).cast<String>();
    final List<dynamic> radiationList = data['hourly']['shortwave_radiation'];

    LogService.log(
        '🌤️ Початок побудови прогнозу: часових точок=${times.length}, радіаційних точок=${radiationList.length}');

    final lookup = _buildLookupOrNull(
      historicalPvData: historicalPvData,
      times: times,
      radiationList: radiationList,
    );

    final now = DateTime.now();
    final forecast = <String, double>{};
    final formatter = DateFormat('yyyy-MM-dd HH:mm');

    LogService.log('⏰ Поточний час: ${now.toIso8601String()}');
    if (targetDate != null) {
      LogService.log(
          '🎯 Цільова дата прогнозу: ${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}-${targetDate.day.toString().padLeft(2, '0')}');
    }

    var processedCount = 0;
    var includedCount = 0;

    for (var i = 0; i < times.length; i++) {
      final dt = DateTime.parse(times[i]);

      if (targetDate != null) {
        // Режим конкретної дати: повертаємо всі сонячні години для неї
        final isTargetDay = dt.year == targetDate.year &&
            dt.month == targetDate.month &&
            dt.day == targetDate.day;
        if (!isTargetDay) continue;
        // Для сьогоднішньої дати пропускаємо минулі години
        final isToday = targetDate.year == now.year &&
            targetDate.month == now.month &&
            targetDate.day == now.day;
        if (isToday && dt.isBefore(now)) continue;
      } else {
        // Режим "тільки майбутнє" (поведінка за замовчуванням)
        final isToday =
            dt.year == now.year && dt.month == now.month && dt.day == now.day;
        if (!isToday && !dt.isAfter(now)) continue;
        if (isToday && dt.isBefore(now)) continue;
      }

      processedCount++;

      if (i >= radiationList.length) continue;
      final radiationWm2 = _toDoubleOrNull(radiationList[i]);
      if (radiationWm2 == null) continue;
      if (radiationWm2 <= 0) continue;

      double predicted;
      if (lookup != null) {
        predicted = _predictFromLookup(
          radiationWm2,
          lookup,
          fallbackEfficiency: fallbackEfficiency,
          pvCapacityW: pvCapacityW,
        );
      } else {
        predicted = (radiationWm2 / 1000.0) * pvCapacityW * fallbackEfficiency;
      }

      // Обмежуємо максимальною потужністю панелей (фізична межа)
      if (pvCapacityW > 0) {
        predicted = predicted.clamp(0.0, pvCapacityW);
      }

      final timeKey = formatter.format(dt);
      forecast[timeKey] = predicted;
      includedCount++;

      if (includedCount <= 5) {
        LogService.log(
            '📊 Додано прогноз: $timeKey, радіація=${radiationWm2.toStringAsFixed(1)} W/m², передбачено=${predicted.toStringAsFixed(1)} Wh');
      }
    }

    LogService.log(
        '✅ Завершено побудову прогнозу: оброблено=$processedCount, включено=$includedCount, фінальних точок=${forecast.length}');

    return forecast;
  }

  // ─────────────────────────────────────────────────────────
  // 5. УТИЛІТИ
  // ─────────────────────────────────────────────────────────

  Map<String, double> _normalizeHistoryKeys(Map<String, double> raw) {
    final result = <String, double>{};
    for (final entry in raw.entries) {
      try {
        // Підтримуємо обидва формати: "2026-04-09T13:00" і "2026-04-09 13:00"
        final dt = DateTime.parse(entry.key.replaceAll(' ', 'T')).toLocal();
        result[_toNormKey(dt)] = entry.value;
      } catch (_) {}
    }
    return result;
  }

  String _toNormKey(DateTime dt) =>
      '${dt.year}-${dt.month}-${dt.day}-${dt.hour}';
}

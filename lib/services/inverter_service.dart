import 'dart:developer' as log_service;

import 'package:dio/dio.dart';
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/foundation.dart';
import '../models/inverter_data.dart';
import 'log_service.dart' as app_log;

class InverterService {
  final String _appId = 'rBrTRfAPXz';
  final String _encryptedAppSecret =
      'I4D0KRr2339z3pQ/at91V9BpFAOe54DaTafwSm6suIQ=';

  late final Dio _dio;
  String? accessToken;
  String? userId;
  String? deviceSn;
  String? currentStationId;
  int? currentMode;

  double dailyEnergy = 0.0;
  double totalEnergy = 0.0;
  double co2Reduction = 0.0;

  Map<String, dynamic>? _cachedFullConfigs;
  DateTime? _lastConfigFetchTime;
  bool _isConfigFetching = false;
  String? _configBatchReadId;

  late final String _appSecret = _decryptAppSecret(_appId, _encryptedAppSecret);

  InverterService() {
    _dio = Dio(BaseOptions(
      baseUrl: 'https://solar.siseli.com',
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
    ));

    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        final nonce = _generateNonce(32);
        final bodyHash = _calculateBodyHash(options.method, options.data);
        final sign = _calculateAppSign(
          appId: _appId,
          nonce: nonce,
          bodyHash: bodyHash,
          appSecret: _appSecret,
        );

        options.headers.addAll({
          'IOT-Open-AppID': _appId,
          'IOT-Open-Nonce': nonce,
          'IOT-Open-Body-Hash': bodyHash,
          'IOT-Open-Sign': sign,
          'IOT-Time-Zone': 'Europe/Kyiv',
          'Accept': 'application/json, text/plain, */*',
          'Content-Type': 'application/json; charset=utf-8',
          'IOT-Token': (accessToken?.isNotEmpty == true) ? accessToken : 'null',
        });
        return handler.next(options);
      },
      onError: (DioException e, handler) {
        log_service.log('🔴 API Error at ${e.requestOptions.path}',
            error: '${e.message} | ${e.response?.data}');
        return handler.next(e);
      },
    ));
  }

  // --- Хелпери для безпеки ---
  String _generateNonce(int length) {
    const chars =
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    final random = Random.secure();
    return String.fromCharCodes(Iterable.generate(
        length, (_) => chars.codeUnitAt(random.nextInt(chars.length))));
  }

  String _decryptAppSecret(String appId, String encryptedSecret) {
    final md5AppId = md5.convert(utf8.encode(appId)).toString().toLowerCase();
    final keyHex = md5AppId.substring(0, 16);
    final ivHex = md5AppId.substring(16);
    final key = encrypt.Key.fromUtf8(keyHex);
    final iv = encrypt.IV.fromUtf8(ivHex);
    final encrypter = encrypt.Encrypter(
        encrypt.AES(key, mode: encrypt.AESMode.cbc, padding: null));
    return encrypter
        .decrypt64(encryptedSecret, iv: iv)
        .replaceAll(RegExp(r'\x00+$'), '')
        .trim();
  }

  String _calculateBodyHash(String method, Object? body) {
    if (method.trim().toUpperCase() == 'GET' || body == null) {
      return sha256.convert(utf8.encode('{}')).toString().toLowerCase();
    }
    return sha256
        .convert(utf8.encode(body is String ? body : jsonEncode(body)))
        .toString()
        .toLowerCase();
  }

  String _calculateAppSign(
      {required String appId,
      required String appSecret,
      required String bodyHash,
      required String nonce}) {
    final payload = {
      'IOT-Open-AppID': appId,
      'IOT-Open-Body-Hash': bodyHash,
      'IOT-Open-Nonce': nonce
    };
    final sortedKeys = payload.keys.toList()..sort();
    final queryString = sortedKeys.map((k) => '$k=${payload[k]}').join('&');
    final hmac = Hmac(sha256, utf8.encode(appSecret));
    return md5
        .convert(hmac
            .convert(utf8.encode(base64.encode(utf8.encode(queryString))))
            .bytes)
        .toString()
        .toLowerCase();
  }

  // --- Основні методи API ---
  Future<bool> login(String email, String password) async {
    try {
      final passwordMd5 = (password.length == 32)
          ? password.toLowerCase()
          : md5.convert(utf8.encode(password)).toString().toLowerCase();
      final response = await _dio.post('/apis/login/account',
          data: {'account': email, 'password': passwordMd5});
      log_service.log('Login Response: ${response.data}');

      if (response.data['code'] == 0) {
        final data = response.data['data'];
        accessToken = data['accessToken'] ?? data['token'];
        userId = data['userId']?.toString();
        await _fetchDeviceList();
        return true;
      }
    } catch (e) {
      log_service.log('Login Exception', error: e);
      return false;
    }
    return false;
  }

  Future<void> _fetchDeviceList() async {
    if (userId == null) return;
    try {
      final response = await _dio.post('/apis/device/list',
          data: {'page': 1, 'count': 10, 'applyModeCategory': 1});
      if (response.data['code'] == 0 && response.data['data'] != null) {
        final devices = response.data['data']['list'] ?? [];
        if (devices.isNotEmpty) {
          final dev = devices[0];
          deviceSn = dev['id']?.toString();
          currentStationId = dev['stationId']?.toString();
          dailyEnergy = _parseDouble(dev['dailyProducedQuantity']);
          totalEnergy = _parseDouble(dev['totalProducedQuantity']);
        }
      }
    } catch (e) {
      log_service.log('Device list error', error: e);
    }
  }

  Future<bool> ensureDeviceSelected() async {
    if (deviceSn != null && deviceSn!.isNotEmpty) return true;
    await _fetchDeviceList();
    return deviceSn != null && deviceSn!.isNotEmpty;
  }

  Future<InverterData?> getRealTimeData() async {
    if (deviceSn == null) return null;

    Future<InverterData?> tryEndpoint(String endpoint) async {
      final response = await _dio.get(
        endpoint,
        queryParameters: {'deviceId': deviceSn, 'dataSource': 1},
      );
      if (response.data['code'] == 0 && response.data['data'] != null) {
        return InverterData.fromJson(
          response.data['data'] as Map<String, dynamic>,
          deviceSn!,
          currentMode?.toString() ?? '',
        );
      }
      return null;
    }

    try {
      final primary =
          await tryEndpoint('/apis/deviceState/simple/energy/flow/v1');
      if (primary != null) return primary;

      final fallback =
          await tryEndpoint('/apis/deviceState/simple/state/latest/v1');
      if (fallback != null) return fallback;

      app_log.LogService.log(
        'Realtime data empty from both endpoints for deviceId=$deviceSn',
      );
    } catch (e, stack) {
      app_log.LogService.log(
        'Realtime data error',
        error: e,
        stack: stack,
      );
      if (kDebugMode) print('Realtime data error: $e');
    }
    return null;
  }

// Оновлений гібридний метод завантаження даних
  Future<Map<String, List<FlSpot>>> getChartData(
      int range, DateTime targetDate) async {
    if (currentStationId == null || deviceSn == null) {
      return {'pv': [], 'load': [], 'grid': [], 'battery': []};
    }

    if (range == 0) {
      // ========================================================
      // 1. НОВИЙ ПІДХІД ДЛЯ ДНЯ (Телеметрія кожні 5 хв)
      // ========================================================
      final endpoint = '/apis/deviceState/simple/attribute/keys/history/v1';

      // Форматуємо дати. Визначаємо зсув часового поясу (наприклад, +03:00 або +02:00 для Києва)
      final offsetHours =
          targetDate.timeZoneOffset.inHours.toString().padLeft(2, '0');
      final offsetString = targetDate.timeZoneOffset.isNegative
          ? '-$offsetHours:00'
          : '+$offsetHours:00';

      final dayStr =
          "${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}-${targetDate.day.toString().padLeft(2, '0')}";

      final payload = {
        'deviceId': deviceSn,
        'count': 1500,
        'page': 1,
        'fromTime': '${dayStr}T00:00:00$offsetString',
        'toTime': '${dayStr}T23:59:59$offsetString',
        'orderByTimeAsc': true,
        'keys': [
          'generationPower',
          'loadPower',
          'acOutputActivePower',
          'batteryPower',
          'gridPower',
          'acInputActivePower',
          'batteryChargingCurrent',
          'batteryDischargeCurrent',
          'batteryVoltage',
          'acInputVoltage',
        ]
      };

      try {
        log_service.log('Fetching chart data for range: $range at $endpoint');
        final response = await _dio.post(endpoint, data: payload);
        if (response.data['code'] == 0) {
          return _parseHistoryData(response.data['data'] ?? {});
        }
      } catch (e) {
        log_service.log('getChartData Exception', error: e);
        return {};
      }
    } else {
      // ========================================================
      // 2. СТАРИЙ ПІДХІД ДЛЯ МІСЯЦЯ/РОКУ (Агрегація енергії кВт*год)
      // ========================================================
      var category = range == 1 ? 'monthly' : 'yearly';
      final endpoint =
          '/apis/ownerOverView/station/stateAttributeSummary/category/$category?summaryCategoryKey=pvInverterElectricityQuantityClass';

      var timeStr = range == 1
          ? "${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}"
          : '${targetDate.year}';

      try {
        final response = await _dio.post(endpoint,
            data: {'time': timeStr, 'stationId': currentStationId});
        if (response.data['code'] == 0) {
          return _parseHarChartData(response.data['data'] ?? {});
        }
      } catch (e) {
        debugPrint('Помилка статистики: $e');
      }
    }

    return {'pv': [], 'load': [], 'grid': [], 'battery': []};
  }

  /// Invalidates the config cache so the next call forces a re-fetch
  void invalidateConfigCache() {
    _lastConfigFetchTime = null;
    _cachedFullConfigs = null;
    _configBatchReadId = null;
  }

  Map<String, dynamic>? _extractConfigsFromBatch(dynamic respData) {
    if (respData is! Map<String, dynamic>) return null;

    final configStates = respData['configAttributeStates'];
    if (configStates is Map<String, dynamic> && configStates.isNotEmpty) {
      return configStates;
    }

    final target = respData['targetConfig'];
    if (target is Map<String, dynamic> && target.isNotEmpty) {
      return target;
    }

    return null;
  }

  /// Single API call — triggers the batch read or returns current result.
  /// Respects cooldown. Does NOT wait/retry.
  Future<Map<String, dynamic>?> getDeviceFullConfigs() async {
    if (deviceSn == null) return _cachedFullConfigs;

    final now = DateTime.now();
    if (_lastConfigFetchTime != null &&
        now.difference(_lastConfigFetchTime!).inSeconds < 65) {
      return _cachedFullConfigs;
    }
    if (_isConfigFetching) return _cachedFullConfigs;

    _isConfigFetching = true;
    _lastConfigFetchTime = DateTime.now(); // set cooldown up-front

    try {
      final response = await _dio.post(
        '/apis/remote/device/configs/read?deviceId=$deviceSn',
        data: {},
      );
      _isConfigFetching = false;

      if (response.statusCode == 200 && response.data['code'] == 0) {
        final respData = response.data['data'] as Map<String, dynamic>?;
        if (respData != null) {
          _configBatchReadId = respData['id']?.toString();
          final parsed = _extractConfigsFromBatch(respData);
          if (parsed != null) {
            _cachedFullConfigs = parsed;
            return parsed;
          }
          debugPrint('⏳ Config batch triggered, data pending...');
        }
      } else {
        final code = response.data['code'];
        debugPrint('⏳ Config API code=$code, will retry later...');
      }
    } catch (e) {
      _isConfigFetching = false;
      debugPrint('getDeviceFullConfigs error: $e');
    }
    return _cachedFullConfigs;
  }

  /// Background poller — no cooldown guard, call AFTER getDeviceFullConfigs
  /// returned null (batch triggered). Polls up to [maxAttempts] times.
  Future<Map<String, dynamic>?> pollForConfigsBackground({
    int maxAttempts = 12,
    Duration delay = const Duration(seconds: 5),
  }) async {
    if (deviceSn == null) return _cachedFullConfigs;

    // If batch id is missing, trigger a fresh batch read first.
    if (_configBatchReadId == null) {
      final first = await getDeviceFullConfigs();
      if (first != null) return first;
    }

    for (var i = 0; i < maxAttempts; i++) {
      await Future.delayed(delay);
      try {
        final batchId = _configBatchReadId;
        if (batchId == null || batchId.isEmpty) {
          debugPrint('⚠️ Missing batchReadId while polling configs');
          break;
        }

        final response = await _dio.get(
          '/apis/remote/device/configs/read/details',
          queryParameters: {'batchReadId': batchId},
        );

        if (response.statusCode == 200 && response.data['code'] == 0) {
          final respData = response.data['data'] as Map<String, dynamic>?;
          final parsed = _extractConfigsFromBatch(respData);
          if (parsed != null) {
            _cachedFullConfigs = parsed;
            _lastConfigFetchTime = DateTime.now();
            debugPrint('✅ Конфіги отримано (спроба ${i + 1}/$maxAttempts)');
            return parsed;
          }
        }
        debugPrint('⏳ Конфіги не готові (${i + 1}/$maxAttempts)...');
      } catch (e) {
        debugPrint('pollForConfigsBackground error: $e');
      }
    }
    return _cachedFullConfigs;
  }

  Future<bool> updateSetting(String key, String value) async {
    if (deviceSn == null) return false;
    try {
      final response = await _dio.post(
        '/apis/remote/device/config/write',
        queryParameters: {'deviceId': deviceSn},
        data: {
          'id': deviceSn, // ID пристрою
          'key': key, // Наприклад, 'buzzerSwitchSetting'
          'value': value // Значення рядком: '0' або '1'
        },
      );

      // Код 0 означає, що команда прийнята сервером і відправлена на інвертор
      return response.statusCode == 200 && response.data['code'] == 0;
    } catch (e) {
      debugPrint('Помилка запису налаштування $key: $e');
      return false;
    }
  }

// --- Збір історії для машинного навчання прогнозу ---
  Future<Map<String, double>> getHistoricalPvMapForForecast() async {
    if (deviceSn == null) return {};

    final now = DateTime.now();
    final offsetHours = now.timeZoneOffset.inHours.toString().padLeft(2, '0');
    final offsetString =
        now.timeZoneOffset.isNegative ? '-$offsetHours:00' : '+$offsetHours:00';

    // Беремо останні 3 дні для аналізу
    final fromDate = now.subtract(const Duration(days: 3));
    final fromStr =
        "${fromDate.year}-${fromDate.month.toString().padLeft(2, '0')}-${fromDate.day.toString().padLeft(2, '0')}";
    final toStr =
        "${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}";

    final payload = {
      'deviceId': deviceSn,
      'count': 1500, // Ліміт дозволяє взяти 3 дні (864 точки)
      'page': 1,
      'fromTime': '${fromStr}T00:00:00$offsetString',
      'toTime': '${toStr}T23:59:59$offsetString',
      'orderByTimeAsc': true,
      'keys': ['generationPower']
    };

    var hourlyGroups = <String, List<double>>{};

    try {
      final response = await _dio.post(
          '/apis/deviceState/simple/attribute/keys/history/v1',
          data: payload);
      if (response.data['code'] == 0) {
        final payloadData = response.data['data']['payload'];
        if (payloadData != null) {
          final timeSeries = payloadData['timeSeries'] as List<dynamic>? ?? [];
          final fields = payloadData['fields'] as Map<String, dynamic>? ?? {};
          final genPower = fields['generationPower'] as List<dynamic>? ?? [];

          for (var i = 0; i < timeSeries.length; i++) {
            if (i < genPower.length && genPower[i] != null) {
              final dt = DateTime.tryParse(timeSeries[i].toString())?.toLocal();
              if (dt != null) {
                // Групуємо 5-хвилинні дані в години. Формат як у Open-Meteo: "YYYY-MM-DDTHH:00"
                var hourKey =
                    "${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}T${dt.hour.toString().padLeft(2, '0')}:00";
                var valWatts =
                    (genPower[i] as num).toDouble() * 1000.0; // у Вати

                if (!hourlyGroups.containsKey(hourKey)) {
                  hourlyGroups[hourKey] = [];
                }
                hourlyGroups[hourKey]!.add(valWatts);
              }
            }
          }
        }
      }
    } catch (e) {
      debugPrint('HistoricalPvMapForForecast error: $e');
      return {};
    }

    // Усереднюємо дані (отримуємо середню потужність за кожну годину)
    var result = <String, double>{};
    hourlyGroups.forEach((hourKey, values) {
      var sum = values.reduce((a, b) => a + b);
      result[hourKey] = sum / values.length;
    });

    return result;
  }

  Map<String, List<FlSpot>> _parseHistoryData(Map<String, dynamic> data) {
    var pv = <FlSpot>[];
    var load = <FlSpot>[];
    var battery = <FlSpot>[];
    var grid = <FlSpot>[];

    final payload = (data['payload'] as Map<String, dynamic>?) ?? data;

    final timeSeries = payload['timeSeries'] as List<dynamic>? ?? [];
    final fields = payload['fields'] as Map<String, dynamic>? ?? {};

    log_service.log(
        '📊 _parseHistoryData: timeSeries=${timeSeries.length}, fields keys=${fields.keys.join(', ')}');

    // РОЗУМНИЙ ПОШУК: Шукає перший масив, у якому є хоча б одне не-null значення
    List<dynamic> extractList(List<String> possibleKeys) {
      for (var key in possibleKeys) {
        if (fields.containsKey(key)) {
          var list = fields[key] as List<dynamic>;
          if (list.any((element) => element != null)) {
            log_service.log(
                '✅ Використано ключ $key для ${possibleKeys.join('/')}: ${list.where((e) => e != null).length} не-null значень');
            return list;
          }
        }
      }
      log_service.log(
          '❌ Не знайдено валідних даних для ключів: ${possibleKeys.join(', ')}');
      return [];
    }

    // Витягуємо дані за пріоритетом ключів
    final genPower = extractList(['generationPower']);
    final loadPwr = extractList([
      'acOutputActivePower',
      'loadPower'
    ]); // PowMr зазвичай юзає acOutput...
    final batPower = extractList([
      'batteryPower',
      'batteryActivePower',
      'batteryChargePower',
      'batteryDischargePower'
    ]);
    final gridPwr = extractList(
        ['acInputActivePower', 'gridPower', 'utilityPower', 'acInputPower']);
    final batChargeCurrent = extractList(['batteryChargingCurrent']);
    final batDischargeCurrent = extractList(['batteryDischargeCurrent']);
    final batVoltage = extractList(['batteryVoltage']);

    log_service.log(
        '📊 Розпарсені масиви: genPower=${genPower.length}, loadPwr=${loadPwr.length}, batPower=${batPower.length}, gridPwr=${gridPwr.length}');

    for (var i = 0; i < timeSeries.length; i++) {
      final dtStr = timeSeries[i].toString();
      final dt = DateTime.tryParse(dtStr)?.toLocal();
      if (dt == null) continue;

      var xValue = dt.hour + (dt.minute / 60.0);

      // Додаємо точки ТІЛЬКИ якщо значення існує
      if (i < genPower.length && genPower[i] != null) {
        pv.add(FlSpot(xValue, (genPower[i] as num).toDouble() * 1000));
      }
      if (i < loadPwr.length && loadPwr[i] != null) {
        load.add(FlSpot(xValue, (loadPwr[i] as num).toDouble() * 1000));
      }
      double? batteryW;
      if (i < batPower.length && batPower[i] != null) {
        batteryW = (batPower[i] as num).toDouble() * 1000;
      } else if (i < batVoltage.length && batVoltage[i] != null) {
        final v = (batVoltage[i] as num).toDouble();
        final ch = i < batChargeCurrent.length && batChargeCurrent[i] != null
            ? (batChargeCurrent[i] as num).toDouble()
            : 0.0;
        final dis =
            i < batDischargeCurrent.length && batDischargeCurrent[i] != null
                ? (batDischargeCurrent[i] as num).toDouble()
                : 0.0;
        if (ch > 0) {
          batteryW = v * ch;
        } else if (dis > 0) {
          batteryW = -(v * dis);
        }
      }
      if (batteryW != null) {
        battery.add(FlSpot(xValue, batteryW));
      }

      double? gridW;
      if (i < gridPwr.length && gridPwr[i] != null) {
        gridW = (gridPwr[i] as num).toDouble() * 1000;
      } else {
        final pvW = (i < genPower.length && genPower[i] != null)
            ? (genPower[i] as num).toDouble() * 1000
            : 0.0;
        final loadW = (i < loadPwr.length && loadPwr[i] != null)
            ? (loadPwr[i] as num).toDouble() * 1000
            : 0.0;
        final batW = batteryW ?? 0.0;
        // Approximation: grid covers remaining demand when positive.
        final derivedGrid = loadW + (batW > 0 ? batW : 0.0) - pvW;
        if (derivedGrid > 0) {
          gridW = derivedGrid;
        }
      }
      if (gridW != null) {
        grid.add(FlSpot(xValue, gridW));
      }
    }

    log_service.log(
        '📊 Після парсингу: pv=${pv.length}, load=${load.length}, battery=${battery.length}, grid=${grid.length}');

    // СОРТУВАННЯ: fl_chart вимагає, щоб точки йшли строго зліва направо (за зростанням X).
    // Це захищає від крашу, якщо сервер віддав точки вперемішку або при зміні часового поясу.
    pv.sort((a, b) => a.x.compareTo(b.x));
    load.sort((a, b) => a.x.compareTo(b.x));
    battery.sort((a, b) => a.x.compareTo(b.x));
    grid.sort((a, b) => a.x.compareTo(b.x));

    return {'pv': pv, 'load': load, 'grid': grid, 'battery': battery};
  }

  Map<String, List<FlSpot>> _parseHarChartData(Map<String, dynamic> dataMap) {
    var productionData = <FlSpot>[];
    var consumptionData = <FlSpot>[];
    var batteryData = <FlSpot>[];
    var gridData = <FlSpot>[];

    final properties = dataMap['properties'] as List<dynamic>?;
    if (properties == null) {
      return {'pv': [], 'load': [], 'grid': [], 'battery': []};
    }

    for (var propItem in properties) {
      final propertyMeta = propItem['property'] as Map<String, dynamic>?;
      final timePoints = propItem['timePoints'] as List<dynamic>?;

      if (propertyMeta == null || timePoints == null) continue;

      final key = propertyMeta['key'] as String?;
      if (key == null) continue;

      final keyLower = key.toLowerCase();

      for (var i = 0; i < timePoints.length; i++) {
        final tp = timePoints[i];
        final value = (tp['value'] as num?)?.toDouble() ?? 0.0;

        // Використовуємо timeDisplay для осі X (розумне парсування)
        var timeDisp = tp['timeDisplay']?.toString() ?? '';
        var xValue = i.toDouble();

        if (timeDisp.isNotEmpty) {
          if (timeDisp.contains(':')) {
            // Розбиваємо "07:30" на години та хвилини
            final parts = timeDisp.split(':');
            final hours = double.tryParse(parts[0]) ?? i.toDouble();
            final minutes =
                parts.length > 1 ? (double.tryParse(parts[1]) ?? 0.0) : 0.0;

            // Якщо 30 хвилин, то xValue буде .5 (наприклад, 7.5)
            xValue = hours + (minutes / 60.0);
          } else {
            // Якщо формат "01", "31", "12" -> беремо як є (для місяця і року)
            xValue = double.tryParse(timeDisp) ?? i.toDouble();
          }
        }

        final spot = FlSpot(xValue, value);

        // Універсальна перевірка ключів (працює і для місяця/року, і для дня)
        if (key == 'pvGeneratedEnergy' ||
            keyLower.contains(
                'generationpower') || // <-- ДОДАНО: Ключ генерації для дня
            keyLower.contains('pvpower') ||
            keyLower.contains('pvinverterpower')) {
          productionData.add(spot);
        } else if (key == 'consumeElectricityQuantity' ||
            keyLower.contains('loadpower') ||
            keyLower.contains('usepower') ||
            keyLower.contains('consumepower')) {
          consumptionData.add(spot);
        } else if (key == 'chargeElectricityQuantity' ||
            keyLower.contains('batterypower') ||
            keyLower.contains('chargepower')) {
          batteryData.add(spot);
        } else if (key == 'buyElectricityQuantity' ||
            keyLower.contains('gridpower') ||
            keyLower.contains('buypower')) {
          gridData.add(spot);
        }
      }
    }

    return {
      'pv': productionData,
      'load': consumptionData,
      'battery': batteryData,
      'grid': gridData,
    };
  }

  Future<Map<String, dynamic>> getUserInfo(String userId) async {
    try {
      final response = await _dio
          .post('/apis/user/select/iotUserInfo?iotUserId=$userId', data: {});
      return response.data['code'] == 0 ? (response.data['data'] ?? {}) : {};
    } catch (e) {
      debugPrint('UserInfo error: $e');
    }
    return {};
  }

  // --- Керування та конфігурація ---
  Future<bool> setConfigItem(String key, String value) async {
    if (deviceSn == null) return false;
    try {
      final response = await _dio.post(
        '/apis/remote/device/config/write',
        queryParameters: {'deviceId': deviceSn},
        data: {'id': deviceSn, 'key': key, 'value': value},
      );
      return response.data['code'] == 0;
    } catch (e) {
      return false;
    }
  }

  Future<bool> setMode(int mode) async {
    var success =
        await setConfigItem('outputSourcePrioritySetting', mode.toString());
    if (success) {
      currentMode = mode;
      return true;
    }
    return false;
  }

  double _parseDouble(dynamic value) {
    if (value == null) return 0.0;
    if (value is num) return value.toDouble();
    return double.tryParse(value.toString()) ?? 0.0;
  }
}

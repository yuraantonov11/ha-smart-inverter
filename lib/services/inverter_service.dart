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

  // Energy overview cards expect kWh values.
  double dailyEnergy = 0.0;
  double totalEnergy = 0.0;
  double co2Reduction = 0.0;

  // Carbon emission factor (kg CO2 per kWh) - typical for grid electricity in Ukraine
  static const double _carbonEmissionFactorKgPerKwh = 0.42;

  // БЕЗПЕКА: Rate limiting для запобігання DoS атакам
  final Map<String, DateTime> _lastRequestTime = {};
  static const _minRequestIntervalMs = 1000; // 1 запит за секунду мінімум

  Map<String, dynamic>? _cachedFullConfigs;
  DateTime? _lastConfigFetchTime;
  DateTime? _configPollingBlockedUntil;
  DateTime? _lastConfigConnectionErrorLogAt;
  bool _isConfigFetching = false;
  String? _configBatchReadId;
  final Set<String> _loggedMissingHistoryKeys = <String>{};

  Map<String, List<FlSpot>>? _cachedDayChartData;
  String? _cachedDayChartKey;
  DateTime? _cachedDayChartAt;
  final Set<String> _realtimePostNotAllowedEndpoints = <String>{};
  DateTime? _lastRealtimeIssueLogAt;
  String? _lastRealtimeIssueSignature;
  bool _lastRealtimeOffline = false;

  bool get lastRealtimeOffline => _lastRealtimeOffline;

  // Cached monthly economics summary to avoid frequent repeated API calls.
  DateTime? _cachedMonthlySummaryAt;
  String? _cachedMonthlySummaryKey;
  ({double loadWh, double gridWh})? _cachedMonthlySummary;

  late final String _appSecret = _decryptAppSecret(_appId, _encryptedAppSecret);

  InverterService() {
    _dio = Dio(BaseOptions(
      baseUrl: 'https://solar.siseli.com',
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
    ));

    // БЕЗПЕКА: Включення SSL верифікації (за замовчуванням вже включено в Dio)
    // Dio автоматично перевіряє SSL сертифікати для HTTPS з'єднань
    _dio.options.validateStatus = (status) {
      return status != null && status < 500;
    };

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

        final headers = {
          'IOT-Open-AppID': _appId,
          'IOT-Open-Nonce': nonce,
          'IOT-Open-Body-Hash': bodyHash,
          'IOT-Open-Sign': sign,
          'IOT-Time-Zone': 'Europe/Kyiv',
          'Accept': 'application/json, text/plain, */*',
          'Content-Type': 'application/json; charset=utf-8',
        };
        if (accessToken?.isNotEmpty == true) {
          headers['IOT-Token'] = accessToken!;
        }
        options.headers.addAll(headers);
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

  /// БЕЗПЕКА: Rate limiting для запобігання DoS атакам
  /// Забезпечує мінімальний інтервал між запитами до конкретного endpoint
  Future<void> _applyRateLimit(String endpoint) async {
    final now = DateTime.now();
    final lastTime = _lastRequestTime[endpoint];

    if (lastTime != null) {
      final elapsed = now.difference(lastTime).inMilliseconds;
      if (elapsed < _minRequestIntervalMs) {
        final delayMs = _minRequestIntervalMs - elapsed;
        app_log.LogService.log(
            '⏱️ Rate limit applied for endpoint=$endpoint, delay=${delayMs}ms');
        await Future.delayed(Duration(milliseconds: delayMs));
      }
    }

    _lastRequestTime[endpoint] = DateTime.now();
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
      // БЕЗПЕКА: Rate limiting
      await _applyRateLimit('/apis/login/account');

      final passwordMd5 = (password.length == 32)
          ? password.toLowerCase()
          : md5.convert(utf8.encode(password)).toString().toLowerCase();
      final response = await _dio.post('/apis/login/account',
          data: {'account': email, 'password': passwordMd5});
      // БЕЗПЕКА: Не логуємо повну відповідь яка містить токен
      app_log.LogService.log('✅ Login successful for account: $email');

      if (response.data['code'] == 0) {
        final data = response.data['data'];
        accessToken = data['accessToken'] ?? data['token'];
        userId = data['userId']?.toString();
        await _fetchDeviceList();
        return true;
      }
    } catch (e) {
      app_log.LogService.log('❌ Login failed for account: $email', error: e);
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
          updateCo2Reduction();
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

  Map<String, dynamic>? _extractRealtimePayload(dynamic responseData) {
    if (responseData is! Map<String, dynamic>) return null;

    final data = responseData['data'];
    if (data is Map<String, dynamic>) {
      if (data['deviceAttributeState'] is Map<String, dynamic>) {
        return data;
      }
      final nestedCandidates = [
        data['payload'],
        data['deviceState'],
        data['latestState'],
      ];
      for (final candidate in nestedCandidates) {
        if (candidate is Map<String, dynamic> &&
            candidate['deviceAttributeState'] is Map<String, dynamic>) {
          return candidate;
        }
      }
    }

    // Some backends may return payload directly at root.
    if (responseData['deviceAttributeState'] is Map<String, dynamic>) {
      return responseData;
    }

    return null;
  }

  String _describeDataShape(dynamic data) {
    if (data == null) return 'null';
    if (data is Map<String, dynamic>) {
      if (data.isEmpty) return 'empty-map';
      return 'map(keys=${data.keys.take(8).join(',')})';
    }
    if (data is List) return 'list(len=${data.length})';
    return data.runtimeType.toString();
  }

  void _logRealtimeIssueThrottled(String signature, String message,
      {Duration window = const Duration(seconds: 90)}) {
    final now = DateTime.now();
    if (_lastRealtimeIssueSignature == signature &&
        _lastRealtimeIssueLogAt != null &&
        now.difference(_lastRealtimeIssueLogAt!) < window) {
      return;
    }
    _lastRealtimeIssueSignature = signature;
    _lastRealtimeIssueLogAt = now;
    app_log.LogService.log(message);
  }

  Future<InverterData?> getRealTimeData() async {
    if (deviceSn == null) return null;
    _lastRealtimeOffline = false;

    Future<InverterData?> tryEndpoint(String endpoint,
        {bool usePost = false}) async {
      final params = {'deviceId': deviceSn, 'dataSource': 1};
      if (usePost && _realtimePostNotAllowedEndpoints.contains(endpoint)) {
        return null;
      }

      Response<dynamic> response;
      try {
        // Realtime endpoints should fail fast to keep UI responsive,
        // then retry once for transient DNS/timeout hiccups.
        final realtimeOptions = Options(
          connectTimeout: const Duration(seconds: 8),
          sendTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 12),
        );
        response = usePost
            ? await _dio.post(endpoint, data: params, options: realtimeOptions)
            : await _dio.get(endpoint,
                queryParameters: params, options: realtimeOptions);
      } on DioException catch (e) {
        final statusCode = e.response?.statusCode;
        if (usePost && statusCode == 405) {
          _realtimePostNotAllowedEndpoints.add(endpoint);
          _logRealtimeIssueThrottled(
            'post405:$endpoint',
            'ℹ️ Realtime POST disabled for $endpoint (HTTP 405).',
            window: const Duration(minutes: 10),
          );
          return null;
        }

        final isTransient = e.type == DioExceptionType.connectionTimeout ||
            e.type == DioExceptionType.receiveTimeout ||
            e.type == DioExceptionType.sendTimeout ||
            e.type == DioExceptionType.connectionError;

        if (isTransient) {
          try {
            await Future.delayed(const Duration(milliseconds: 450));
            final retryOptions = Options(
              connectTimeout: const Duration(seconds: 10),
              sendTimeout: const Duration(seconds: 12),
              receiveTimeout: const Duration(seconds: 14),
            );
            response = usePost
                ? await _dio.post(endpoint, data: params, options: retryOptions)
                : await _dio.get(endpoint,
                    queryParameters: params, options: retryOptions);
          } on DioException catch (retryError) {
            _logRealtimeIssueThrottled(
              'dio:${retryError.type}:$endpoint:$usePost:${retryError.response?.statusCode}',
              'Realtime request issue: endpoint=$endpoint, method=${usePost ? 'POST' : 'GET'}, type=${retryError.type}, status=${retryError.response?.statusCode}',
            );
            return null;
          }
        } else {
          _logRealtimeIssueThrottled(
            'dio:${e.type}:$endpoint:$usePost:$statusCode',
            'Realtime request issue: endpoint=$endpoint, method=${usePost ? 'POST' : 'GET'}, type=${e.type}, status=$statusCode',
          );
          return null;
        }
      }

      final responseData = response.data as Map<String, dynamic>?;
      if (responseData == null) {
        _logRealtimeIssueThrottled(
          'invalid:$endpoint:$usePost',
          'Realtime endpoint invalid response: endpoint=$endpoint, method=${usePost ? 'POST' : 'GET'}',
        );
        return null;
      }

      final code = responseData['code'];
      final message = responseData['message'] ?? responseData['localMessage'];
      final extracted = _extractRealtimePayload(responseData);

      if (code == 0 && extracted != null) {
        _lastRealtimeOffline = false;
        return InverterData.fromJson(
          extracted,
          deviceSn!,
          currentMode?.toString() ?? '',
        );
      }

      final isOffline = code == 71000 ||
          (message?.toString().toLowerCase().contains('offline') ?? false);
      if (isOffline) {
        _lastRealtimeOffline = true;
        _logRealtimeIssueThrottled(
          'offline:$endpoint:$code',
          'ℹ️ Інвертор офлайн: endpoint=$endpoint, code=$code, message=$message',
          window: const Duration(minutes: 2),
        );
        return null;
      }

      _logRealtimeIssueThrottled(
        'empty:$endpoint:$usePost:$code',
        'Realtime endpoint empty: endpoint=$endpoint, method=${usePost ? 'POST' : 'GET'}, code=$code, message=$message, dataShape=${_describeDataShape(responseData['data'])}',
      );
      return null;
    }

    try {
      final primary =
          await tryEndpoint('/apis/deviceState/simple/energy/flow/v1');
      if (primary != null) return primary;
      final primaryPost = _lastRealtimeOffline
          ? null
          : await tryEndpoint('/apis/deviceState/simple/energy/flow/v1',
              usePost: true);
      if (primaryPost != null) return primaryPost;

      final fallback =
          await tryEndpoint('/apis/deviceState/simple/state/latest/v1');
      if (fallback != null) return fallback;
      final fallbackPost = _lastRealtimeOffline
          ? null
          : await tryEndpoint(
              '/apis/deviceState/simple/state/latest/v1',
              usePost: true,
            );
      if (fallbackPost != null) return fallbackPost;

      _logRealtimeIssueThrottled(
        'empty_all:$deviceSn',
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
      app_log.LogService.log(
          '📉 chart.fetch aborted: missing station/device (range=$range, stationId=$currentStationId, deviceSn=$deviceSn)');
      return {'pv': [], 'load': [], 'grid': [], 'battery': []};
    }

    final rangeLabel = range == 0
        ? 'day'
        : range == 1
            ? 'week'
            : 'month';
    final dateLabel =
        '${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}-${targetDate.day.toString().padLeft(2, '0')}';
    app_log.LogService.log(
        '📊 chart.fetch start: range=$rangeLabel($range), date=$dateLabel, stationId=$currentStationId, deviceSn=$deviceSn');

    if (range == 0) {
      // ========================================================
      // 1. НОВИЙ ПІДХІД ДЛЯ ДНЯ (Тел��метрія кожні 5 хв)
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

      final dayCacheKey = '${deviceSn}_$dayStr';
      final now = DateTime.now();
      if (_cachedDayChartData != null &&
          _cachedDayChartKey == dayCacheKey &&
          _cachedDayChartAt != null &&
          now.difference(_cachedDayChartAt!).inSeconds < 45) {
        return _cloneSpotsMap(_cachedDayChartData!);
      }

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
        app_log.LogService.log(
            '📡 chart.fetch day request: endpoint=$endpoint, day=$dayStr');
        final response = await _dio.post(endpoint, data: payload);
        app_log.LogService.log(
            '📡 chart.fetch day response: code=${response.data['code']}, endpoint=$endpoint');
        if (response.data['code'] == 0) {
          final parsed = _parseHistoryData(response.data['data'] ?? {});
          _logChartSummary('chart.parse day', parsed);
          _cachedDayChartData = _cloneSpotsMap(parsed);
          _cachedDayChartKey = dayCacheKey;
          _cachedDayChartAt = DateTime.now();
          return parsed;
        }
      } catch (e) {
        app_log.LogService.log('❌ chart.fetch day failed', error: e);
        return {};
      }
    } else if (range == 1) {
      // ========================================================
      // 2. ТИЖДЕНЬ: агрегуємо 7 днів із 5-хв телеметрії у Wh/день
      // ========================================================
      final weekStart = _startOfWeek(targetDate);
      final weekEnd = weekStart.add(const Duration(days: 6));

      final history = await _fetchHistoryRaw(
        from: DateTime(weekStart.year, weekStart.month, weekStart.day),
        to: DateTime(weekEnd.year, weekEnd.month, weekEnd.day, 23, 59, 59),
        count: 3500,
      );

      if (history != null) {
        final parsed = _parseWeeklyHistoryData(history, weekStart);
        if (_hasAnyChartData(parsed)) {
          _logChartSummary('chart.parse week bulk', parsed);
          return parsed;
        }
        app_log.LogService.log(
            '⚠️ chart.parse week bulk empty: weekStart=${weekStart.toIso8601String().substring(0, 10)}');
      }

      // Fallback: if bulk weekly payload is empty or rejected by backend limits,
      // fetch each day separately to guarantee week chart visibility.
      final fallback = await _buildWeeklyDataFromDailyRequests(weekStart);
      if (_hasAnyChartData(fallback)) {
        app_log.LogService.log(
            '🛟 chart.fetch week fallback used: day-by-day history aggregation');
        _logChartSummary('chart.parse week fallback', fallback);
        return fallback;
      }
      app_log.LogService.log(
          '❌ chart.fetch week no data after fallback: weekStart=${weekStart.toIso8601String().substring(0, 10)}');
    } else {
      // ========================================================
      // 3. МІСЯЦЬ: денна агрегація енергії за вибраний місяць
      // ========================================================
      const category = 'monthly';
      final endpoint =
          '/apis/ownerOverView/station/stateAttributeSummary/category/$category?summaryCategoryKey=pvInverterElectricityQuantityClass';
      final timeStr =
          "${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}";

      try {
        final response = await _dio.post(endpoint,
            data: {'time': timeStr, 'stationId': currentStationId});
        app_log.LogService.log(
            '📡 chart.fetch month response: code=${response.data['code']}, time=$timeStr');
        if (response.data['code'] == 0) {
          final parsed = _parseHarChartData(response.data['data'] ?? {});
          _logChartSummary('chart.parse month', parsed);
          return parsed;
        }
      } catch (e) {
        app_log.LogService.log('❌ chart.fetch month failed', error: e);
      }
    }

    app_log.LogService.log(
        '📉 chart.fetch empty result: range=$rangeLabel($range), date=$dateLabel');
    return {'pv': [], 'load': [], 'grid': [], 'battery': []};
  }

  Future<({double loadWh, double gridWh})?> getMonthlyEnergySummary(
      DateTime targetDate) async {
    if (currentStationId == null) return null;

    final monthKey =
        '${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}';
    final now = DateTime.now();
    if (_cachedMonthlySummary != null &&
        _cachedMonthlySummaryKey == monthKey &&
        _cachedMonthlySummaryAt != null &&
        now.difference(_cachedMonthlySummaryAt!) <
            const Duration(minutes: 20)) {
      return _cachedMonthlySummary;
    }

    final chart = await getChartData(2, targetDate);
    var loadWh = _sumSpotsWh(chart['load'] ?? const []);
    var gridWh = _sumSpotsWh(chart['grid'] ?? const []);

    // CRITICAL FIX: Validate grid <= load relationship
    // If grid > load, the fields are likely swapped from API
    // In a real system: grid_import <= load (can't import more than consuming)
    if (gridWh > loadWh && loadWh > 0 && gridWh > 0) {
      app_log.LogService.log(
          '⚠️ ENERGY DATA SWAP DETECTED: load($loadWh Wh) < grid($gridWh Wh), fields were swapped, correcting...');
      final temp = gridWh;
      gridWh = loadWh;
      loadWh = temp;
    }

    // The monthly summary endpoint uses summaryCategoryKey=pvInverterElectricityQuantityClass
    // which only returns PV generation data. If load/grid come back as zero,
    // fall back to aggregating from telemetry history (5-day chunks).
    if (loadWh <= 0 || gridWh <= 0) {
      app_log.LogService.log(
          '⚠️ monthly.summary: load/grid zeros from chart API (load=${loadWh.toStringAsFixed(0)}, grid=${gridWh.toStringAsFixed(0)}), falling back to telemetry aggregation');
      final telemetry = await _aggregateMonthFromTelemetry(targetDate);
      if (telemetry != null) {
        if (loadWh <= 0) {
          loadWh = telemetry.loadWh;
          app_log.LogService.log(
              '✅ Telemetry load: ${loadWh.toStringAsFixed(0)}Wh');
        }
        if (gridWh <= 0) {
          gridWh = telemetry.gridWh;
          app_log.LogService.log(
              '✅ Telemetry grid: ${gridWh.toStringAsFixed(0)}Wh');
        }
        app_log.LogService.log(
            '✅ monthly.summary telemetry fallback: load=${loadWh.toStringAsFixed(0)}Wh, grid=${gridWh.toStringAsFixed(0)}Wh');
      }
    }

    final summary = (loadWh: loadWh, gridWh: gridWh);
    _cachedMonthlySummary = summary;
    _cachedMonthlySummaryKey = monthKey;
    _cachedMonthlySummaryAt = now;
    return summary;
  }

  /// Aggregates load and grid Wh for the whole month by fetching telemetry
  /// in 5-day chunks (≤ 1440 points each @ 5-min cadence) — proven reliable.
  Future<({double loadWh, double gridWh})?> _aggregateMonthFromTelemetry(
      DateTime targetDate) async {
    if (deviceSn == null) return null;

    final firstDay = DateTime(targetDate.year, targetDate.month, 1);
    // Do not go beyond today for current month.
    final now = DateTime.now();
    final lastDay =
        (targetDate.year == now.year && targetDate.month == now.month)
            ? now
            : DateTime(targetDate.year, targetDate.month + 1, 0);

    var totalLoadWh = 0.0;
    var totalGridWh = 0.0;

    // Split month into 5-day windows (max ~1440 points per window at 5-min cadence).
    var chunkStart = firstDay;
    while (!chunkStart.isAfter(lastDay)) {
      final chunkEnd = chunkStart.add(const Duration(days: 5));
      final to = chunkEnd.isAfter(lastDay) ? lastDay : chunkEnd;
      final raw = await _fetchHistoryRaw(from: chunkStart, to: to, count: 1500);
      if (raw != null) {
        final energy = _extractEnergyTotalsFromHistoryPayload(raw);
        totalLoadWh += energy['load'] ?? 0.0;
        totalGridWh += energy['grid'] ?? 0.0;
      }
      chunkStart = chunkStart.add(const Duration(days: 5, seconds: 1));
    }

    // Validate grid <= load relationship even from telemetry
    if (totalGridWh > totalLoadWh && totalLoadWh > 0 && totalGridWh > 0) {
      app_log.LogService.log(
          '⚠️ ENERGY DATA SWAP DETECTED IN TELEMETRY: load($totalLoadWh Wh) < grid($totalGridWh Wh), fields were swapped, correcting...');
      final temp = totalGridWh;
      totalGridWh = totalLoadWh;
      totalLoadWh = temp;
    }

    app_log.LogService.log(
        '📊 monthly.telemetry aggregation done: load=${totalLoadWh.toStringAsFixed(0)}Wh, grid=${totalGridWh.toStringAsFixed(0)}Wh');
    return (loadWh: totalLoadWh, gridWh: totalGridWh);
  }

  Future<List<({int day, double loadWh, double gridWh})>> getMonthlyDailyEnergy(
      DateTime targetDate) async {
    if (currentStationId == null) return const [];

    final chart = await getChartData(2, targetDate);
    final loadSpots = chart['load'] ?? const <FlSpot>[];
    final gridSpots = chart['grid'] ?? const <FlSpot>[];

    final loadByDay = <int, double>{};
    final gridByDay = <int, double>{};

    for (final s in loadSpots) {
      if (!s.x.isFinite || !s.y.isFinite) continue;
      final day = s.x.round().clamp(1, 31);
      loadByDay[day] = (loadByDay[day] ?? 0.0) + s.y;
    }
    for (final s in gridSpots) {
      if (!s.x.isFinite || !s.y.isFinite) continue;
      final day = s.x.round().clamp(1, 31);
      gridByDay[day] = (gridByDay[day] ?? 0.0) + s.y;
    }

    // If load or grid per-day data is empty, fall back to telemetry aggregation.
    final hasLoad = loadByDay.values.any((v) => v > 0);
    final hasGrid = gridByDay.values.any((v) => v > 0);
    if (!hasLoad || !hasGrid) {
      app_log.LogService.log(
          '⚠️ monthly.daily: load/grid zeros from chart API, falling back to telemetry per-day');
      final perDay = await _aggregateMonthDailyFromTelemetry(targetDate);
      if (perDay.isNotEmpty) return perDay;
    }

    final days = <int>{...loadByDay.keys, ...gridByDay.keys}.toList()..sort();
    return days
        .map((d) => (
              day: d,
              loadWh: loadByDay[d] ?? 0.0,
              gridWh: gridByDay[d] ?? 0.0,
            ))
        .toList(growable: false);
  }

  bool _isNightTariffHour(
    DateTime dt, {
    required int dayStartHour,
    required int nightStartHour,
  }) {
    final dayStart = dayStartHour.clamp(0, 23);
    final nightStart = nightStartHour.clamp(0, 23);
    final hour = dt.hour;

    if (dayStart == nightStart) {
      return hour < 7 || hour >= 23;
    }

    if (dayStart < nightStart) {
      return hour < dayStart || hour >= nightStart;
    }

    return hour >= nightStart && hour < dayStart;
  }

  Future<
      ({
        double loadWh,
        double gridWh,
        double selfConsumedWh,
        double payableUah,
        double savedUah,
        List<({int day, double payableUah, double savedUah})> daily,
      })?> getMonthlyTouEconomics({
    required DateTime targetDate,
    required double dayTariffUahPerKwh,
    required double nightTariffUahPerKwh,
    required int dayStartHour,
    required int nightStartHour,
    required double batteryRoundTripEfficiency,
  }) async {
    if (deviceSn == null) return null;

    final efficiency = batteryRoundTripEfficiency.clamp(0.5, 1.0).toDouble();
    final firstDay = DateTime(targetDate.year, targetDate.month, 1);
    final now = DateTime.now();
    final lastDay =
        (targetDate.year == now.year && targetDate.month == now.month)
            ? now
            : DateTime(targetDate.year, targetDate.month + 1, 0);

    var totalLoadWh = 0.0;
    var totalGridWh = 0.0;
    var totalSelfConsumedWh = 0.0;
    var totalPayableUah = 0.0;
    var totalSavedUah = 0.0;
    var processedSamples = 0;

    final payableByDay = <int, double>{};
    final savedByDay = <int, double>{};

    var chunkStart = firstDay;
    while (!chunkStart.isAfter(lastDay)) {
      final chunkEnd = chunkStart.add(const Duration(days: 5));
      final to = chunkEnd.isAfter(lastDay) ? lastDay : chunkEnd;
      final raw = await _fetchHistoryRaw(from: chunkStart, to: to, count: 1500);
      if (raw != null) {
        final payload = (raw['payload'] as Map<String, dynamic>?) ?? raw;
        final timeSeries = payload['timeSeries'] as List<dynamic>? ?? [];
        final fields = payload['fields'] as Map<String, dynamic>? ?? {};

        List<dynamic> pick(List<String> keys) {
          for (final key in keys) {
            final value = fields[key];
            if (value is List<dynamic> &&
                value.any((element) => element != null)) {
              return value;
            }
          }
          return const [];
        }

        final genPower = pick(['generationPower']);
        final loadPower = pick(['acOutputActivePower', 'loadPower']);
        final batPower = pick([
          'batteryPower',
          'batteryActivePower',
          'batteryChargePower',
          'batteryDischargePower',
        ]);
        final gridPower =
            pick(['acInputActivePower', 'gridPower', 'acInputPower']);
        final batChargeCurrent = pick(['batteryChargingCurrent']);
        final batDischargeCurrent = pick(['batteryDischargeCurrent']);
        final batVoltage = pick(['batteryVoltage']);

        DateTime? previousDt;

        for (var i = 0; i < timeSeries.length; i++) {
          final dt = DateTime.tryParse(timeSeries[i].toString())?.toLocal();
          if (dt == null) continue;
          if (dt.year != targetDate.year || dt.month != targetDate.month) {
            previousDt = dt;
            continue;
          }

          final minutesFromPrevious = previousDt == null
              ? 5.0
              : dt.difference(previousDt).inMinutes.toDouble();
          final sampleMinutes = minutesFromPrevious <= 0
              ? 5.0
              : minutesFromPrevious > 30
                  ? 5.0
                  : minutesFromPrevious;
          final sampleHours = sampleMinutes / 60.0;
          previousDt = dt;

          final pvW = (i < genPower.length && genPower[i] != null)
              ? (genPower[i] as num).toDouble() * 1000.0
              : 0.0;
          final loadW = (i < loadPower.length && loadPower[i] != null)
              ? (loadPower[i] as num).toDouble() * 1000.0
              : 0.0;

          var batteryW = 0.0;
          if (i < batPower.length && batPower[i] != null) {
            batteryW = (batPower[i] as num).toDouble() * 1000.0;
          } else if (i < batVoltage.length && batVoltage[i] != null) {
            final voltage = (batVoltage[i] as num).toDouble();
            final chargeCurrent =
                i < batChargeCurrent.length && batChargeCurrent[i] != null
                    ? (batChargeCurrent[i] as num).toDouble()
                    : 0.0;
            final dischargeCurrent =
                i < batDischargeCurrent.length && batDischargeCurrent[i] != null
                    ? (batDischargeCurrent[i] as num).toDouble()
                    : 0.0;
            if (chargeCurrent > 0) {
              batteryW = voltage * chargeCurrent;
            } else if (dischargeCurrent > 0) {
              batteryW = -(voltage * dischargeCurrent);
            }
          }

          var gridW = 0.0;
          if (i < gridPower.length && gridPower[i] != null) {
            gridW = (gridPower[i] as num).toDouble() * 1000.0;
          }

          final totalDemand = loadW + (batteryW > 0 ? batteryW : 0.0);
          final derivedGrid = totalDemand - pvW;
          if (gridW <= 0 || (gridW > totalDemand && derivedGrid > 0)) {
            gridW = derivedGrid > 0 ? derivedGrid : 0.0;
          }

          final sampleLoadWh = loadW * sampleHours;
          final sampleGridWh = gridW * sampleHours;
          totalLoadWh += sampleLoadWh;
          totalGridWh += sampleGridWh;
          processedSamples++;

          final day = dt.day;
          final isNight = _isNightTariffHour(
            dt,
            dayStartHour: dayStartHour,
            nightStartHour: nightStartHour,
          );
          final tariff = isNight ? nightTariffUahPerKwh : dayTariffUahPerKwh;

          final payable = (sampleGridWh / 1000.0) * tariff;
          totalPayableUah += payable;
          payableByDay[day] = (payableByDay[day] ?? 0.0) + payable;

          final selfConsumedLoadW =
              (loadW - gridW).clamp(0.0, double.infinity).toDouble();
          final directSolarToLoadW = min(selfConsumedLoadW, pvW);
          final batteryDischargeToLoadW = min(
            max(0.0, selfConsumedLoadW - directSolarToLoadW),
            max(0.0, -batteryW),
          );
          final remainingSelfConsumedW = max(
            0.0,
            selfConsumedLoadW - directSolarToLoadW - batteryDischargeToLoadW,
          );
          final adjustedSelfConsumedWh = (directSolarToLoadW +
                  remainingSelfConsumedW +
                  (batteryDischargeToLoadW * efficiency)) *
              sampleHours;
          totalSelfConsumedWh += adjustedSelfConsumedWh;
          final saved = (adjustedSelfConsumedWh / 1000.0) * tariff;
          totalSavedUah += saved;
          savedByDay[day] = (savedByDay[day] ?? 0.0) + saved;
        }
      }

      chunkStart = chunkStart.add(const Duration(days: 5, seconds: 1));
    }

    if (processedSamples == 0) {
      app_log.LogService.log(
          '⚠️ monthly.tou: no telemetry samples available for month ${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}');
      return null;
    }

    final days = <int>{...payableByDay.keys, ...savedByDay.keys}.toList()
      ..sort();
    final daily = days
        .map((day) => (
              day: day,
              payableUah: payableByDay[day] ?? 0.0,
              savedUah: savedByDay[day] ?? 0.0,
            ))
        .toList(growable: false);

    app_log.LogService.log(
        '📊 monthly.tou telemetry: load=${totalLoadWh.toStringAsFixed(0)}Wh, grid=${totalGridWh.toStringAsFixed(0)}Wh, self=${totalSelfConsumedWh.toStringAsFixed(0)}Wh, payable=${totalPayableUah.toStringAsFixed(1)}UAH, saved=${totalSavedUah.toStringAsFixed(1)}UAH, batteryEff=${(efficiency * 100).toStringAsFixed(0)}%');

    return (
      loadWh: totalLoadWh,
      gridWh: totalGridWh,
      selfConsumedWh: totalSelfConsumedWh,
      payableUah: totalPayableUah,
      savedUah: totalSavedUah,
      daily: daily,
    );
  }

  /// Builds per-day load/grid Wh for the selected month using telemetry.
  /// Fetches one day at a time (max 31 API calls); called only as a fallback.
  Future<List<({int day, double loadWh, double gridWh})>>
      _aggregateMonthDailyFromTelemetry(DateTime targetDate) async {
    if (deviceSn == null) return const [];

    final firstDay = DateTime(targetDate.year, targetDate.month, 1);
    final now = DateTime.now();
    final lastDay =
        (targetDate.year == now.year && targetDate.month == now.month)
            ? DateTime(now.year, now.month, now.day)
            : DateTime(targetDate.year, targetDate.month + 1, 0);

    final results = <({int day, double loadWh, double gridWh})>[];
    var day = firstDay;
    while (!day.isAfter(lastDay)) {
      final raw = await _fetchHistoryRaw(
        from: day,
        to: DateTime(day.year, day.month, day.day, 23, 59, 59),
      );
      if (raw != null) {
        final energy = _extractEnergyTotalsFromHistoryPayload(raw);
        var dayLoadWh = energy['load'] ?? 0.0;
        var dayGridWh = energy['grid'] ?? 0.0;

        // Validate grid <= load relationship
        if (dayGridWh > dayLoadWh && dayLoadWh > 0 && dayGridWh > 0) {
          final temp = dayGridWh;
          dayGridWh = dayLoadWh;
          dayLoadWh = temp;
        }

        results.add((
          day: day.day,
          loadWh: dayLoadWh,
          gridWh: dayGridWh,
        ));
      }
      day = day.add(const Duration(days: 1));
    }
    app_log.LogService.log(
        '📊 monthly.daily telemetry: ${results.length} days aggregated');
    return results;
  }

  double _sumSpotsWh(List<FlSpot> spots) {
    if (spots.isEmpty) return 0.0;
    return spots.fold<double>(
        0.0, (sum, s) => sum + (s.y.isFinite ? s.y : 0.0));
  }

  /// Invalidates the config cache so the next call forces a re-fetch
  void invalidateConfigCache() {
    _lastConfigFetchTime = null;
    _configPollingBlockedUntil = null;
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
    if (_configPollingBlockedUntil != null &&
        now.isBefore(_configPollingBlockedUntil!)) {
      return _cachedFullConfigs;
    }
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
      if (e is DioException && e.type == DioExceptionType.connectionError) {
        _configPollingBlockedUntil =
            DateTime.now().add(const Duration(minutes: 2));
        final shouldLog = _lastConfigConnectionErrorLogAt == null ||
            DateTime.now().difference(_lastConfigConnectionErrorLogAt!) >
                const Duration(seconds: 45);
        if (shouldLog) {
          _lastConfigConnectionErrorLogAt = DateTime.now();
          debugPrint(
              '⚠️ Config fetch paused for 2 min due to connection error');
        }
      }
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
    final now = DateTime.now();
    if (_configPollingBlockedUntil != null &&
        now.isBefore(_configPollingBlockedUntil!)) {
      return _cachedFullConfigs;
    }

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
        if (e is DioException && e.type == DioExceptionType.connectionError) {
          _configPollingBlockedUntil =
              DateTime.now().add(const Duration(minutes: 2));
          final shouldLog = _lastConfigConnectionErrorLogAt == null ||
              DateTime.now().difference(_lastConfigConnectionErrorLogAt!) >
                  const Duration(seconds: 45);
          if (shouldLog) {
            _lastConfigConnectionErrorLogAt = DateTime.now();
            debugPrint(
                '⚠️ Config background polling paused for 2 min (network/DNS issue)');
          }
          break;
        }
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
    List<dynamic> extractList(
      List<String> possibleKeys, {
      required String fieldAlias,
      bool optional = false,
    }) {
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

      final signature = '$fieldAlias:${possibleKeys.join('|')}';
      if (optional) {
        if (_loggedMissingHistoryKeys.add(signature)) {
          log_service.log(
              'ℹ️ Для $fieldAlias немає валідних даних за ключами: ${possibleKeys.join(', ')}. Буде використано fallback-розрахунок.');
        }
      } else {
        log_service.log(
            '❌ Не знайдено валідних даних для ключів: ${possibleKeys.join(', ')}');
      }
      return [];
    }

    // Витягуємо дані за пріоритетом ключів
    final genPower = extractList(['generationPower'], fieldAlias: 'generation');
    final loadPwr = extractList(['acOutputActivePower', 'loadPower'],
        fieldAlias: 'load'); // PowMr зазвичай юзає acOutput...
    final batPower = extractList([
      'batteryPower',
      'batteryActivePower',
      'batteryChargePower',
      'batteryDischargePower'
    ], fieldAlias: 'batteryPower', optional: true);
    final gridPwr = extractList(
        ['acInputActivePower', 'gridPower', 'utilityPower', 'acInputPower'],
        fieldAlias: 'gridPower', optional: true);
    final batChargeCurrent = extractList(['batteryChargingCurrent'],
        fieldAlias: 'batteryChargeCurrent');
    final batDischargeCurrent = extractList(['batteryDischargeCurrent'],
        fieldAlias: 'batteryDischargeCurrent');
    final batVoltage =
        extractList(['batteryVoltage'], fieldAlias: 'batteryVoltage');

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

  Map<String, List<FlSpot>> _cloneSpotsMap(Map<String, List<FlSpot>> source) {
    return {
      'pv': List<FlSpot>.from(source['pv'] ?? const []),
      'load': List<FlSpot>.from(source['load'] ?? const []),
      'grid': List<FlSpot>.from(source['grid'] ?? const []),
      'battery': List<FlSpot>.from(source['battery'] ?? const []),
    };
  }

  Map<String, List<FlSpot>> _parseHarChartData(Map<String, dynamic> dataMap) {
    var productionData = <FlSpot>[];
    var consumptionData = <FlSpot>[];
    var batteryData = <FlSpot>[];
    var gridData = <FlSpot>[];

    final properties = dataMap['properties'] as List<dynamic>?;
    if (properties == null) {
      app_log.LogService.log('⚠️ chart.parse month: no properties in response');
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
        // API monthly summary is typically kWh -> normalize to Wh for chart.
        final valueWh = ((tp['value'] as num?)?.toDouble() ?? 0.0) * 1000.0;

        final xValue = _resolveSummaryPointX(tp, i);

        final spot = FlSpot(xValue, valueWh);

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

    final result = {
      'pv': productionData,
      'load': consumptionData,
      'battery': batteryData,
      'grid': gridData,
    };
    _logChartSummary('chart.parse summary/month-source', result);
    return result;
  }

  bool _hasAnyChartData(Map<String, List<FlSpot>> data) {
    return (data['pv']?.isNotEmpty ?? false) ||
        (data['load']?.isNotEmpty ?? false) ||
        (data['battery']?.isNotEmpty ?? false) ||
        (data['grid']?.isNotEmpty ?? false);
  }

  double _resolveSummaryPointX(Map<String, dynamic> tp, int index) {
    final timeDisplay = tp['timeDisplay']?.toString() ?? '';
    final timeRaw = tp['time']?.toString() ?? '';

    if (timeDisplay.contains(':')) {
      final parts = timeDisplay.split(':');
      final hours = double.tryParse(parts[0]);
      final minutes = parts.length > 1 ? double.tryParse(parts[1]) : 0.0;
      if (hours != null) {
        return hours + ((minutes ?? 0.0) / 60.0);
      }
    }

    final dayByDisplay = int.tryParse(timeDisplay);
    if (dayByDisplay != null && dayByDisplay > 0) {
      return dayByDisplay.toDouble();
    }

    final fromDisplayDate = DateTime.tryParse(timeDisplay);
    if (fromDisplayDate != null) {
      return fromDisplayDate.toLocal().day.toDouble();
    }

    final fromRawDate = DateTime.tryParse(timeRaw);
    if (fromRawDate != null) {
      return fromRawDate.toLocal().day.toDouble();
    }

    // Keep month axis 1-based to avoid visual right shift.
    return (index + 1).toDouble();
  }

  Future<Map<String, List<FlSpot>>> _buildWeeklyDataFromDailyRequests(
      DateTime weekStart) async {
    final pvWhByDay = List<double>.filled(7, 0.0);
    final loadWhByDay = List<double>.filled(7, 0.0);
    final batteryWhByDay = List<double>.filled(7, 0.0);
    final gridWhByDay = List<double>.filled(7, 0.0);

    for (var dayOffset = 0; dayOffset < 7; dayOffset++) {
      final day = weekStart.add(Duration(days: dayOffset));
      final raw = await _fetchHistoryRaw(
        from: day,
        to: DateTime(day.year, day.month, day.day, 23, 59, 59),
      );
      if (raw == null) continue;

      final dayEnergy = _extractEnergyTotalsFromHistoryPayload(raw);
      pvWhByDay[dayOffset] = dayEnergy['pv'] ?? 0.0;
      loadWhByDay[dayOffset] = dayEnergy['load'] ?? 0.0;
      batteryWhByDay[dayOffset] = dayEnergy['battery'] ?? 0.0;
      gridWhByDay[dayOffset] = dayEnergy['grid'] ?? 0.0;
    }

    List<FlSpot> toSpots(List<double> values) {
      return List<FlSpot>.generate(
          7, (index) => FlSpot(index.toDouble(), values[index]));
    }

    return {
      'pv': toSpots(pvWhByDay),
      'load': toSpots(loadWhByDay),
      'battery': toSpots(batteryWhByDay),
      'grid': toSpots(gridWhByDay),
    };
  }

  Map<String, double> _extractEnergyTotalsFromHistoryPayload(
      Map<String, dynamic> data) {
    final payload = (data['payload'] as Map<String, dynamic>?) ?? data;
    final timeSeries = payload['timeSeries'] as List<dynamic>? ?? [];
    final fields = payload['fields'] as Map<String, dynamic>? ?? {};

    List<dynamic> pick(List<String> keys) {
      for (final key in keys) {
        final v = fields[key];
        if (v is List<dynamic> && v.any((e) => e != null)) return v;
      }
      return const [];
    }

    final genPower = pick(['generationPower']);
    final loadPower = pick(['acOutputActivePower', 'loadPower']);
    final batPower = pick([
      'batteryPower',
      'batteryActivePower',
      'batteryChargePower',
      'batteryDischargePower',
    ]);
    final gridPower = pick(['acInputActivePower', 'gridPower', 'acInputPower']);
    final batChargeCurrent = pick(['batteryChargingCurrent']);
    final batDischargeCurrent = pick(['batteryDischargeCurrent']);
    final batVoltage = pick(['batteryVoltage']);

    var pvWh = 0.0;
    var loadWh = 0.0;
    var batteryWh = 0.0;
    var gridWh = 0.0;

    DateTime? previousDt;

    for (var i = 0; i < timeSeries.length; i++) {
      final dt = DateTime.tryParse(timeSeries[i].toString())?.toLocal();
      if (dt == null) continue;

      final minutesFromPrevious = previousDt == null
          ? 5.0
          : dt.difference(previousDt).inMinutes.toDouble();
      final sampleMinutes = minutesFromPrevious <= 0
          ? 5.0
          : minutesFromPrevious > 30
              ? 5.0
              : minutesFromPrevious;
      final sampleHours = sampleMinutes / 60.0;
      previousDt = dt;

      final pvW = (i < genPower.length && genPower[i] != null)
          ? (genPower[i] as num).toDouble() * 1000.0
          : 0.0;
      final loadW = (i < loadPower.length && loadPower[i] != null)
          ? (loadPower[i] as num).toDouble() * 1000.0
          : 0.0;

      var batteryW = 0.0;
      if (i < batPower.length && batPower[i] != null) {
        batteryW = (batPower[i] as num).toDouble() * 1000.0;
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

      var gridW = 0.0;
      if (i < gridPower.length && gridPower[i] != null) {
        gridW = (gridPower[i] as num).toDouble() * 1000.0;
      }

      // If gridW is missing or seems invalid (grid > load+battery), derive it
      final totalDemand = loadW + (batteryW > 0 ? batteryW : 0.0);
      final derivedGrid = totalDemand - pvW;

      if (gridW <= 0 || (gridW > totalDemand && derivedGrid > 0)) {
        // Use derived value if grid is missing or suspiciously high
        gridW = derivedGrid > 0 ? derivedGrid : 0.0;
      }

      pvWh += pvW * sampleHours;
      loadWh += loadW * sampleHours;
      batteryWh += batteryW * sampleHours;
      gridWh += gridW * sampleHours;
    }

    return {
      'pv': pvWh,
      'load': loadWh,
      'battery': batteryWh,
      'grid': gridWh,
    };
  }

  DateTime _startOfWeek(DateTime date) {
    final normalized = DateTime(date.year, date.month, date.day);
    return normalized.subtract(Duration(days: normalized.weekday - 1));
  }

  Future<Map<String, dynamic>?> _fetchHistoryRaw({
    required DateTime from,
    required DateTime to,
    int count = 1500,
    bool retryOnTimeout = true,
  }) async {
    if (deviceSn == null) return null;

    app_log.LogService.log(
        '📡 history.fetch start: from=${from.toIso8601String().substring(0, 10)}, to=${to.toIso8601String().substring(0, 10)}, count=$count');

    final offset = from.timeZoneOffset;
    final absHours = offset.inHours.abs().toString().padLeft(2, '0');
    final offsetString = offset.isNegative ? '-$absHours:00' : '+$absHours:00';

    String fmt(DateTime d) =>
        '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

    final payload = {
      'deviceId': deviceSn,
      'count': count,
      'page': 1,
      'fromTime': '${fmt(from)}T00:00:00$offsetString',
      'toTime': '${fmt(to)}T23:59:59$offsetString',
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
      ],
    };

    try {
      final response = await _dio.post(
        '/apis/deviceState/simple/attribute/keys/history/v1',
        data: payload,
        options: Options(
          connectTimeout: const Duration(seconds: 25),
          sendTimeout: const Duration(seconds: 25),
          receiveTimeout: const Duration(seconds: 35),
        ),
      );
      if (response.data['code'] == 0) {
        final parsed = response.data['data'] as Map<String, dynamic>?;
        final payload = parsed?['payload'] as Map<String, dynamic>?;
        final tsLen = (payload?['timeSeries'] as List<dynamic>?)?.length ??
            (parsed?['timeSeries'] as List<dynamic>?)?.length ??
            0;
        app_log.LogService.log(
            '✅ history.fetch ok: points=$tsLen, from=${from.toIso8601String().substring(0, 10)}, to=${to.toIso8601String().substring(0, 10)}');
        return parsed;
      }
      app_log.LogService.log(
          '⚠️ history.fetch non-zero code: code=${response.data['code']}');
    } on DioException catch (e) {
      final isTimeout = e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.receiveTimeout ||
          e.type == DioExceptionType.sendTimeout;

      if (isTimeout && retryOnTimeout) {
        final retryCount = (count * 0.6).round().clamp(800, count);
        app_log.LogService.log(
            '⚠️ history.fetch timeout, retry once with smaller count=$retryCount (was $count), from=${from.toIso8601String().substring(0, 10)}, to=${to.toIso8601String().substring(0, 10)}');
        return _fetchHistoryRaw(
          from: from,
          to: to,
          count: retryCount,
          retryOnTimeout: false,
        );
      }

      app_log.LogService.log('❌ history.fetch failed', error: e);
    } catch (e) {
      app_log.LogService.log('❌ history.fetch failed', error: e);
    }

    return null;
  }

  Map<String, List<FlSpot>> _parseWeeklyHistoryData(
      Map<String, dynamic> data, DateTime weekStart) {
    final payload = (data['payload'] as Map<String, dynamic>?) ?? data;
    final timeSeries = payload['timeSeries'] as List<dynamic>? ?? [];
    final fields = payload['fields'] as Map<String, dynamic>? ?? {};
    app_log.LogService.log(
        '📊 chart.parse week source: weekStart=${weekStart.toIso8601String().substring(0, 10)}, points=${timeSeries.length}, fields=${fields.keys.length}');

    List<dynamic> pick(List<String> keys) {
      for (final key in keys) {
        final v = fields[key];
        if (v is List<dynamic> && v.any((e) => e != null)) return v;
      }
      return const [];
    }

    final genPower = pick(['generationPower']); // kW
    final loadPower = pick(['acOutputActivePower', 'loadPower']); // kW
    final batPower = pick([
      'batteryPower',
      'batteryActivePower',
      'batteryChargePower',
      'batteryDischargePower',
    ]); // kW
    final gridPower =
        pick(['acInputActivePower', 'gridPower', 'acInputPower']); // kW
    final batChargeCurrent = pick(['batteryChargingCurrent']); // A
    final batDischargeCurrent = pick(['batteryDischargeCurrent']); // A
    final batVoltage = pick(['batteryVoltage']); // V

    final pvWhByDay = List<double>.filled(7, 0.0);
    final loadWhByDay = List<double>.filled(7, 0.0);
    final batteryWhByDay = List<double>.filled(7, 0.0);
    final gridWhByDay = List<double>.filled(7, 0.0);

    DateTime? previousDt;

    for (var i = 0; i < timeSeries.length; i++) {
      final dt = DateTime.tryParse(timeSeries[i].toString())?.toLocal();
      if (dt == null) continue;

      // Integrate power using real telemetry cadence; clamp large gaps.
      final minutesFromPrevious = previousDt == null
          ? 5.0
          : dt.difference(previousDt).inMinutes.toDouble();
      final sampleMinutes = minutesFromPrevious <= 0
          ? 5.0
          : minutesFromPrevious > 30
              ? 5.0
              : minutesFromPrevious;
      final sampleHours = sampleMinutes / 60.0;
      previousDt = dt;

      final dayIndex = dt.difference(weekStart).inDays;
      if (dayIndex < 0 || dayIndex > 6) continue;

      final pvW = (i < genPower.length && genPower[i] != null)
          ? (genPower[i] as num).toDouble() * 1000.0
          : 0.0;
      final loadW = (i < loadPower.length && loadPower[i] != null)
          ? (loadPower[i] as num).toDouble() * 1000.0
          : 0.0;

      var batteryW = 0.0;
      if (i < batPower.length && batPower[i] != null) {
        batteryW = (batPower[i] as num).toDouble() * 1000.0;
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

      double gridW;
      if (i < gridPower.length && gridPower[i] != null) {
        gridW = (gridPower[i] as num).toDouble() * 1000.0;
      } else {
        final derivedGrid = loadW + (batteryW > 0 ? batteryW : 0.0) - pvW;
        gridW = derivedGrid > 0 ? derivedGrid : 0.0;
      }

      pvWhByDay[dayIndex] += pvW * sampleHours;
      loadWhByDay[dayIndex] += loadW * sampleHours;
      batteryWhByDay[dayIndex] += batteryW * sampleHours;
      gridWhByDay[dayIndex] += gridW * sampleHours;
    }

    List<FlSpot> toSpots(List<double> values) {
      return List<FlSpot>.generate(
          7, (index) => FlSpot(index.toDouble(), values[index]));
    }

    final result = {
      'pv': toSpots(pvWhByDay),
      'load': toSpots(loadWhByDay),
      'battery': toSpots(batteryWhByDay),
      'grid': toSpots(gridWhByDay),
    };
    _logChartSummary('chart.parse week', result);
    return result;
  }

  Future<Map<String, dynamic>> getUserInfo() async {
    if (accessToken == null || accessToken!.isEmpty) {
      app_log.LogService.log(
          '⚠️ profile.fetch skipped: missing access token for iotUserInfo');
      return {};
    }

    try {
      // HAR shows this endpoint uses token auth and empty JSON body.
      final response =
          await _dio.post('/apis/user/select/iotUserInfo', data: {});
      final code = response.data['code'];
      final message = response.data['message'] ?? response.data['localMessage'];

      app_log.LogService.log(
          '👤 profile.fetch response: code=$code, message=$message');

      if (code == 0) {
        return response.data['data'] ?? {};
      }
      return {};
    } catch (e) {
      app_log.LogService.log('❌ profile.fetch failed', error: e);
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

  void _logChartSummary(String prefix, Map<String, List<FlSpot>> data) {
    String fmt(List<FlSpot> spots) {
      if (spots.isEmpty) return 'count=0';
      final minX = spots.map((e) => e.x).reduce(min);
      final maxX = spots.map((e) => e.x).reduce(max);
      final minY = spots.map((e) => e.y).reduce(min);
      final maxY = spots.map((e) => e.y).reduce(max);
      return 'count=${spots.length},x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)},y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}';
    }

    String quality(List<FlSpot> spots) {
      if (spots.isEmpty) return 'missing';
      if (spots.any((s) => !s.x.isFinite || !s.y.isFinite)) return 'invalid';
      final uniqueX = spots.map((e) => e.x.toStringAsFixed(3)).toSet().length;
      if (uniqueX < 2) return 'poor';

      final minX = spots.map((e) => e.x).reduce(min);
      final maxX = spots.map((e) => e.x).reduce(max);
      final span = (maxX - minX).abs();
      if (span < 0.5) return 'low';
      if (span < 2.0) return 'partial';
      return 'good';
    }

    app_log.LogService.log(
        '📈 $prefix | pv[q=${quality(data['pv'] ?? const [])}](${fmt(data['pv'] ?? const [])}) load[q=${quality(data['load'] ?? const [])}](${fmt(data['load'] ?? const [])}) battery[q=${quality(data['battery'] ?? const [])}](${fmt(data['battery'] ?? const [])}) grid[q=${quality(data['grid'] ?? const [])}](${fmt(data['grid'] ?? const [])})');
  }

  double _parseDouble(dynamic value) {
    if (value == null) return 0.0;
    if (value is num) return value.toDouble();
    return double.tryParse(value.toString()) ?? 0.0;
  }

  /// Updates CO2 reduction based on lifetime PV generation in kWh.
  void updateCo2Reduction() {
    final totalEnergyKwh = totalEnergy;
    co2Reduction = totalEnergyKwh * _carbonEmissionFactorKgPerKwh;
    app_log.LogService.log(
        '♻️ CO2 reduction updated: ${co2Reduction.toStringAsFixed(2)} kg CO2 saved (total: ${totalEnergyKwh.toStringAsFixed(2)} kWh)');
  }

  double _integratePowerSpotsToKwh(List<FlSpot> spots) {
    final sorted = spots
        .where((spot) => spot.x.isFinite && spot.y.isFinite)
        .toList(growable: false)
      ..sort((a, b) => a.x.compareTo(b.x));

    if (sorted.isEmpty) return 0.0;
    if (sorted.length == 1) {
      return (sorted.first.y * (5.0 / 60.0)) / 1000.0;
    }

    var totalWh = 0.0;
    for (var i = 0; i < sorted.length - 1; i++) {
      final current = sorted[i];
      final next = sorted[i + 1];
      final deltaHoursRaw = next.x - current.x;
      final deltaHours = deltaHoursRaw <= 0 || deltaHoursRaw > 0.5
          ? (5.0 / 60.0)
          : deltaHoursRaw;
      final averagePowerW =
          ((current.y + next.y) / 2.0).clamp(0.0, double.infinity).toDouble();
      totalWh += averagePowerW * deltaHours;
    }

    return totalWh / 1000.0;
  }

  /// Updates today's generated solar energy in kWh from the day chart.
  void updateDailyEnergyFromChart(Map<String, List<FlSpot>> chartData) {
    final pvSpots = chartData['pv'] ?? [];
    if (pvSpots.isEmpty) {
      dailyEnergy = 0.0;
    } else {
      dailyEnergy = _integratePowerSpotsToKwh(pvSpots);
    }
  }

  /// Refreshes the energy overview cards:
  /// - `dailyEnergy`: today's PV generation in kWh
  /// - `totalEnergy`: lifetime PV generation from device metadata in kWh
  Future<void> updateDailyEnergyStats(DateTime targetDate) async {
    if (currentStationId == null || deviceSn == null) {
      return;
    }

    try {
      final chartData = await getChartData(0, targetDate);
      if (chartData['pv'] != null && (chartData['pv']?.isNotEmpty ?? false)) {
        updateDailyEnergyFromChart(chartData);
      }

      updateCo2Reduction();
      app_log.LogService.log(
          '📊 Energy stats updated: daily=${dailyEnergy.toStringAsFixed(2)}kWh, lifetime_total=${totalEnergy.toStringAsFixed(2)}kWh');
    } catch (e) {
      app_log.LogService.log('⚠️ updateDailyEnergyStats failed', error: e);
    }
  }
}

import 'package:dio/dio.dart';
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/inverter_data.dart';

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
        if (kDebugMode) print('🔴 Помилка мережі: ${e.message}');
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

      if (response.data['code'] == 0) {
        final data = response.data['data'];
        accessToken = data['accessToken'] ?? data['token'];
        userId = data['userId']?.toString();
        await _fetchDeviceList();
        return true;
      }
    } catch (e) {
      if (kDebugMode) print('Login error: $e');
    }
    return false;
  }

  Future<void> _fetchDeviceList() async {
    if (userId == null) return;
    try {
      final prefs = await SharedPreferences.getInstance();
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
          co2Reduction = _parseDouble(dev['co2EmissionReduction']);

          if (deviceSn != null) {
            await prefs.setString('saved_device_sn', deviceSn!);
          }
        }
      }
    } catch (e) {
      if (kDebugMode) print('Device list error: $e');
    }
  }

  Future<InverterData?> getRealTimeData() async {
    if (deviceSn == null) return null;
    try {
      final response = await _dio.get('/apis/deviceState/simple/energy/flow/v1',
          queryParameters: {'deviceId': deviceSn, 'dataSource': 1});
      if (response.data['code'] == 0) {
        return InverterData.fromJson(
            response.data['data'], deviceSn!, currentMode?.toString() ?? '');
      }
    } catch (e) {
      if (kDebugMode) print('Realtime data error: $e');
    }
    return null;
  }

  Future<Map<String, List<FlSpot>>> getChartData(
      int range, DateTime targetDate) async {
    if (currentStationId == null) {
      return {'pv': [], 'load': [], 'grid': [], 'battery': []};
    }

    var category = range == 0 ? 'daily' : (range == 1 ? 'monthly' : 'yearly');
    var type = range == 0
        ? 'pvInverterPowerClass'
        : 'pvInverterElectricityQuantityClass';

    final endpoint =
        '/apis/ownerOverView/station/stateAttributeSummary/category/$category?summaryCategoryKey=$type';

    String timeStr;
    if (category == 'daily') {
      timeStr =
          "${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}-${targetDate.day.toString().padLeft(2, '0')}";
    } else if (category == 'monthly') {
      timeStr =
          "${targetDate.year}-${targetDate.month.toString().padLeft(2, '0')}";
    } else {
      timeStr = '${targetDate.year}';
    }

    try {
      final response = await _dio.post(endpoint,
          data: {'time': timeStr, 'stationId': currentStationId});

      if (response.data['code'] == 0) {
        return _parseHarChartData(response.data['data'] ?? {});
      }
    } catch (e) {
      debugPrint('Chart error: $e');
    }
    return {'pv': [], 'load': [], 'grid': [], 'battery': []};
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

import 'package:dio/dio.dart';
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/inverter_data.dart';

class InverterService {
  final String _appId = 'rBrTRfAPXz';
  final String _encryptedAppSecret = 'I4D0KRr2339z3pQ/at91V9BpFAOe54DaTafwSm6suIQ=';

  late final Dio _dio;
  String? accessToken;
  String? userId;
  String? deviceSn;
  int? currentMode;

  late final String _appSecret = _decryptAppSecret(_appId, _encryptedAppSecret);

  InverterService() {
    _dio = Dio(BaseOptions(
      baseUrl: "https://solar.siseli.com",
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
          'Origin': 'https://solar.siseli.com',
          'Referer': 'https://solar.siseli.com/',
          'IOT-Token': (accessToken?.isNotEmpty == true) ? accessToken : 'null',
        });
        return handler.next(options);
      },
    ));
  }

  String _generateNonce(int length) {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    final random = Random.secure();
    return String.fromCharCodes(Iterable.generate(length, (_) => chars.codeUnitAt(random.nextInt(chars.length))));
  }

  String _decryptAppSecret(String appId, String encryptedSecret) {
    final md5AppId = md5.convert(utf8.encode(appId)).toString().toLowerCase();
    final keyHex = md5AppId.substring(0, 16);
    final ivHex = md5AppId.substring(16);
    final key = encrypt.Key.fromUtf8(keyHex);
    final iv = encrypt.IV.fromUtf8(ivHex);
    final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc, padding: null));
    return encrypter.decrypt64(encryptedSecret, iv: iv).replaceAll(RegExp(r'\x00+$'), '').trim();
  }

  String _calculateBodyHash(String method, Object? body) {
    if (method.trim().toUpperCase() == 'GET' || body == null) {
      return sha256.convert(utf8.encode('{}')).toString().toLowerCase();
    }
    return sha256.convert(utf8.encode(body is String ? body : jsonEncode(body))).toString().toLowerCase();
  }

  String _calculateAppSign({required String appId, required String appSecret, required String bodyHash, required String nonce}) {
    final payload = {'IOT-Open-AppID': appId, 'IOT-Open-Body-Hash': bodyHash, 'IOT-Open-Nonce': nonce};
    final sortedKeys = payload.keys.toList()..sort();
    final queryString = sortedKeys.map((k) => '$k=${payload[k]}').join('&');
    final hmac = Hmac(sha256, utf8.encode(appSecret));
    return md5.convert(hmac.convert(utf8.encode(base64.encode(utf8.encode(queryString)))).bytes).toString().toLowerCase();
  }

  Future<bool> login(String email, String password) async {
    try {
      final passwordMd5 = (password.length == 32) ? password.toLowerCase() : md5.convert(utf8.encode(password)).toString().toLowerCase();
      final response = await _dio.post("/apis/login/account", data: {"account": email, "password": passwordMd5});

      if (response.data['code'] == 0 || response.data['success'] == true) {
        final data = response.data['data'];
        accessToken = data['accessToken'] ?? data['token'];
        userId = data['userId']?.toString();
        await _fetchDeviceList();
        return true;
      }
      return false;
    } catch (_) { return false; }
  }

  Future<void> _fetchDeviceList() async {
    if (userId == null) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      deviceSn = prefs.getString('saved_device_sn');

      final response = await _dio.post("/apis/device/list", data: {
        "page": 1, "count": 10, "serialNumber": "", "name": "", "stationId": "", "state": "", "exportType": 0, "applyModeCategory": 1
      });

      if ((response.data['code'] == 0 || response.data['success'] == true) && response.data['data'] != null) {
        final dataInfo = response.data['data'];
        List devices = dataInfo is List ? dataInfo : (dataInfo['list'] ?? dataInfo['records'] ?? dataInfo['data'] ?? []);
        if (devices.isNotEmpty) {
          final firstDevice = devices[0];
          final extractedId = firstDevice['id']?.toString() ?? firstDevice['deviceId']?.toString() ?? firstDevice['deviceSn']?.toString();
          if (extractedId != null && extractedId.isNotEmpty) {
            deviceSn = extractedId;
            await prefs.setString('saved_device_sn', deviceSn!);
          }
        }
      }
    } catch (_) {}
  }

  Future<InverterData?> getRealTimeData() async {
    if (userId == null || deviceSn == null || deviceSn!.isEmpty) return null;
    try {
      final response = await _dio.get("/apis/deviceState/simple/energy/flow/v1", queryParameters: {
        "deviceId": deviceSn,
        "dataSource": 1,
      });

      if ((response.data['code'] == 0 || response.data['success'] == true) && response.data['data'] != null) {
        return InverterData.fromJson(response.data['data'], deviceSn!, currentMode?.toString() ?? "");
      }
    } catch (_) {}
    return null;
  }

  Future<bool> setConfigItem(String key, String value) async {
    if (accessToken == null || deviceSn == null) return false;
    try {
      final response = await _dio.post(
        "/apis/remote/device/config/write",
        queryParameters: {"deviceId": deviceSn},
        data: {"id": deviceSn, "key": key, "value": value},
      );
      return response.data['code'] == 0 || response.data['success'] == true;
    } catch (_) {
      return false;
    }
  }

  Future<bool> setMode(int mode) async {
    bool success = await setConfigItem("outputSourcePrioritySetting", mode.toString());
    if (success) {
      currentMode = mode;
      return true;
    }
    return false;
  }
}
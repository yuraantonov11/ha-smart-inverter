import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Безпечний сервіс для зберігання конфіденційних даних (паролі, токени)
/// Використовує flutter_secure_storage який зберігає дані в системних хранилищах:
/// - Windows: DPAPI (Data Protection API)
/// - macOS: Keychain
/// - Linux: SecretService
/// - iOS: Keychain
class SecureStorageService {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(
      keyCipherAlgorithm:
          KeyCipherAlgorithm.RSA_ECB_OAEPwithSHA_256andMGF1Padding,
      storageCipherAlgorithm: StorageCipherAlgorithm.AES_GCM_NoPadding,
    ),
  );

  /// Зберегти пароль безпечно
  static Future<void> savePassword(String password) async {
    try {
      await _storage.write(key: 'saved_pass', value: password);
    } catch (e) {
      throw Exception('Помилка збереження пароля: $e');
    }
  }

  /// Отримати пароль
  static Future<String?> getPassword() async {
    try {
      return await _storage.read(key: 'saved_pass');
    } catch (e) {
      throw Exception('Помилка читання пароля: $e');
    }
  }

  /// Видалити пароль
  static Future<void> deletePassword() async {
    try {
      await _storage.delete(key: 'saved_pass');
    } catch (e) {
      throw Exception('Помилка видалення пароля: $e');
    }
  }

  /// Зберегти токен безпечно
  static Future<void> saveToken(String token) async {
    try {
      await _storage.write(key: 'access_token', value: token);
    } catch (e) {
      throw Exception('Помилка збереження токена: $e');
    }
  }

  /// Отримати токен
  static Future<String?> getToken() async {
    try {
      return await _storage.read(key: 'access_token');
    } catch (e) {
      throw Exception('Помилка читання токена: $e');
    }
  }

  /// Видалити токен
  static Future<void> deleteToken() async {
    try {
      await _storage.delete(key: 'access_token');
    } catch (e) {
      throw Exception('Помилка видалення токена: $e');
    }
  }

  /// Очистити всі безпечні дані
  static Future<void> clearAll() async {
    try {
      await _storage.deleteAll();
    } catch (e) {
      throw Exception('Помилка очищення сховища: $e');
    }
  }
}

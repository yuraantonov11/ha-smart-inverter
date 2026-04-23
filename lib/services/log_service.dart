// lib/services/log_service.dart
import 'package:flutter/foundation.dart';

enum LogLevel { info, warn, error }

class LogEntry {
  final DateTime timestamp;
  final LogLevel level;
  final String message;
  final String? errorText;

  const LogEntry({
    required this.timestamp,
    required this.level,
    required this.message,
    this.errorText,
  });

  String get formattedTime {
    final hh = timestamp.hour.toString().padLeft(2, '0');
    final mm = timestamp.minute.toString().padLeft(2, '0');
    final ss = timestamp.second.toString().padLeft(2, '0');
    return '$hh:$mm:$ss';
  }

  String get levelLabel => switch (level) {
        LogLevel.info => 'INFO',
        LogLevel.warn => 'WARN',
        LogLevel.error => 'ERROR',
      };

  String get levelIcon => switch (level) {
        LogLevel.info => 'ℹ️',
        LogLevel.warn => '⚠️',
        LogLevel.error => '❌',
      };

  String toDisplayString() {
    final suffix = errorText != null ? ' | Error: $errorText' : '';
    return '[$formattedTime] $levelIcon $levelLabel | $message$suffix';
  }
}

class LogService {
  static final List<LogEntry> _entries = [];

  /// Маскує конфіденційні дані в log-повідомленні
  static String _maskSensitiveData(String message) {
    var masked = message;

    // Маскуємо JSON токени та паролі
    masked = masked.replaceAllMapped(
      RegExp(
          r'"(password|token|accessToken|refreshToken|Authorization)":\s*"([^"]*)"',
          caseSensitive: false),
      (match) => '"${match.group(1)}":"***MASKED***"',
    );

    // Маскуємо URL параметри з токенами
    masked = masked.replaceAllMapped(
      RegExp(r'(token=|password=|auth=|apikey=)([a-zA-Z0-9._-]+)',
          caseSensitive: false),
      (match) => '${match.group(1)}***MASKED***',
    );

    // Маскуємо довгі рядки які виглядають як токени (більше 20 символів)
    masked = masked.replaceAllMapped(
      RegExp(r'(?:Bearer|token|password)[\s]*:?\s*([a-zA-Z0-9._\-+/]{20,})',
          caseSensitive: false),
      (match) => '${match.group(0)?.substring(0, 10) ?? ''}...***MASKED***',
    );

    // Маскуємо email адреси (опційно, залежно від конфіденційності)
    // masked = masked.replaceAllMapped(RegExp(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'),
    //   (match) => '***EMAIL***');

    return masked;
  }

  static LogLevel _autoLevel(String message, dynamic error, LogLevel level) {
    if (level != LogLevel.info) return level;
    if (error != null || message.contains('❌')) return LogLevel.error;
    if (message.contains('⚠️') || message.contains('🛟')) return LogLevel.warn;
    return LogLevel.info;
  }

  static void log(
    String message, {
    dynamic error,
    StackTrace? stack,
    LogLevel level = LogLevel.info,
  }) {
    // ===== БЕЗПЕКА: МАСКУВАННЯ КОНФІДЕНЦІЙНИХ ДАНИХ =====
    final maskedMessage = _maskSensitiveData(message);
    String? maskedError;
    if (error != null) {
      maskedError = _maskSensitiveData(error.toString());
    }

    final effectiveLevel = _autoLevel(maskedMessage, maskedError, level);
    final entry = LogEntry(
      timestamp: DateTime.now(),
      level: effectiveLevel,
      message: maskedMessage,
      errorText: maskedError,
    );
    final logLine = entry.toDisplayString();

    if (kDebugMode) {
      print(logLine);
      if (maskedError != null) print('Error: $maskedError');
      if (stack != null) print('Stack: $stack');
    }

    _entries.add(entry);
    if (_entries.length > 1000) _entries.removeAt(0);
  }

  static List<LogEntry> get entries => List.unmodifiable(_entries);
  static List<String> get allLogs =>
      List.unmodifiable(_entries.map((e) => e.toDisplayString()));

  /// БЕЗПЕКА: Повертає санітизовані логи без конфіденційних даних
  /// Використовується при експорті логів користувачем
  static List<String> get sanitizedLogs {
    return _entries.map((e) {
      var log = e.toDisplayString();

      // Видаліть IP адреси
      log = log.replaceAll(RegExp(r'\b\d+\.\d+\.\d+\.\d+\b'), '***IP***');

      // Видаліть токени/ключи
      log = log.replaceAll(
          RegExp(r'token[=:\s]+[a-zA-Z0-9._+/]{20,}', caseSensitive: false),
          'token=***REDACTED***');

      // Видаліть URLs з токенами
      log = log.replaceAll(RegExp(r'https?://[^\s]+', caseSensitive: false),
          'https://***URL_REDACTED***');

      return log;
    }).toList();
  }

  static void clear() => _entries.clear();
}

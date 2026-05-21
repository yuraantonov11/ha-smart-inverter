// lib/services/log_service.dart
import 'package:flutter/foundation.dart';
import 'dart:io';
import 'package:path_provider/path_provider.dart';

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

  // File-based logging for critical events (HEMS, battery, mode switches)
  static File? _criticalLogFile;
  static final int _maxFileSize = 5 * 1024 * 1024; // 5 MB per file
  static int _fileRotationCount = 0;
  static final int _maxLogFiles = 10;

  /// Initialize file-based logging (must be called on app startup)
  static Future<void> initializeFileLogging() async {
    try {
      final appDir = await getApplicationDocumentsDirectory();
      final logsDir = Directory('${appDir.path}/siseli_debug_logs');
      if (!await logsDir.exists()) {
        await logsDir.create(recursive: true);
      }

      final todayDate = DateTime.now();
      final dateStr =
          '${todayDate.year}-${todayDate.month.toString().padLeft(2, '0')}-${todayDate.day.toString().padLeft(2, '0')}';
      const logFileName = 'hems_critical_events.log';
      _criticalLogFile = File('${logsDir.path}/$logFileName-$dateStr');

      // Log initialization
      await _writeToFile('=== DEBUG LOG SESSION STARTED ===');
      await _writeToFile(
          'Device: ${Platform.isWindows ? 'Windows' : Platform.isAndroid ? 'Android' : 'iOS'}');
      await _writeToFile('Timestamp: $todayDate');
    } catch (e) {
      if (kDebugMode) print('❌ Failed to initialize file logging: $e');
    }
  }

  /// Write directly to critical events log file
  static Future<void> _writeToFile(String message) async {
    try {
      if (_criticalLogFile == null) return;

      final file = _criticalLogFile!;
      if (await file.exists() && await file.length() > _maxFileSize) {
        // Rotate log file
        _fileRotationCount++;
        if (_fileRotationCount > _maxLogFiles) {
          _fileRotationCount = 1;
        }
        final rotatedName =
            '${file.path}.${_fileRotationCount.toString().padLeft(2, '0')}';
        await file.rename(rotatedName);
        _criticalLogFile = File(file.path);
      }

      final timestamp = DateTime.now().toString().substring(10, 19);
      final logLine = '[$timestamp] $message\n';
      await _criticalLogFile!.writeAsString(
        logLine,
        mode: FileMode.append,
      );
    } catch (e) {
      if (kDebugMode) print('❌ Failed to write log file: $e');
    }
  }

  /// Log critical events related to HEMS, battery protection, mode conflicts
  static void logCritical(String message,
      {String? category = 'GENERAL'}) async {
    final fullMsg = '[$category] $message';
    await _writeToFile(fullMsg);

    // Also log to memory buffer
    log(fullMsg, level: LogLevel.warn);
  }

  /// Get file path for debug logs (returns null if not initialized)
  static Future<String?> getDebugLogPath() async {
    try {
      if (_criticalLogFile == null) {
        await initializeFileLogging();
      }
      return _criticalLogFile?.path;
    } catch (e) {
      return null;
    }
  }

  /// Read critical events log file
  static Future<String> readCriticalLog() async {
    try {
      if (_criticalLogFile == null) {
        await initializeFileLogging();
      }
      if (_criticalLogFile == null || !await _criticalLogFile!.exists()) {
        return 'No critical log file available yet.';
      }
      return await _criticalLogFile!.readAsString();
    } catch (e) {
      return 'Error reading log file: $e';
    }
  }

  /// List all debug log files in the app documents directory
  static Future<List<FileSystemEntity>> listDebugLogFiles() async {
    try {
      final appDir = await getApplicationDocumentsDirectory();
      final logsDir = Directory('${appDir.path}/siseli_debug_logs');
      if (!await logsDir.exists()) {
        return [];
      }
      return logsDir
          .listSync()
          .where((f) => f.path.contains('hems_critical_events.log'))
          .toList()
        ..sort(
            (a, b) => b.statSync().modified.compareTo(a.statSync().modified));
    } catch (e) {
      if (kDebugMode) print('❌ Error listing log files: $e');
      return [];
    }
  }

  /// Delete all debug log files
  static Future<void> clearDebugLogs() async {
    try {
      final appDir = await getApplicationDocumentsDirectory();
      final logsDir = Directory('${appDir.path}/siseli_debug_logs');
      if (await logsDir.exists()) {
        logsDir.deleteSync(recursive: true);
        await initializeFileLogging();
      }
    } catch (e) {
      if (kDebugMode) print('❌ Failed to clear debug logs: $e');
    }
  }

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

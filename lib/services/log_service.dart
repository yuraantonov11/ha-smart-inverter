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
    final effectiveLevel = _autoLevel(message, error, level);
    final entry = LogEntry(
      timestamp: DateTime.now(),
      level: effectiveLevel,
      message: message,
      errorText: error?.toString(),
    );
    final logLine = entry.toDisplayString();

    if (kDebugMode) {
      print(logLine);
      if (error != null) print('Error: $error');
      if (stack != null) print('Stack: $stack');
    }

    _entries.add(entry);
    if (_entries.length > 1000) _entries.removeAt(0);
  }

  static List<LogEntry> get entries => List.unmodifiable(_entries);
  static List<String> get allLogs =>
      List.unmodifiable(_entries.map((e) => e.toDisplayString()));
  static void clear() => _entries.clear();
}

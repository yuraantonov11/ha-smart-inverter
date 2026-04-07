// lib/services/log_service.dart
import 'package:flutter/foundation.dart';

class LogService {
  static final List<String> _logs = [];

  // Додано параметр stack
  static void log(String message, {dynamic error, StackTrace? stack}) {
    final timestamp = DateTime.now().toIso8601String().substring(11, 19);
    final logEntry = '[$timestamp] $message';

    if (kDebugMode) {
      print(logEntry);
      if (error != null) print('Error: $error');
      if (stack != null) print('Stack: $stack');
    }

    _logs.add(logEntry + (error != null ? ' | Error: $error' : ''));
    if (_logs.length > 1000) _logs.removeAt(0);
  }

  static List<String> get allLogs => List.unmodifiable(_logs);
  static void clear() => _logs.clear();
}

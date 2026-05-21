// lib/services/event_history_service.dart
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'log_service.dart';

// ---------------------------------------------------------------------------
// Event types
// ---------------------------------------------------------------------------

enum HemsEventType {
  gridOutage,
  gridRestored,
  gridInstability,
  modeChanged,
  lowBattery,
  batteryRecovered,
  emergencyCharge,
  batteryRecovery,
  stormAutoActivated,
  stormAutoDeactivated,
  anomaly,
  custom,
}

extension HemsEventTypeExt on HemsEventType {
  String get icon {
    switch (this) {
      case HemsEventType.gridOutage:
        return '⚡';
      case HemsEventType.gridRestored:
        return '🔌';
      case HemsEventType.gridInstability:
        return '⚠️';
      case HemsEventType.modeChanged:
        return '🔄';
      case HemsEventType.lowBattery:
        return '🪫';
      case HemsEventType.batteryRecovered:
        return '🔋';
      case HemsEventType.emergencyCharge:
        return '🚨';
      case HemsEventType.batteryRecovery:
        return '💪';
      case HemsEventType.stormAutoActivated:
        return '🌩️';
      case HemsEventType.stormAutoDeactivated:
        return '☀️';
      case HemsEventType.anomaly:
        return '📊';
      case HemsEventType.custom:
        return 'ℹ️';
    }
  }

  bool get isCritical {
    return this == HemsEventType.gridOutage ||
        this == HemsEventType.lowBattery ||
        this == HemsEventType.emergencyCharge ||
        this == HemsEventType.batteryRecovery ||
        this == HemsEventType.stormAutoActivated ||
        this == HemsEventType.anomaly;
  }
}

// ---------------------------------------------------------------------------
// Event model
// ---------------------------------------------------------------------------

class HemsEvent {
  final String id;
  final HemsEventType type;
  final DateTime time;
  final String message;
  final Map<String, dynamic>? meta;

  const HemsEvent({
    required this.id,
    required this.type,
    required this.time,
    required this.message,
    this.meta,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type.index,
        'time': time.toIso8601String(),
        'message': message,
        if (meta != null) 'meta': meta,
      };

  factory HemsEvent.fromJson(Map<String, dynamic> json) {
    final typeIdx =
        (json['type'] as int? ?? 0).clamp(0, HemsEventType.values.length - 1);
    return HemsEvent(
      id: json['id'] as String? ??
          DateTime.now().millisecondsSinceEpoch.toString(),
      type: HemsEventType.values[typeIdx],
      time: DateTime.tryParse(json['time'] as String? ?? '') ?? DateTime.now(),
      message: json['message'] as String? ?? '',
      meta: json['meta'] as Map<String, dynamic>?,
    );
  }

  String toCsvRow() {
    final t = time.toLocal();
    final timeStr =
        '${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')} '
        '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}:${t.second.toString().padLeft(2, '0')}';
    final escapedMsg = message.replaceAll('"', '""');
    return '"$timeStr","${type.name}","$escapedMsg"';
  }
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

class EventHistoryService extends ChangeNotifier {
  static const _prefsKey = 'event_history_v1';
  static const _maxInMemory = 300;
  static const _maxPersisted = 60;

  static EventHistoryService? _instance;

  static EventHistoryService get instance {
    _instance ??= EventHistoryService._();
    return _instance!;
  }

  EventHistoryService._();

  final List<HemsEvent> _events = [];
  bool _loaded = false;

  List<HemsEvent> get events => List.unmodifiable(_events);
  bool get isEmpty => _events.isEmpty;
  int get count => _events.length;

  /// Load persisted events on startup.
  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw != null) {
        final list = jsonDecode(raw) as List<dynamic>;
        _events.addAll(
          list
              .map((e) => HemsEvent.fromJson(e as Map<String, dynamic>))
              .toList(),
        );
        _events.sort((a, b) => b.time.compareTo(a.time));
        notifyListeners();
      }
    } catch (e) {
      LogService.log('⚠️ EventHistoryService.load: $e');
    }
  }

  /// Add a new event to history.
  void addEvent(HemsEventType type, String message,
      {Map<String, dynamic>? meta}) {
    final event = HemsEvent(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
      type: type,
      time: DateTime.now(),
      message: message,
      meta: meta,
    );
    _events.insert(0, event);
    if (_events.length > _maxInMemory) _events.removeLast();
    notifyListeners();
    _persistAsync();
  }

  void clearAll() {
    _events.clear();
    notifyListeners();
    _persistAsync();
  }

  Future<void> _persistAsync() async {
    try {
      final toSave =
          _events.take(_maxPersisted).map((e) => e.toJson()).toList();
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, jsonEncode(toSave));
    } catch (e) {
      LogService.log('⚠️ EventHistoryService._persist: $e');
    }
  }

  /// Build CSV string from all in-memory events.
  String toCsvString() {
    final lines = [
      '"Time","Type","Message"',
      ..._events.map((e) => e.toCsvRow()),
    ];
    return lines.join('\n');
  }

  /// Export events to a CSV file in the app documents folder.
  /// Returns the file path on success, null on failure.
  Future<String?> exportToCsvFile() async {
    try {
      final docsDir = await getApplicationDocumentsDirectory();
      final logsDir = Directory('${docsDir.path}/siseli_debug_logs');
      if (!logsDir.existsSync()) logsDir.createSync(recursive: true);
      final now = DateTime.now();
      final fname =
          'hems_events_${now.year}${now.month.toString().padLeft(2, '0')}${now.day.toString().padLeft(2, '0')}.csv';
      final file = File('${logsDir.path}/$fname');
      await file.writeAsString(toCsvString(), flush: true);
      LogService.log('📤 EventHistory exported to: ${file.path}');
      return file.path;
    } catch (e) {
      LogService.log('⚠️ EventHistory export failed: $e');
      return null;
    }
  }
}

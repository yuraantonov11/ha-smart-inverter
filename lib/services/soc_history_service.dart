// lib/services/soc_history_service.dart
//
// Rolling 24-hour SOC history store.
// Stores up to [_maxSamples] data points (default: 288 = every 5 min × 24 h).
// Persists to SharedPreferences as a compact JSON array.
//
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'log_service.dart';

// ---------------------------------------------------------------------------
// Data model
// ---------------------------------------------------------------------------

class SocSample {
  final DateTime timestamp;
  final double soc; // 0..100
  final double pvPower; // W
  final double loadPower; // W
  final double batteryPower; // W positive=charge, negative=discharge

  const SocSample({
    required this.timestamp,
    required this.soc,
    required this.pvPower,
    required this.loadPower,
    required this.batteryPower,
  });

  Map<String, dynamic> toJson() => {
        't': timestamp.millisecondsSinceEpoch,
        's': soc,
        'p': pvPower,
        'l': loadPower,
        'b': batteryPower,
      };

  factory SocSample.fromJson(Map<String, dynamic> j) => SocSample(
        timestamp: DateTime.fromMillisecondsSinceEpoch((j['t'] as num).toInt()),
        soc: (j['s'] as num).toDouble(),
        pvPower: (j['p'] as num?)?.toDouble() ?? 0.0,
        loadPower: (j['l'] as num?)?.toDouble() ?? 0.0,
        batteryPower: (j['b'] as num?)?.toDouble() ?? 0.0,
      );
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

class SocHistoryService {
  static const String _prefsKey = 'soc_history_v1';
  static const int _maxSamples = 288; // 24 h @ 5-min intervals
  static const Duration _24h = Duration(hours: 24);

  /// Minimum gap between saved samples (prevents burst-writes on reconnect).
  static const Duration _minInterval = Duration(minutes: 4, seconds: 30);

  static SocHistoryService? _instance;
  static SocHistoryService get instance {
    _instance ??= SocHistoryService._();
    return _instance!;
  }

  SocHistoryService._();

  final List<SocSample> _samples = [];
  bool _loaded = false;
  DateTime? _lastAdded;

  void _normalizeSamples(DateTime now) {
    final cutoff = now.subtract(_24h);

    // Keep only the 24h window, oldest -> newest, and collapse duplicates.
    _samples.removeWhere((s) => s.timestamp.isBefore(cutoff));
    _samples.sort((a, b) => a.timestamp.compareTo(b.timestamp));

    final deduped = <SocSample>[];
    for (final sample in _samples) {
      if (deduped.isEmpty ||
          deduped.last.timestamp.millisecondsSinceEpoch !=
              sample.timestamp.millisecondsSinceEpoch) {
        deduped.add(sample);
      } else {
        // Prefer the latest value for the same timestamp key.
        deduped[deduped.length - 1] = sample;
      }
    }

    _samples
      ..clear()
      ..addAll(deduped);

    while (_samples.length > _maxSamples) {
      _samples.removeAt(0);
    }

    _lastAdded = _samples.isEmpty ? null : _samples.last.timestamp;
  }

  // --- Public API -----------------------------------------------------------

  /// All samples ordered oldest-first, within the last 24 h.
  List<SocSample> get samples => List.unmodifiable(_samples);

  /// Load persisted history from SharedPreferences (call once at startup).
  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw != null && raw.isNotEmpty) {
        final list = (json.decode(raw) as List)
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList(growable: false);
        _samples.addAll(
          list.map(SocSample.fromJson).where(
              (s) => !s.timestamp.isBefore(DateTime.now().subtract(_24h))),
        );
        _normalizeSamples(DateTime.now());
        LogService.log('SocHistoryService loaded: ${_samples.length} samples');
      }
    } catch (e) {
      LogService.log('SocHistoryService.load error: $e');
    }
  }

  /// Add a new sample. Silently ignored if called too soon after previous add.
  /// Returns `true` if the sample was accepted.
  bool addSample({
    required double soc,
    required double pvPower,
    required double loadPower,
    required double batteryPower,
    DateTime? at,
  }) {
    final now = at ?? DateTime.now();

    // Throttle
    if (_lastAdded != null && now.difference(_lastAdded!) < _minInterval) {
      return false;
    }

    // Purge samples older than 24 h
    _normalizeSamples(now);

    // Add new sample
    _samples.add(SocSample(
      timestamp: now,
      soc: soc.clamp(0.0, 100.0),
      pvPower: pvPower,
      loadPower: loadPower,
      batteryPower: batteryPower,
    ));

    // Keep within max capacity
    while (_samples.length > _maxSamples) {
      _samples.removeAt(0);
    }

    _lastAdded = now;
    _normalizeSamples(now);
    _persistAsync();
    return true;
  }

  /// Convenience getter: only samples from today (local time, midnight..now).
  List<SocSample> get todaySamples {
    final midnight = DateTime.now();
    final start = DateTime(midnight.year, midnight.month, midnight.day);
    return _samples.where((s) => !s.timestamp.isBefore(start)).toList();
  }

  @visibleForTesting
  void debugResetForTests() {
    _samples.clear();
    _loaded = false;
    _lastAdded = null;
  }

  // --- Persistence ----------------------------------------------------------

  void _persistAsync() {
    SharedPreferences.getInstance().then((prefs) {
      try {
        final encoded = json.encode(_samples.map((s) => s.toJson()).toList());
        prefs.setString(_prefsKey, encoded);
      } catch (e) {
        LogService.log('SocHistoryService._persist error: $e');
      }
    });
  }
}

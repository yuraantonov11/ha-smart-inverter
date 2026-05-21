import 'dart:convert';

/// HEMS mode that a schedule rule forces during its active window.
enum ScheduleRuleMode {
  adaptive, // 0 — same as smartMode==0
  arbitrage, // 1 — same as smartMode==1
  storm, // 2 — same as smartMode==2
}

extension ScheduleRuleModeX on ScheduleRuleMode {
  int get smartModeIndex => index; // maps directly to smartMode int
}

/// A time-based override rule for HEMS.
///
/// When a rule is enabled and its time window is currently active, the HEMS
/// engine executes the [mode] instead of the user-selected smart mode.
class ScheduleRule {
  final String id;
  final String name;

  /// ISO weekdays that this rule applies to: 1=Monday … 7=Sunday.
  final List<int> daysOfWeek;

  final int startHour; // 0‑23
  final int startMinute; // 0‑59
  final int endHour; // 0‑23
  final int endMinute; // 0‑59

  final ScheduleRuleMode mode;
  final bool enabled;

  /// Rule priority: 1 (lowest) … 10 (highest).
  /// When multiple rules are active at the same time the one with the
  /// highest [priority] value wins. Equal priorities resolve by list order
  /// (first rule wins).
  final int priority;

  static const int minPriority = 1;
  static const int maxPriority = 10;
  static const int defaultPriority = 5;

  const ScheduleRule({
    required this.id,
    required this.name,
    required this.daysOfWeek,
    required this.startHour,
    required this.startMinute,
    required this.endHour,
    required this.endMinute,
    required this.mode,
    this.enabled = true,
    this.priority = defaultPriority,
  });

  int get startTotalMinutes =>
      _normalizeHour(startHour) * 60 + _normalizeMinute(startMinute);
  int get endTotalMinutes =>
      _normalizeHour(endHour) * 60 + _normalizeMinute(endMinute);
  bool get isOvernight => startTotalMinutes > endTotalMinutes;

  static int _normalizeHour(int h) => h.clamp(0, 23);
  static int _normalizeMinute(int m) => m.clamp(0, 59);
  static int _normalizeWeekday(int d) => d.clamp(1, 7);

  static List<int> _normalizeDays(Iterable<int> days) {
    final set = <int>{};
    for (final d in days) {
      set.add(_normalizeWeekday(d));
    }
    final out = set.toList()..sort();
    return out;
  }

  /// Returns true if the rule covers the given [dt].
  bool isActiveAt(DateTime dt) {
    if (!enabled) return false;
    if (daysOfWeek.isEmpty) return false;
    final nowMin = dt.hour * 60 + dt.minute;
    final startMin = startTotalMinutes;
    final endMin = endTotalMinutes;

    if (startMin == endMin) return false; // degenerate

    if (startMin < endMin) {
      // Same-day window, e.g. 08:00‑22:00
      if (!daysOfWeek.contains(dt.weekday)) return false;
      return nowMin >= startMin && nowMin < endMin;
    } else {
      // Overnight window, e.g. 23:00‑06:00
      if (nowMin >= startMin) {
        // Late segment belongs to current weekday.
        return daysOfWeek.contains(dt.weekday);
      }
      if (nowMin >= endMin) {
        return false;
      }
      // Early segment belongs to previous weekday's window.
      final prevWeekday =
          dt.weekday == DateTime.monday ? DateTime.sunday : dt.weekday - 1;
      return daysOfWeek.contains(prevWeekday);
    }
  }

  bool get isActiveNow => isActiveAt(DateTime.now());

  /// Human-readable time range string, e.g. "08:00 – 22:00".
  String get timeRangeLabel {
    final s =
        '${startHour.toString().padLeft(2, '0')}:${startMinute.toString().padLeft(2, '0')}';
    final e =
        '${endHour.toString().padLeft(2, '0')}:${endMinute.toString().padLeft(2, '0')}';
    return '$s – $e';
  }

  /// Abbreviated day list, e.g. "Mon Wed Fri".
  String get daysLabel {
    const abbr = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    if (daysOfWeek.length == 7) return 'Every day';
    final weekdays = [1, 2, 3, 4, 5];
    final weekend = [6, 7];
    final sorted = List<int>.from(daysOfWeek)..sort();
    if (sorted.length == 5 && sorted.every((d) => weekdays.contains(d))) {
      return 'Weekdays';
    }
    if (sorted.length == 2 && sorted.every((d) => weekend.contains(d))) {
      return 'Weekend';
    }
    return sorted.map((d) => abbr[d]).join(', ');
  }

  // ── Serialisation ──────────────────────────────────────────────────────────

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'daysOfWeek': _normalizeDays(daysOfWeek),
        'startHour': _normalizeHour(startHour),
        'startMinute': _normalizeMinute(startMinute),
        'endHour': _normalizeHour(endHour),
        'endMinute': _normalizeMinute(endMinute),
        'mode': mode.index,
        'enabled': enabled,
        'priority': priority.clamp(minPriority, maxPriority),
      };

  factory ScheduleRule.fromJson(Map<String, dynamic> json) {
    final modeIdx = ((json['mode'] as num?)?.toInt() ?? 0)
        .clamp(0, ScheduleRuleMode.values.length - 1);
    final rawDays = (json['daysOfWeek'] as List<dynamic>? ?? []).map((e) {
      if (e is num) return e.toInt();
      if (e is String) return int.tryParse(e);
      return null;
    }).whereType<int>();
    final idCandidate = json['id']?.toString().trim() ?? '';
    return ScheduleRule(
      id: idCandidate.isNotEmpty
          ? idCandidate
          : DateTime.now().millisecondsSinceEpoch.toString(),
      name: json['name'] as String? ?? '',
      daysOfWeek: _normalizeDays(rawDays),
      startHour: _normalizeHour(((json['startHour'] as num?)?.toInt() ?? 0)),
      startMinute:
          _normalizeMinute(((json['startMinute'] as num?)?.toInt() ?? 0)),
      endHour: _normalizeHour(((json['endHour'] as num?)?.toInt() ?? 23)),
      endMinute: _normalizeMinute(((json['endMinute'] as num?)?.toInt() ?? 59)),
      mode: ScheduleRuleMode.values[modeIdx],
      enabled: json['enabled'] as bool? ?? true,
      priority: (((json['priority'] as num?)?.toInt() ?? defaultPriority))
          .clamp(minPriority, maxPriority),
    );
  }

  ScheduleRule copyWith({
    String? id,
    String? name,
    List<int>? daysOfWeek,
    int? startHour,
    int? startMinute,
    int? endHour,
    int? endMinute,
    ScheduleRuleMode? mode,
    bool? enabled,
    int? priority,
  }) {
    return ScheduleRule(
      id: id ?? this.id,
      name: name ?? this.name,
      daysOfWeek: _normalizeDays(daysOfWeek ?? List<int>.from(this.daysOfWeek)),
      startHour: _normalizeHour(startHour ?? this.startHour),
      startMinute: _normalizeMinute(startMinute ?? this.startMinute),
      endHour: _normalizeHour(endHour ?? this.endHour),
      endMinute: _normalizeMinute(endMinute ?? this.endMinute),
      mode: mode ?? this.mode,
      enabled: enabled ?? this.enabled,
      priority: (priority ?? this.priority).clamp(minPriority, maxPriority),
    );
  }

  // ── List serialisation helpers ─────────────────────────────────────────────

  static String encodeList(List<ScheduleRule> rules) =>
      jsonEncode(rules.map((r) => r.toJson()).toList());

  static List<ScheduleRule> decodeList(String json) {
    try {
      final list = jsonDecode(json) as List<dynamic>;
      return list
          .map((e) => ScheduleRule.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }
}

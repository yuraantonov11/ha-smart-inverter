import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/schedule_rule.dart';
import 'log_service.dart';

/// Manages time-based HEMS override rules.
///
/// Rules are persisted in [SharedPreferences] under [_prefsKey].
/// The service is a [ChangeNotifier] so the UI can react to changes.
class ScheduleRulesService extends ChangeNotifier {
  static const _prefsKey = 'schedule_rules_v1';

  ScheduleRulesService._();
  static final instance = ScheduleRulesService._();

  /// For unit tests only: creates a fresh in-memory instance with pre-seeded rules.
  factory ScheduleRulesService.testInstance(List<ScheduleRule> initial) {
    final svc = ScheduleRulesService._();
    svc._rules.addAll(initial);
    return svc;
  }

  final List<ScheduleRule> _rules = [];

  List<ScheduleRule> get rules => List.unmodifiable(_rules);

  // ── Persistence ───────────────────────────────────────────────────────────

  Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      _rules.clear();
      if (raw != null && raw.isNotEmpty) {
        _rules.addAll(ScheduleRule.decodeList(raw));
        LogService.log('📅 ScheduleRules: loaded ${_rules.length} rules');
      }
    } catch (e) {
      LogService.log('❌ ScheduleRules: load failed', error: e);
    }
    notifyListeners();
  }

  Future<void> _save() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, ScheduleRule.encodeList(_rules));
    } catch (e) {
      LogService.log('❌ ScheduleRules: save failed', error: e);
    }
  }

  // ── CRUD ──────────────────────────────────────────────────────────────────

  Future<void> addRule(ScheduleRule rule) async {
    _rules.add(rule);
    notifyListeners();
    await _save();
    LogService.log('📅 ScheduleRules: added rule "${rule.name}" (${rule.id})');
  }

  Future<void> updateRule(ScheduleRule updated) async {
    final idx = _rules.indexWhere((r) => r.id == updated.id);
    if (idx == -1) return;
    _rules[idx] = updated;
    notifyListeners();
    await _save();
    LogService.log(
        '📅 ScheduleRules: updated rule "${updated.name}" (${updated.id})');
  }

  Future<void> deleteRule(String id) async {
    _rules.removeWhere((r) => r.id == id);
    notifyListeners();
    await _save();
    LogService.log('📅 ScheduleRules: deleted rule $id');
  }

  Future<void> toggleRule(String id) async {
    final idx = _rules.indexWhere((r) => r.id == id);
    if (idx == -1) return;
    _rules[idx] = _rules[idx].copyWith(enabled: !_rules[idx].enabled);
    notifyListeners();
    await _save();
  }

  // ── Runtime query ─────────────────────────────────────────────────────────

  /// Returns the active rule with the *highest* [priority] at [now].
  /// When priorities are equal, list order (first added) is the tiebreak.
  /// Returns null if no rule is active.
  ScheduleRule? getActiveRuleNow({DateTime? now}) {
    final dt = now ?? DateTime.now();
    ScheduleRule? winner;
    for (final rule in _rules) {
      if (!rule.isActiveAt(dt)) continue;
      if (winner == null || rule.priority > winner.priority) {
        winner = rule;
      }
    }
    return winner;
  }
}

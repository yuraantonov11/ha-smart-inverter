import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/models/schedule_rule.dart';
import 'package:inverter_app/services/schedule_rules_service.dart';

void main() {
  group('ScheduleRule.isActiveAt', () {
    test('activates in same-day window with inclusive start and exclusive end',
        () {
      const rule = ScheduleRule(
        id: 'r1',
        name: 'Day rule',
        daysOfWeek: [DateTime.monday],
        startHour: 8,
        startMinute: 0,
        endHour: 10,
        endMinute: 0,
        mode: ScheduleRuleMode.adaptive,
      );

      expect(rule.isActiveAt(DateTime(2026, 5, 11, 7, 59)), isFalse);
      expect(rule.isActiveAt(DateTime(2026, 5, 11, 8, 0)), isTrue);
      expect(rule.isActiveAt(DateTime(2026, 5, 11, 9, 59)), isTrue);
      expect(rule.isActiveAt(DateTime(2026, 5, 11, 10, 0)), isFalse);
      expect(rule.isActiveAt(DateTime(2026, 5, 12, 8, 30)), isFalse);
    });

    test('applies overnight window to next day early hours', () {
      const rule = ScheduleRule(
        id: 'r2',
        name: 'Night rule',
        daysOfWeek: [DateTime.monday],
        startHour: 23,
        startMinute: 0,
        endHour: 6,
        endMinute: 0,
        mode: ScheduleRuleMode.arbitrage,
      );

      // Monday late segment.
      expect(rule.isActiveAt(DateTime(2026, 5, 11, 23, 30)), isTrue);
      // Tuesday early segment still belongs to Monday rule.
      expect(rule.isActiveAt(DateTime(2026, 5, 12, 2, 0)), isTrue);
      // End boundary is exclusive.
      expect(rule.isActiveAt(DateTime(2026, 5, 12, 6, 0)), isFalse);
      // Tuesday late night is not active because Tuesday is not selected.
      expect(rule.isActiveAt(DateTime(2026, 5, 12, 23, 30)), isFalse);
    });

    test(
        'does not activate when previous day is not selected for overnight rule',
        () {
      const rule = ScheduleRule(
        id: 'r3',
        name: 'Weekend night',
        daysOfWeek: [DateTime.saturday],
        startHour: 22,
        startMinute: 0,
        endHour: 5,
        endMinute: 0,
        mode: ScheduleRuleMode.storm,
      );

      // Friday->Saturday early morning should not be active.
      expect(rule.isActiveAt(DateTime(2026, 5, 9, 2, 0)), isFalse);
      // Saturday late night should be active.
      expect(rule.isActiveAt(DateTime(2026, 5, 9, 22, 30)), isTrue);
      // Sunday early morning should still be active (carried from Saturday).
      expect(rule.isActiveAt(DateTime(2026, 5, 10, 2, 0)), isTrue);
    });
  });

  group('ScheduleRule.fromJson', () {
    test('normalizes invalid time/day values and mixed day types', () {
      final parsed = ScheduleRule.fromJson({
        'id': 123,
        'name': 'Raw',
        'daysOfWeek': [0, 1, 8, '5', 'bad', 1],
        'startHour': 99,
        'startMinute': -1,
        'endHour': -5,
        'endMinute': 70,
        'mode': 99,
        'enabled': true,
      });

      expect(parsed.id, '123');
      expect(parsed.daysOfWeek, [1, 5, 7]);
      expect(parsed.startHour, 23);
      expect(parsed.startMinute, 0);
      expect(parsed.endHour, 0);
      expect(parsed.endMinute, 59);
      expect(parsed.mode, ScheduleRuleMode.storm);
    });

    test('deserialises priority and clamps out-of-range values', () {
      final low = ScheduleRule.fromJson({
        'id': 'x',
        'name': '',
        'daysOfWeek': [1],
        'startHour': 8,
        'startMinute': 0,
        'endHour': 9,
        'endMinute': 0,
        'mode': 0,
        'enabled': true,
        'priority': -99,
      });
      expect(low.priority, ScheduleRule.minPriority);

      final high = ScheduleRule.fromJson({
        'id': 'y',
        'name': '',
        'daysOfWeek': [1],
        'startHour': 8,
        'startMinute': 0,
        'endHour': 9,
        'endMinute': 0,
        'mode': 0,
        'enabled': true,
        'priority': 999,
      });
      expect(high.priority, ScheduleRule.maxPriority);

      final missing = ScheduleRule.fromJson({
        'id': 'z',
        'name': '',
        'daysOfWeek': [1],
        'startHour': 8,
        'startMinute': 0,
        'endHour': 9,
        'endMinute': 0,
        'mode': 0,
        'enabled': true,
        // no 'priority' key → should default
      });
      expect(missing.priority, ScheduleRule.defaultPriority);
    });

    test('encode/decode roundtrip preserves priority and rule count', () {
      const rules = [
        ScheduleRule(
          id: 'a',
          name: 'A',
          daysOfWeek: [1, 2, 3],
          startHour: 7,
          startMinute: 30,
          endHour: 9,
          endMinute: 0,
          mode: ScheduleRuleMode.adaptive,
          priority: 3,
        ),
        ScheduleRule(
          id: 'b',
          name: 'B',
          daysOfWeek: [6, 7],
          startHour: 23,
          startMinute: 0,
          endHour: 6,
          endMinute: 0,
          mode: ScheduleRuleMode.arbitrage,
          priority: 8,
        ),
      ];

      final encoded = ScheduleRule.encodeList(rules);
      final decoded = ScheduleRule.decodeList(encoded);

      expect(decoded.length, 2);
      expect(decoded.first.id, 'a');
      expect(decoded.first.priority, 3);
      expect(decoded.last.id, 'b');
      expect(decoded.last.priority, 8);
      expect(decoded.last.isOvernight, isTrue);
    });
  });

  group('ScheduleRulesService conflict resolution', () {
    /// Builds a fresh service with the given rules injected directly
    /// (bypassing SharedPreferences).
    ScheduleRulesService _serviceWith(List<ScheduleRule> rules) {
      final svc = ScheduleRulesService.testInstance(rules);
      return svc;
    }

    test('returns the rule with highest priority when two overlap', () {
      final now = DateTime(2026, 5, 11, 10, 0); // Monday 10:00
      final low = ScheduleRule(
        id: '1',
        name: 'Low',
        daysOfWeek: const [DateTime.monday],
        startHour: 8,
        startMinute: 0,
        endHour: 12,
        endMinute: 0,
        mode: ScheduleRuleMode.adaptive,
        priority: 3,
      );
      final high = ScheduleRule(
        id: '2',
        name: 'High',
        daysOfWeek: const [DateTime.monday],
        startHour: 9,
        startMinute: 0,
        endHour: 11,
        endMinute: 0,
        mode: ScheduleRuleMode.storm,
        priority: 7,
      );
      final svc = _serviceWith([low, high]);

      final winner = svc.getActiveRuleNow(now: now);
      expect(winner?.id, '2');
      expect(winner?.mode, ScheduleRuleMode.storm);
    });

    test('returns first rule in list when priorities are equal', () {
      final now = DateTime(2026, 5, 11, 10, 0); // Monday 10:00
      final first = ScheduleRule(
        id: 'first',
        name: 'First',
        daysOfWeek: const [DateTime.monday],
        startHour: 8,
        startMinute: 0,
        endHour: 12,
        endMinute: 0,
        mode: ScheduleRuleMode.arbitrage,
        priority: 5,
      );
      final second = ScheduleRule(
        id: 'second',
        name: 'Second',
        daysOfWeek: const [DateTime.monday],
        startHour: 9,
        startMinute: 0,
        endHour: 11,
        endMinute: 0,
        mode: ScheduleRuleMode.storm,
        priority: 5,
      );
      final svc = _serviceWith([first, second]);

      final winner = svc.getActiveRuleNow(now: now);
      expect(winner?.id, 'first');
    });

    test('returns null when no rules are active', () {
      final now = DateTime(2026, 5, 11, 10, 0);
      final svc = _serviceWith([
        ScheduleRule(
          id: 'x',
          name: 'Night only',
          daysOfWeek: const [DateTime.monday],
          startHour: 23,
          startMinute: 0,
          endHour: 6,
          endMinute: 0,
          mode: ScheduleRuleMode.arbitrage,
          priority: 5,
        ),
      ]);
      expect(svc.getActiveRuleNow(now: now), isNull);
    });

    test('ignores disabled rules', () {
      final now = DateTime(2026, 5, 11, 10, 0);
      final svc = _serviceWith([
        ScheduleRule(
          id: 'd',
          name: 'Disabled',
          daysOfWeek: const [DateTime.monday],
          startHour: 8,
          startMinute: 0,
          endHour: 12,
          endMinute: 0,
          mode: ScheduleRuleMode.storm,
          priority: 10,
          enabled: false,
        ),
        ScheduleRule(
          id: 'e',
          name: 'Enabled',
          daysOfWeek: const [DateTime.monday],
          startHour: 8,
          startMinute: 0,
          endHour: 12,
          endMinute: 0,
          mode: ScheduleRuleMode.adaptive,
          priority: 3,
        ),
      ]);
      final winner = svc.getActiveRuleNow(now: now);
      expect(winner?.id, 'e');
    });
  });
}

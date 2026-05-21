import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/services/grid_outage_detector.dart';

void main() {
  group('GridOutageDetector transitions', () {
    test('does not emit transition on first sample (bootstraps baseline)', () {
      final detector = GridOutageDetector();

      final decision = detector.evaluate(
        gridVoltage: 230,
        now: DateTime(2026, 5, 13, 10, 0),
      );

      expect(decision.transition, GridTransition.none);
      expect(decision.gridAvailable, isTrue);
      expect(decision.instabilityAlert, isFalse);
    });

    test('requires consecutive low-voltage samples to declare outage', () {
      final detector = GridOutageDetector(
        consecutiveDownSamples: 2,
        consecutiveUpSamples: 2,
      );
      final t0 = DateTime(2026, 5, 13, 10, 0);

      detector.evaluate(gridVoltage: 230, now: t0);
      final d1 = detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 1)));
      final d2 = detector.evaluate(
          gridVoltage: 85, now: t0.add(const Duration(minutes: 2)));

      expect(d1.transition, GridTransition.none);
      expect(d2.transition, GridTransition.outage);
      expect(d2.gridAvailable, isFalse);
    });

    test('requires consecutive high-voltage samples to declare restore', () {
      final detector = GridOutageDetector(
        consecutiveDownSamples: 2,
        consecutiveUpSamples: 2,
      );
      final t0 = DateTime(2026, 5, 13, 10, 0);

      detector.evaluate(gridVoltage: 230, now: t0);
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 1)));
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 2))); // outage

      final d1 = detector.evaluate(
          gridVoltage: 140, now: t0.add(const Duration(minutes: 3)));
      final d2 = detector.evaluate(
          gridVoltage: 150, now: t0.add(const Duration(minutes: 4)));

      expect(d1.transition, GridTransition.none);
      expect(d2.transition, GridTransition.restored);
      expect(d2.gridAvailable, isTrue);
    });
  });

  group('GridOutageDetector instability', () {
    test('emits instability alert after rapid transition bursts', () {
      final detector = GridOutageDetector(
        consecutiveDownSamples: 1,
        consecutiveUpSamples: 1,
        instabilityTransitionThreshold: 4,
        instabilityWindow: const Duration(minutes: 15),
        instabilityCooldown: const Duration(hours: 1),
      );
      final t0 = DateTime(2026, 5, 13, 10, 0);

      detector.evaluate(gridVoltage: 230, now: t0); // init
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 1))); // outage 1
      detector.evaluate(
          gridVoltage: 150,
          now: t0.add(const Duration(minutes: 2))); // restore 2
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 3))); // outage 3
      final d4 = detector.evaluate(
          gridVoltage: 150,
          now: t0.add(const Duration(minutes: 4))); // restore 4

      expect(d4.instabilityAlert, isTrue);
    });

    test('respects instability cooldown and re-alerts after cooldown', () {
      final detector = GridOutageDetector(
        consecutiveDownSamples: 1,
        consecutiveUpSamples: 1,
        instabilityTransitionThreshold: 2,
        instabilityWindow: const Duration(minutes: 15),
        instabilityCooldown: const Duration(hours: 1),
      );
      final t0 = DateTime(2026, 5, 13, 10, 0);

      detector.evaluate(gridVoltage: 230, now: t0);
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 1))); // trans1
      final firstAlert = detector.evaluate(
          gridVoltage: 150,
          now: t0.add(const Duration(minutes: 2))); // trans2 -> alert
      expect(firstAlert.instabilityAlert, isTrue);

      // Another burst within cooldown should not alert.
      detector.evaluate(
          gridVoltage: 80, now: t0.add(const Duration(minutes: 10))); // trans1
      final suppressed = detector.evaluate(
          gridVoltage: 150, now: t0.add(const Duration(minutes: 11))); // trans2
      expect(suppressed.instabilityAlert, isFalse);

      // After cooldown, burst alerts again.
      detector.evaluate(
          gridVoltage: 80,
          now: t0.add(const Duration(hours: 1, minutes: 2))); // trans1
      final secondAlert = detector.evaluate(
          gridVoltage: 150,
          now: t0.add(const Duration(hours: 1, minutes: 3))); // trans2
      expect(secondAlert.instabilityAlert, isTrue);
    });
  });
}

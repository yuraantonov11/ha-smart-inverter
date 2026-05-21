enum GridTransition {
  none,
  outage,
  restored,
}

class GridOutageDecision {
  final bool gridAvailable;
  final GridTransition transition;
  final bool instabilityAlert;

  const GridOutageDecision({
    required this.gridAvailable,
    required this.transition,
    required this.instabilityAlert,
  });
}

/// Stateful detector for robust grid outage/restore events.
///
/// - Uses voltage hysteresis (down/up thresholds)
/// - Requires consecutive samples before declaring transitions
/// - Emits instability alerts when too many transitions happen in a short window
class GridOutageDetector {
  final double outageVoltageThreshold;
  final double restoreVoltageThreshold;
  final int consecutiveDownSamples;
  final int consecutiveUpSamples;
  final int instabilityTransitionThreshold;
  final Duration instabilityWindow;
  final Duration instabilityCooldown;

  bool _isInitialized = false;
  bool _gridAvailable = true;
  int _downCount = 0;
  int _upCount = 0;
  final List<DateTime> _transitionTimes = <DateTime>[];
  DateTime? _lastInstabilityAlertAt;

  GridOutageDetector({
    this.outageVoltageThreshold = 90.0,
    this.restoreVoltageThreshold = 130.0,
    this.consecutiveDownSamples = 2,
    this.consecutiveUpSamples = 2,
    this.instabilityTransitionThreshold = 4,
    this.instabilityWindow = const Duration(minutes: 15),
    this.instabilityCooldown = const Duration(hours: 1),
  });

  bool get isInitialized => _isInitialized;
  bool get gridAvailable => _gridAvailable;

  GridOutageDecision evaluate({
    required double gridVoltage,
    DateTime? now,
  }) {
    final ts = now ?? DateTime.now();

    if (!_isInitialized) {
      _isInitialized = true;
      _gridAvailable = gridVoltage >= restoreVoltageThreshold;
      _downCount = 0;
      _upCount = 0;
      return GridOutageDecision(
        gridAvailable: _gridAvailable,
        transition: GridTransition.none,
        instabilityAlert: false,
      );
    }

    var transition = GridTransition.none;

    if (_gridAvailable) {
      if (gridVoltage <= outageVoltageThreshold) {
        _downCount += 1;
        if (_downCount >= consecutiveDownSamples) {
          _gridAvailable = false;
          _downCount = 0;
          _upCount = 0;
          transition = GridTransition.outage;
          _recordTransition(ts);
        }
      } else {
        _downCount = 0;
      }
    } else {
      if (gridVoltage >= restoreVoltageThreshold) {
        _upCount += 1;
        if (_upCount >= consecutiveUpSamples) {
          _gridAvailable = true;
          _upCount = 0;
          _downCount = 0;
          transition = GridTransition.restored;
          _recordTransition(ts);
        }
      } else {
        _upCount = 0;
      }
    }

    final instability = _shouldEmitInstabilityAlert(ts);

    return GridOutageDecision(
      gridAvailable: _gridAvailable,
      transition: transition,
      instabilityAlert: instability,
    );
  }

  void reset({bool? gridAvailable}) {
    _isInitialized = false;
    _gridAvailable = gridAvailable ?? true;
    _downCount = 0;
    _upCount = 0;
    _transitionTimes.clear();
    _lastInstabilityAlertAt = null;
  }

  void _recordTransition(DateTime now) {
    _transitionTimes.add(now);
    _transitionTimes.removeWhere(
      (t) => now.difference(t) > instabilityWindow,
    );
  }

  bool _shouldEmitInstabilityAlert(DateTime now) {
    if (_transitionTimes.length < instabilityTransitionThreshold) {
      return false;
    }

    final cooldownPassed = _lastInstabilityAlertAt == null ||
        now.difference(_lastInstabilityAlertAt!) >= instabilityCooldown;

    if (!cooldownPassed) {
      return false;
    }

    _lastInstabilityAlertAt = now;
    _transitionTimes.clear();
    return true;
  }
}

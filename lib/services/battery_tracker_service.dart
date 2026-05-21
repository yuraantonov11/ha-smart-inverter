import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'log_service.dart';

class BatteryTrackerService extends ChangeNotifier {
  static const String _cycleCountKey = 'battery_cycle_count_v1';
  static const String _inLowStateKey = 'battery_in_low_state_v1';
  static const int _ratedCycleLife = 2000;
  static const double _lowThreshold = 30.0;
  static const double _highThreshold = 80.0;

  static BatteryTrackerService? _instance;

  static BatteryTrackerService get instance {
    _instance ??= BatteryTrackerService._();
    return _instance!;
  }

  BatteryTrackerService._();

  int _cycleCount = 0;
  bool _inLowState = false;
  bool _loaded = false;

  int get cycleCount => _cycleCount;

  double estimatedSohPercent({DateTime? installDate}) {
    final cycleDegrade = (_cycleCount / _ratedCycleLife).clamp(0.0, 0.8);
    var ageFactor = 1.0;
    if (installDate != null) {
      final years = DateTime.now().difference(installDate).inDays / 365.0;
      ageFactor = (1.0 - years * 0.03).clamp(0.0, 1.0);
    }
    return ((1.0 - cycleDegrade) * ageFactor * 100.0).clamp(0.0, 100.0);
  }

  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      _cycleCount = prefs.getInt(_cycleCountKey) ?? 0;
      _inLowState = prefs.getBool(_inLowStateKey) ?? false;
      LogService.log(
          'BatteryTracker loaded: cycles=$_cycleCount inLow=$_inLowState');
    } catch (e) {
      LogService.log('BatteryTrackerService.load error: $e');
    }
  }

  bool trackSoc(double soc) {
    if (!_inLowState && soc <= _lowThreshold) {
      _inLowState = true;
      _saveAsync();
    }
    if (_inLowState && soc >= _highThreshold) {
      _inLowState = false;
      _cycleCount++;
      notifyListeners();
      _saveAsync();
      LogService.log('Battery cycle completed: #$_cycleCount');
      return true;
    }
    return false;
  }

  Future<void> resetCycleCount() async {
    _cycleCount = 0;
    _inLowState = false;
    notifyListeners();
    await _saveAsync();
  }

  Future<void> _saveAsync() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt(_cycleCountKey, _cycleCount);
      await prefs.setBool(_inLowStateKey, _inLowState);
    } catch (e) {
      LogService.log('BatteryTrackerService._save error: $e');
    }
  }
}

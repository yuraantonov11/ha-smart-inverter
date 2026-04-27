import '../models/hems_optimization_profile.dart';

class DemandForecastService {
  static const double _minLoadW = 100.0;
  static const double _maxLoadW = 12000.0;

  Map<int, double> updateEwmaProfile(
    Map<int, double> currentProfile, {
    required DateTime timestamp,
    required double loadW,
    double alpha = 0.25,
  }) {
    final h = timestamp.hour;
    final sample = loadW.clamp(_minLoadW, _maxLoadW).toDouble();
    final old = currentProfile[h] ?? sample;
    final updated = (alpha * sample) + ((1 - alpha) * old);
    return {
      ...currentProfile,
      h: updated,
    };
  }

  Map<int, double> buildDefaultProfile() {
    return {
      0: 250,
      1: 200,
      2: 200,
      3: 200,
      4: 200,
      5: 250,
      6: 500,
      7: 1500,
      8: 1200,
      9: 600,
      10: 500,
      11: 500,
      12: 500,
      13: 500,
      14: 600,
      15: 800,
      16: 900,
      17: 1500,
      18: 2000,
      19: 3000,
      20: 2500,
      21: 2000,
      22: 1000,
      23: 500,
    };
  }

  DemandForecastData toDemandForecastData(Map<int, double> profile) {
    final metrics = <int, DemandMetrics>{};
    for (var h = 0; h < 24; h++) {
      final base = (profile[h] ?? 500.0).clamp(_minLoadW, _maxLoadW).toDouble();
      metrics[h] = DemandMetrics(
        p25: base * 0.8,
        p50: base,
        p75: base * 1.2,
        p90: base * 1.35,
      );
    }
    return DemandForecastData(hourlyMetrics: metrics);
  }
}

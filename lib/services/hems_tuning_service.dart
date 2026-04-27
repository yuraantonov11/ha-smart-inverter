/// HEMS Tuning Service — auto-compute adaptive parameters
/// Based on system characteristics, history, and forecasts
library;

import 'package:intl/intl.dart';
import 'dart:math';
import '../models/hems_optimization_profile.dart';
import 'log_service.dart';

class HemsTuningService {
  final HemsOptimizationProfile profile;

  HemsTuningService(this.profile);

  /// Calculate sunrise/sunset for given date and location
  /// Simple astronomical formula (good enough for ±5 min accuracy)
  static DateTime calculateSunrise(
      double latitude, double longitude, DateTime date) {
    // Simplified algorithm; for production consider solar_calculator package
    // This is a rough estimate based on latitude and day of year
    final dayOfYear = date.difference(DateTime(date.year, 1, 1)).inDays;
    final declinationAngle =
        23.44 * sin((2 * pi * (dayOfYear - 81)) / 365.0) * (pi / 180.0);

    final B = (latitude * pi / 180.0);
    const h = -0.833 * pi / 180.0; // sunset/sunrise angle
    final numerator = sin(h) - sin(B) * sin(declinationAngle);
    final denominator = cos(B) * cos(declinationAngle);

    if (denominator == 0) {
      // Polar midnight or midnight sun
      return DateTime(date.year, date.month, date.day, 6, 0);
    }

    final cosH = numerator / denominator;
    if (cosH.abs() > 1) {
      // Sun doesn't rise/set on this day (polar regions)
      return DateTime(date.year, date.month, date.day, 6, 0);
    }

    final hourAngle = acos(cosH) * 180.0 / pi / 15.0; // convert to hours
    final solarNoon = 12.0 - (longitude / 15.0); // rough UTC correction
    final sunrise = solarNoon - hourAngle;

    final hour = sunrise.toInt();
    final minute = ((sunrise - hour) * 60).toInt();
    return DateTime(date.year, date.month, date.day, hour.clamp(0, 23),
        minute.clamp(0, 59));
  }

  static DateTime calculateSunset(
      double latitude, double longitude, DateTime date) {
    final sunrise = calculateSunrise(latitude, longitude, date);
    // Sunset is roughly 12 hours later (for temperate zones)
    // More accurate: use the complement angle
    return sunrise
        .add(const Duration(hours: 12, minutes: 20)); // rough estimate
  }

  /// Update time windows based on GPS location (if available)
  Future<void> updateAstronomicalWindows({
    required double latitude,
    required double longitude,
    DateTime? forDate,
  }) async {
    final date = forDate ?? DateTime.now();
    final sunrise = calculateSunrise(latitude, longitude, date);
    final sunset = calculateSunset(latitude, longitude, date);

    profile.timeWindows = DailyTimeWindows.fromAstronomical(
      sunrise: sunrise,
      sunset: sunset,
    );

    LogService.log(
      '🌅 HEMS tuning: astronomical windows updated — sunrise=${DateFormat('HH:mm').format(sunrise)}, sunset=${DateFormat('HH:mm').format(sunset)}',
    );
  }

  /// Compute adaptive PV surplus threshold based on live variance
  double computeAdaptivePvSurplus(List<double> recentSurplusHistory) {
    if (recentSurplusHistory.isEmpty) {
      return profile.getAdaptivePvSurplusEnter();
    }

    // Calculate volatility (standard deviation)
    final mean = recentSurplusHistory.reduce((a, b) => a + b) /
        recentSurplusHistory.length;
    final variance = recentSurplusHistory
            .map((x) => pow(x - mean, 2).toDouble())
            .reduce((a, b) => a + b) /
        recentSurplusHistory.length;
    final stdDev = sqrt(variance);

    // Store for learning
    profile.learningMetrics['pvVariance'] = stdDev;

    // Base threshold adjusted for volatility
    final base = 0.10 * profile.pvPeakW;
    final hour = DateTime.now().hour;
    final hourPenalty = (hour < 10 || hour > 16) ? 1.5 : 0.9;

    // High variance = cloudy, need higher threshold to avoid flapping
    final variancePenalty = min(1.5, 1.0 + (stdDev / 200.0));

    final adaptive = base * hourPenalty * variancePenalty;
    return adaptive.clamp(70, 600);
  }

  /// Compute adaptive dwell time based on PV stability
  Duration computeAdaptiveDwell(List<double> recentSurplus) {
    if (recentSurplus.isEmpty) return const Duration(minutes: 15);

    final mean = recentSurplus.reduce((a, b) => a + b) / recentSurplus.length;
    final variance = recentSurplus
            .map((x) => pow(x - mean, 2).toDouble())
            .reduce((a, b) => a + b) /
        recentSurplus.length;
    final stdDev = sqrt(variance);

    // Cloudy (high variance) → short dwell (8 min)
    // Clear (low variance) → long dwell (25 min)
    if (stdDev > 300) return const Duration(minutes: 8);
    if (stdDev < 50) return const Duration(minutes: 25);
    return const Duration(minutes: 15);
  }

  /// Compute reserve SOC based on battery health, tariff, strategy
  double computeAdaptiveReserveSoc({
    required double baseReserveSoc,
    required bool isTimeOfUseTariff,
  }) {
    var adaptive = baseReserveSoc;

    // Battery health factor
    adaptive = profile.batteryHealth
        .getAdaptiveReserveSoc(baseReserveSoc: baseReserveSoc);

    // Strategy factor
    if (profile.optimizationStrategy == HemsOptimizationStrategy.solarMaxed) {
      adaptive -= 2; // more aggressive
    } else if (profile.optimizationStrategy ==
        HemsOptimizationStrategy.batteryLife) {
      adaptive += 3; // more conservative
    }

    // TOU tariff factor: if cheap night charge available, can use lower reserve
    if (isTimeOfUseTariff && profile.tariffForecast != null) {
      adaptive -= 1; // can afford lower reserve due to cheap charging
    }

    return adaptive.clamp(15, 35);
  }

  /// Get recommended strategy based on profile + forecasts + mode
  String recommendOptimizationStrategy() {
    // If grid reliability is poor → gridReliance
    if (profile.gridForecast?.shouldPrechargeForStability() ?? false) {
      return 'gridReliance: precharging for stability';
    }

    // If thermal load needs heating → solarMaxed (maximize PV for heat)
    if (profile.thermalLoad?.isHeatingNeeded() ?? false) {
      return 'solarMaxed: thermal demand detected';
    }

    // If TOU tariff + low PV → economical
    if (profile.tariffForecast != null && profile.pvPeakW < 2000) {
      return 'economical: TOU tariff + small PV';
    }

    // If large battery + good PV → batteryLife (care for longevity)
    if (profile.batteryCapacityKwh > 10 && profile.pvPeakW > 3000) {
      return 'batteryLife: large system, extend cycles';
    }

    return 'hybrid: balanced approach';
  }

  /// Cap adaptive parameter drift to prevent sudden jumps
  double capDrift(double oldValue, double newValue, double maxDriftPercent) {
    final maxChange = oldValue * (maxDriftPercent / 100.0);
    return newValue.clamp(oldValue - maxChange, oldValue + maxChange);
  }

  /// Log transitions when parameters change
  void logParameterUpdate(String paramName, double oldValue, double newValue) {
    if ((oldValue - newValue).abs() > 0.01) {
      LogService.log(
        '🔧 HEMS: parameter adjusted — $paramName: ${oldValue.toStringAsFixed(1)} → ${newValue.toStringAsFixed(1)}',
      );
    }
  }
}

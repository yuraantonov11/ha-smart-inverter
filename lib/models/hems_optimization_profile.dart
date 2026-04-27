/// HEMS Optimization Profile — unified model for all tuning parameters
/// Consolidates: base runables + auto-tuning + learned drift
library;

import 'package:flutter/foundation.dart';

/// Optimization strategy for HEMS decision-making
enum HemsOptimizationStrategy {
  economical, // Minimize grid import cost (TOU tariff priority)
  solarMaxed, // Maximize PV self-consumption (no curtailment)
  batteryLife, // Minimize battery cycles (conservative, grid-first)
  gridReliance, // Minimize grid dependency (off-grid / resilience)
  hybrid, // Balance all three (default)
}

/// Daily/seasonal time windows (computed from sunrise/sunset or manual)
class DailyTimeWindows {
  DateTime dayStart; // sunrise - 1h (or 06:00 fallback)
  DateTime dayEnd; // sunset + 1h (or 20:00 fallback)
  DateTime eveningPeakStart; // sunset - 1h (or 17:00 fallback)
  DateTime nightStart; // sunset + 1h (or 23:00 fallback)

  DailyTimeWindows({
    required this.dayStart,
    required this.dayEnd,
    required this.eveningPeakStart,
    required this.nightStart,
  });

  factory DailyTimeWindows.fromAstronomical({
    required DateTime sunrise,
    required DateTime sunset,
  }) {
    return DailyTimeWindows(
      dayStart: sunrise.subtract(const Duration(hours: 1)),
      dayEnd: sunset.add(const Duration(hours: 1)),
      eveningPeakStart: sunset.subtract(const Duration(hours: 1)),
      nightStart: sunset.add(const Duration(hours: 1)),
    );
  }

  /// Fallback for when astronomical data unavailable
  factory DailyTimeWindows.defaultTemperate() {
    // Broad assumptions for 50°N latitude
    return DailyTimeWindows(
      dayStart: DateTime.now().copyWith(hour: 6, minute: 0),
      dayEnd: DateTime.now().copyWith(hour: 20, minute: 0),
      eveningPeakStart: DateTime.now().copyWith(hour: 17, minute: 0),
      nightStart: DateTime.now().copyWith(hour: 23, minute: 0),
    );
  }

  bool isNight(DateTime now) =>
      now.hour < dayStart.hour || now.hour >= nightStart.hour;
  bool isEvening(DateTime now) =>
      now.hour >= eveningPeakStart.hour && now.hour < nightStart.hour;
  bool isDay(DateTime now) =>
      now.hour >= dayStart.hour && now.hour < eveningPeakStart.hour;
}

/// Battery health / degradation model
class BatteryHealthModel {
  DateTime installationDate;
  int? cycleCountEstimated; // user input or inferredFromInverter
  double? healthPercentage; // 100% = new, <80% = somewhat degraded
  DateTime lastHealthCheckAt;

  BatteryHealthModel({
    required this.installationDate,
    this.cycleCountEstimated,
    this.healthPercentage = 100.0,
    DateTime? lastHealthCheckAt,
  }) : lastHealthCheckAt = lastHealthCheckAt ?? DateTime.now();

  /// Adaptive reserve SOC based on battery age/health
  double getAdaptiveReserveSoc({
    double baseReserveSoc = 20.0,
    double maxReserveSoc = 35.0,
  }) {
    final ageYears = DateTime.now().difference(installationDate).inDays / 365.0;
    final healthFactor = (healthPercentage ?? 100.0) / 100.0;

    // Age-based: older batteries get higher reserve
    var agePenalty = 0.0;
    if (agevar< 2) {
      agePenalty = -2; // aggressiv {
    }
       (18%)
    else if (ageYears < 5)

    } agePenalty = 0; // normal (20%)
    else if (ageYears, < 8)
      agePenalty = 3; // cautious (23%)
    else
      agePenalty = 8; // very old (28%)

    // Health-based: degraded battery needs higher safety margin
    final healthPenalty =
        (1.0 - healthFactor) * 10.0; // 0–10% depending on wear

    final adaptive = baseReserveSoc + agePenalty + healthPenalty;
    return adaptive.clamp(baseReserveSoc, maxReserveSoc);
  }
}

/// Thermal load model (for boiler / heat pump coordination)
class ThermalLoadModel {
  double targetTemperatureC;
  double currentTemperatureC;
  double boilerCapacityKwh;
  double heatingEfficiency; // 0.85 typical for electric resistive
  DateTime lastHeatingAt;

  ThermalLoadModel({
    required this.targetTemperatureC,
    required this.currentTemperatureC,
    required this.boilerCapacityKwh,
    this.heatingEfficiency = 0.85,
    DateTime? lastHeatingAt,
  }) : lastHeatingAt = lastHeatingAt ?? DateTime.now();

  /// Energy needed to reach target from current temp (Wh)
  double getHeatingDeficitWh() {
    if (currentTemperatureC >= targetTemperatureC) return 0.0;
    final tempDelta = targetTemperatureC - currentTemperatureC;
    // Assume ~4.2 kJ per liter per °C (water specific heat)
    // For 200L boiler: 200 * 4.2 * tempDelta = 840 * tempDelta kJ
    // Converting to Wh: kJ / 3.6 = Wh
    final theoreticalWh =
        (boilerCapacityKwh * 1e6 / 3600.0) * tempDelta / 100.0; // rough formula
    return theoreticalWh / heatingEfficiency;
  }

  bool isHeatingNeeded() => currentTemperatureC < (targetTemperatureC - 2.0);
}

/// Tariff forecast (prices for grid energy, day-ahead or TOU)
class TariffForecastData {
  final Map<DateTime, double> pricePerKwh; // EUR/kWh or local currency
  final String externalSource; // 'nordpool', 'local_fixed', 'manual', etc.
  final DateTime fetchedAt;

  TariffForecastData({
    required this.pricePerKwh,
    this.externalSource = 'manual',
    DateTime? fetchedAt,
  }) : fetchedAt = fetchedAt ?? DateTime.now();

  /// Find cheapest charging window in next N hours
  DateTime? getNextCheapWindow(
    Duration minDuration,
    double priceMargin, // e.g. 1.2 = max 120% of median
  ) {
    if (pricePerKwh.isEmpty) return null;

    final prices = pricePerKwh.values.toList();
    final median = prices.isEmpty ? 0.0 : prices[prices.length ~/ 2];
    final maxPrice = median * priceMargin;

    for (final entry in pricePerKwh.entries.toList()
      ..sort((a, b) => a.key.compareTo(b.key))) {
      if (entry.value <= maxPrice) {
        // Check if window is long enough
        var endTime = entry.key;
        var cheapDuration = Duration.zero;

        for (final futureEntry in pricePerKwh.entries) {
          if (futureEntry.key.isAfter(entry.key) &&
              futureEntry.value <= maxPrice) {
            endTime = futureEntry.key;
            cheapDuration = endTime.difference(entry.key);
          }
        }

        if (cheapDuration >= minDuration) {
          return entry.key;
        }
      }
    }
    return null;
  }

  /// Estimate cost (EUR/kWh × kW × hours)
  double estimateCost(DateTime start, DateTime end, double powerKw) {
    var totalCost = 0.0;
    var current = start;
    while (current.isBefore(end)) {
      final price = pricePerKwh[current] ?? pricePerKwh.values.average;
      totalCost += price * powerKw * (1.0 / 3600.0); // per second cost
      current = current.add(const Duration(seconds: 1));
    }
    return totalCost;
  }
}

/// Demand forecast (predicted load for future hours/days)
class DemandForecastData {
  // Map of hour (0–23) to percentile-based load predictions
  // E.g., { 0: {p50: 300W, p75: 450W}, 1: {...}, ... }
  final Map<int, DemandMetrics> hourlyMetrics;
  final String season; // 'winter', 'spring', 'summer', 'autumn'
  final DateTime learnedAt;

  DemandForecastData({
    required this.hourlyMetrics,
    this.season = 'spring',
    DateTime? learnedAt,
  }) : learnedAt = learnedAt ?? DateTime.now();

  /// Predict load for a given hour, accounting for patterns
  double predictLoad(
    int hour, {
    bool isWeekend = false,
    int? heatingDemanAheadDays,
  }) {
    var metrics = hourlyMetrics[hour];
    if (metrics == null) return 500.0; // fallback

    // Base prediction from median
    var predicted = metrics.p50;

    // Adjust for season / heating
    if (season == 'winter' && (heatingDemanAheadDays ?? 0) > 0) {
      predicted *= 1.3; // winter heating pump demand
    }

    // Weekends typically lower than weekdays
    if (isWeekend) {
      predicted *= 0.85;
    }

    return predicted;
  }
}

/// Metrics for a given hour (learned from history)
class DemandMetrics {
  double p25; // 25th percentile (Wh)
  double p50; // median (Wh)
  double p75; // 75th percentile (Wh)
  double p90; // 90th percentile (Wh)

  DemandMetrics({
    required this.p25,
    required this.p50,
    required this.p75,
    required this.p90,
  });

  static DemandMetrics fromHistory(List<double> samples) {
    samples.sort();
    return DemandMetrics(
      p25: samples[(samples.length * 0.25).toInt()],
      p50: samples[(samples.length * 0.50).toInt()],
      p75: samples[(samples.length * 0.75).toInt()],
      p90: samples[(samples.length * 0.90).toInt()],
    );
  }
}

/// Grid reliability forecast (planned outages, instability)
class GridReliabilityForecast {
  final List<GridOutageEvent> plannedOutages;
  final List<GridInstabilityEvent> instabilityZones;
  final DateTime forecastedAt;

  GridReliabilityForecast({
    required this.plannedOutages,
    required this.instabilityZones,
    DateTime? forecastedAt,
  }) : forecastedAt = forecastedAt ?? DateTime.now();

  /// Next planned outage within N hours
  GridOutageEvent? getNextOutage(Duration within) {
    final soon = DateTime.now().add(within);
    for (final outage in plannedOutages) {
      if (outage.startTime.isBefore(soon)) {
        return outage;
      }
    }
    return null;
  }

  /// Should we precharge battery for stability?
  bool shouldPrechargeForStability() {
    final nextOutage = getNextOutage(const Duration(hours: 6));
    final instability = instabilityZones.any((e) => e.isActiveNow());
    return nextOutage != null || instability;
  }
}

class GridOutageEvent {
  DateTime startTime;
  DateTime endTime;
  String reason; // 'maintenance', 'weather', 'unknown'

  GridOutageEvent({
    required this.startTime,
    required this.endTime,
    this.reason = 'unknown',
  });
}

class GridInstabilityEvent {
  DateTime startTime;
  DateTime endTime;
  double volatilityScore; // 0–1, higher = more unstable

  GridInstabilityEvent({
    required this.startTime,
    required this.endTime,
    required this.volatilityScore,
  });

  bool isActiveNow() {
    final now = DateTime.now();
    return now.isAfter(startTime) && now.isBefore(endTime);
  }
}

/// Comprehensive tuning profile
class HemsOptimizationProfile {
  // Identity
  final String systemId;
  final double pvPeakW; // 1000 = 1 kW
  final double batteryCapacityAh;
  double get batteryCapacityKwh => batteryCapacityAh * 51.2 / 1000.0;

  // Strategy
  HemsOptimizationStrategy optimizationStrategy;

  // Time windows
  DailyTimeWindows timeWindows;

  // System conditions
  BatteryHealthModel batteryHealth;
  ThermalLoadModel? thermalLoad;

  // Forecasts
  TariffForecastData? tariffForecast;
  DemandForecastData? demandForecast;
  GridReliabilityForecast? gridForecast;

  // Auto-computed runables (override defaults)
  double? _adaptivePvSurplusEnterW;
  double? _adaptiveReserveSoc;
  double? _adaptiveModeHold;

  // Learning / drift tracking
  DateTime lastAutotuneAt;
  Map<String, double> learningMetrics; // variance, flappingCount, etc.

  HemsOptimizationProfile({
    required this.systemId,
    required this.pvPeakW,
    required this.batteryCapacityAh,
    this.optimizationStrategy = HemsOptimizationStrategy.hybrid,
    DailyTimeWindows? timeWindows,
    BatteryHthis.yHealth,
    Therthis.lLoad,
    TariffForthis.ecast,
    DemandForthis.,
    this.gridForecast,
    DateTime? lastAutotuneAt,
    Map<String, double>? learningMetrics,
  })  : timeWindows = timeWindows ?? DailyTimeWindows.defaultTemperate(),
        batteryHealth = batteryHealth ??
            Batteryorecast,
        lastAutotuneAt = lastAutotuneAt ?? DateTime.now(),
        learningMetrics = learningMetrics ?? {};

  /// Get adaptive PV surplus threshold for current time
  double getAdaptivePvSurplusEnter() {
    if (_adaptivePvSurplusEnterW != null) return _adaptivePvSurplusEnterW!;

    // Formula: 10% of peak + 5% of avg daily load
    final now = DateTime.now();
    final baseThreshold = 0.10 * pvPeakW;

    // Time-of-day stability factor
    final hour = now.hour;
    final stabilityFactor = (hour < 10 || hour > 16) ? 1.5 : 0.9;

    // Get recent variance if available
    final variance = learningMetrics['pvVariance'] ?? 0.0;
    final variancePenalty = variance > 200 ? 1.3 : 1.0;

    return (baseThreshold * stabilityFactor * variancePenalty).clamp(70, 600);
  }

  /// Get adaptive reserve SOC
  double getAdaptiveReserveSoc() {
    if (_adaptiveReserveSoc != null) return _adaptiveReserveSoc!;
    return batteryHealth.getAdaptiveReserveSoc();
  }

  /// Get adaptive mode hold duration
  Duration getAdaptiveModeHold() {
    if (_adaptiveModeHold != null) {
      return Duration(minutes: _adaptiveModeHold!.toInt());
    }

    // If high PV variance (cloudy), shorten dwell; clear sky, lengthen it
    final variance = learningMetrics['pvVariance'] ?? 0.0;
    if (variance > 300) return const Duration(minutes: 8); // cloudy
    if (variance < 50) return const Duration(minutes: 25); // clear
    return const Duration(minutes: 15); // normal
  }

  /// Mark profile as "just auto-tuned"
  void markAutotuned() {
    lastAutotuneAt = DateTime.now();
  }
}

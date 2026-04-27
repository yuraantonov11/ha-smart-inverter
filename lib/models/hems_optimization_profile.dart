/// HEMS Optimization Profile — unified model for all tuning parameters
/// Consolidates: base tunables + auto-tuning + learned drift
library;

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
  DateTime dayStart;
  DateTime dayEnd;
  DateTime eveningPeakStart;
  DateTime nightStart;

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

  factory DailyTimeWindows.defaultTemperate() {
    final now = DateTime.now();
    return DailyTimeWindows(
      dayStart: now.copyWith(hour: 6, minute: 0, second: 0, millisecond: 0),
      dayEnd: now.copyWith(hour: 20, minute: 0, second: 0, millisecond: 0),
      eveningPeakStart:
          now.copyWith(hour: 17, minute: 0, second: 0, millisecond: 0),
      nightStart: now.copyWith(hour: 23, minute: 0, second: 0, millisecond: 0),
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
  int? cycleCountEstimated;
  double? healthPercentage; // 100% = new
  DateTime lastHealthCheckAt;

  BatteryHealthModel({
    required this.installationDate,
    this.cycleCountEstimated,
    this.healthPercentage = 100.0,
    DateTime? lastHealthCheckAt,
  }) : lastHealthCheckAt = lastHealthCheckAt ?? DateTime.now();

  double getAdaptiveReserveSoc({
    double baseReserveSoc = 20.0,
    double maxReserveSoc = 35.0,
  }) {
    final ageYears = DateTime.now().difference(installationDate).inDays / 365.0;
    final healthFactor = (healthPercentage ?? 100.0) / 100.0;

    double agePenalty;
    if (ageYears < 2) {
      agePenalty = -2; // aggressive (18%)
    } else if (ageYears < 5) {
      agePenalty = 0; // normal (20%)
    } else if (ageYears < 8) {
      agePenalty = 3; // cautious (23%)
    } else {
      agePenalty = 8; // very old (28%)
    }

    final healthPenalty = (1.0 - healthFactor) * 10.0;
    final adaptive = baseReserveSoc + agePenalty + healthPenalty;
    // Allow aggressive (lower) reserve for young batteries; clamp at 15% hard floor.
    return adaptive.clamp(15.0, maxReserveSoc);
  }
}

/// Thermal load model (for boiler / heat pump coordination)
class ThermalLoadModel {
  double targetTemperatureC;
  double currentTemperatureC;
  double boilerCapacityKwh;
  double heatingEfficiency;
  DateTime lastHeatingAt;

  ThermalLoadModel({
    required this.targetTemperatureC,
    required this.currentTemperatureC,
    required this.boilerCapacityKwh,
    this.heatingEfficiency = 0.85,
    DateTime? lastHeatingAt,
  }) : lastHeatingAt = lastHeatingAt ?? DateTime.now();

  double getHeatingDeficitWh() {
    if (currentTemperatureC >= targetTemperatureC) return 0.0;
    final tempDelta = targetTemperatureC - currentTemperatureC;
    final theoreticalWh =
        (boilerCapacityKwh * 1e6 / 3600.0) * tempDelta / 100.0;
    return theoreticalWh / heatingEfficiency;
  }

  bool isHeatingNeeded() => currentTemperatureC < (targetTemperatureC - 2.0);
}

/// Tariff forecast (prices for grid energy, day-ahead or TOU)
class TariffForecastData {
  final Map<DateTime, double> pricePerKwh;
  final String externalSource;
  final DateTime fetchedAt;

  TariffForecastData({
    required this.pricePerKwh,
    this.externalSource = 'manual',
    DateTime? fetchedAt,
  }) : fetchedAt = fetchedAt ?? DateTime.now();

  double get _averagePrice {
    if (pricePerKwh.isEmpty) return 0.0;
    return pricePerKwh.values.reduce((a, b) => a + b) / pricePerKwh.length;
  }

  DateTime? getNextCheapWindow(Duration minDuration, double priceMargin) {
    if (pricePerKwh.isEmpty) return null;
    final prices = pricePerKwh.values.toList()..sort();
    final median = prices[prices.length ~/ 2];
    final maxPrice = median * priceMargin;

    for (final entry in (pricePerKwh.entries.toList()
      ..sort((a, b) => a.key.compareTo(b.key)))) {
      if (entry.value <= maxPrice) {
        var cheapDuration = Duration.zero;
        for (final futureEntry in pricePerKwh.entries) {
          if (futureEntry.key.isAfter(entry.key) &&
              futureEntry.value <= maxPrice) {
            cheapDuration = futureEntry.key.difference(entry.key);
          }
        }
        if (cheapDuration >= minDuration) return entry.key;
      }
    }
    return null;
  }

  double estimateCost(DateTime start, DateTime end, double powerKw) {
    var totalCost = 0.0;
    var current = start;
    while (current.isBefore(end)) {
      final price = pricePerKwh[current] ?? _averagePrice;
      totalCost += price * powerKw * (1.0 / 3600.0);
      current = current.add(const Duration(seconds: 1));
    }
    return totalCost;
  }
}

/// Demand forecast (predicted load for future hours/days)
class DemandForecastData {
  final Map<int, DemandMetrics> hourlyMetrics;
  final String season;
  final DateTime learnedAt;

  DemandForecastData({
    required this.hourlyMetrics,
    this.season = 'spring',
    DateTime? learnedAt,
  }) : learnedAt = learnedAt ?? DateTime.now();

  double predictLoad(int hour,
      {bool isWeekend = false, int? heatingDemandAheadDays}) {
    final metrics = hourlyMetrics[hour];
    if (metrics == null) return 500.0;
    var predicted = metrics.p50;
    if (season == 'winter' && (heatingDemandAheadDays ?? 0) > 0) {
      predicted *= 1.3;
    }
    if (isWeekend) predicted *= 0.85;
    return predicted;
  }
}

/// Metrics for a given hour (learned from history)
class DemandMetrics {
  double p25;
  double p50;
  double p75;
  double p90;

  DemandMetrics({
    required this.p25,
    required this.p50,
    required this.p75,
    required this.p90,
  });

  static DemandMetrics fromHistory(List<double> samples) {
    final sorted = List<double>.from(samples)..sort();
    final len = sorted.length;
    if (len == 0) {
      return DemandMetrics(p25: 0, p50: 0, p75: 0, p90: 0);
    }
    return DemandMetrics(
      p25: sorted[(len * 0.25).toInt().clamp(0, len - 1)],
      p50: sorted[(len * 0.50).toInt().clamp(0, len - 1)],
      p75: sorted[(len * 0.75).toInt().clamp(0, len - 1)],
      p90: sorted[(len * 0.90).toInt().clamp(0, len - 1)],
    );
  }
}

/// Grid reliability forecast
class GridReliabilityForecast {
  final List<GridOutageEvent> plannedOutages;
  final List<GridInstabilityEvent> instabilityZones;
  final DateTime forecastedAt;

  GridReliabilityForecast({
    required this.plannedOutages,
    required this.instabilityZones,
    DateTime? forecastedAt,
  }) : forecastedAt = forecastedAt ?? DateTime.now();

  GridOutageEvent? getNextOutage(Duration within) {
    final soon = DateTime.now().add(within);
    for (final outage in plannedOutages) {
      if (outage.startTime.isBefore(soon)) return outage;
    }
    return null;
  }

  bool shouldPrechargeForStability() {
    final nextOutage = getNextOutage(const Duration(hours: 6));
    final instability = instabilityZones.any((e) => e.isActiveNow());
    return nextOutage != null || instability;
  }
}

class GridOutageEvent {
  DateTime startTime;
  DateTime endTime;
  String reason;

  GridOutageEvent({
    required this.startTime,
    required this.endTime,
    this.reason = 'unknown',
  });
}

class GridInstabilityEvent {
  DateTime startTime;
  DateTime endTime;
  double volatilityScore;

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
  final String systemId;
  final double pvPeakW;
  final double batteryCapacityAh;
  double get batteryCapacityKwh => batteryCapacityAh * 51.2 / 1000.0;

  HemsOptimizationStrategy optimizationStrategy;
  DailyTimeWindows timeWindows;
  BatteryHealthModel batteryHealth;
  ThermalLoadModel? thermalLoad;
  TariffForecastData? tariffForecast;
  DemandForecastData? demandForecast;
  GridReliabilityForecast? gridForecast;

  double? _adaptivePvSurplusEnterW;
  double? _adaptiveReserveSoc;
  double? _adaptiveModeHold;

  DateTime lastAutotuneAt;
  Map<String, double> learningMetrics;

  HemsOptimizationProfile({
    required this.systemId,
    required this.pvPeakW,
    required this.batteryCapacityAh,
    this.optimizationStrategy = HemsOptimizationStrategy.hybrid,
    DailyTimeWindows? timeWindows,
    BatteryHealthModel? batteryHealth,
    this.thermalLoad,
    this.tariffForecast,
    this.demandForecast,
    this.gridForecast,
    DateTime? lastAutotuneAt,
    Map<String, double>? learningMetrics,
  })  : timeWindows = timeWindows ?? DailyTimeWindows.defaultTemperate(),
        batteryHealth = batteryHealth ??
            BatteryHealthModel(installationDate: DateTime.now()),
        lastAutotuneAt = lastAutotuneAt ?? DateTime.now(),
        learningMetrics = learningMetrics ?? {};

  double getAdaptivePvSurplusEnter() {
    if (_adaptivePvSurplusEnterW != null) return _adaptivePvSurplusEnterW!;

    final hour = DateTime.now().hour;
    final baseThreshold = 0.10 * pvPeakW;
    final stabilityFactor = (hour < 10 || hour > 16) ? 1.5 : 0.9;
    final variance = learningMetrics['pvVariance'] ?? 0.0;
    final variancePenalty = variance > 200 ? 1.3 : 1.0;

    return (baseThreshold * stabilityFactor * variancePenalty).clamp(70, 600);
  }

  double getAdaptiveReserveSoc() {
    if (_adaptiveReserveSoc != null) return _adaptiveReserveSoc!;
    return batteryHealth.getAdaptiveReserveSoc();
  }

  Duration getAdaptiveModeHold() {
    if (_adaptiveModeHold != null) {
      return Duration(minutes: _adaptiveModeHold!.toInt());
    }
    final variance = learningMetrics['pvVariance'] ?? 0.0;
    if (variance > 300) return const Duration(minutes: 8);
    if (variance < 50) return const Duration(minutes: 25);
    return const Duration(minutes: 15);
  }

  void markAutotuned() {
    lastAutotuneAt = DateTime.now();
  }
}

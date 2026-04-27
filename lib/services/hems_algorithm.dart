import 'package:intl/intl.dart';
import 'dart:math';
import '../models/inverter_data.dart';
import '../providers/app_provider.dart';
import 'log_service.dart';

class DailyTimeWindows {
  DailyTimeWindows();

  factory DailyTimeWindows.defaultTemperate() => DailyTimeWindows();
}

/// Output priorities used by the inverter API.
class _Out {
  static const usb = '0'; // grid first
  static const sbu = '2'; // solar/battery first
}

/// Charger priorities.
class _Chg {
  static const snu = '1'; // solar + utility
  static const oso = '2'; // solar only
}

/// Tunable HEMS constants. Centralised to allow future feature flags / UI tuning.
class HemsTunables {
  // SOC thresholds
  final double reserveSoc; // hard floor
  final double minOperatingSoc; // below: prefer USB even with sun
  final double midSoc; // moderate band

  // PV surplus hysteresis (W)
  final double pvSurplusEnterW; // surplus to enter SBU
  final double pvSurplusExitW; // surplus to leave SBU

  // Anti-flapping
  final Duration minModeHold; // min time between output switches
  final Duration manualOverrideHold; // respect user manual switch
  final Duration commandDedupWindow; // suppress duplicate commands

  const HemsTunables({
    this.reserveSoc = 20.0,
    this.minOperatingSoc = 30.0,
    this.midSoc = 50.0,
    this.pvSurplusEnterW = 250.0,
    this.pvSurplusExitW = 50.0,
    this.minModeHold = const Duration(minutes: 20),
    this.manualOverrideHold = const Duration(minutes: 30),
    this.commandDedupWindow = const Duration(seconds: 30),
  });
}

class HemsAlgorithmService {
  final AppStateProvider provider;
  final HemsTunables tun;
  final dynamic optimizationProfile;
  final dynamic tuningService;

  // --- Battery Keepalive ---
  DateTime? _lastBatteryActivityAt;
  bool _keepaliveInProgress = false;
  static const _keepaliveInterval = Duration(hours: 2);
  static const _keepaliveDuration = Duration(seconds: 90);
  static const _keepaliveMinSoc = 22.0;

  // --- Acoustic comfort ---
  String? _lastAppliedBuzzer;

  // --- Anti-flapping / override state ---
  String? _lastCmdOutput;
  String? _lastCmdCharger;
  DateTime? _lastCmdOutputAt;
  DateTime? _lastCmdChargerAt;
  DateTime? _lastOutputSwitchAt;
  DateTime? _manualOverrideUntil;

  final List<double> _recentSurplusHistory = <double>[];
  static const int _surplusHistorySize = 30;

  HemsAlgorithmService(
    this.provider, {
    this.tun = const HemsTunables(),
    this.optimizationProfile,
    this.tuningService,
  });

  // ------------------------------------------------------------------
  // Helpers: read current inverter state
  // ------------------------------------------------------------------
  String? _currentOutput(InverterData d) =>
      d.rawFields['outputSourcePriority']?['value']?.toString();
  String? _currentCharger(InverterData d) =>
      d.rawFields['chargerSourcePriority']?['value']?.toString();

  /// Detects whether the user changed mode externally (UI/web/etc.) and
  /// arms a manual-override hold so the algorithm doesn't fight the user.
  void _detectManualOverride(InverterData data) {
    final cur = _currentOutput(data);
    if (cur == null) return;
    if (_lastCmdOutput == null) return; // never commanded yet
    // If reading stabilised on a value that differs from our last command
    // for longer than the dedup window, assume a human/external change.
    final since = _lastCmdOutputAt == null
        ? Duration.zero
        : DateTime.now().difference(_lastCmdOutputAt!);
    if (cur != _lastCmdOutput && since > tun.commandDedupWindow) {
      _manualOverrideUntil = DateTime.now().add(tun.manualOverrideHold);
      LogService.log(
          '✋ HEMS: detected manual override (mode=$cur, was cmd=$_lastCmdOutput). Holding ${tun.manualOverrideHold.inMinutes}m.');
      // Treat the user value as the new baseline so we don't loop.
      _lastCmdOutput = cur;
      _lastCmdOutputAt = DateTime.now();
    }
  }

  bool get _isManualHoldActive =>
      _manualOverrideUntil != null &&
      DateTime.now().isBefore(_manualOverrideUntil!);

  // ------------------------------------------------------------------
  // Helpers: command application with dedup + dwell
  // ------------------------------------------------------------------
  Future<bool> _applyOutput(String desired, String reason,
      {bool force = false}) async {
    final now = DateTime.now();
    // Dedup identical command in short window
    if (_lastCmdOutput == desired &&
        _lastCmdOutputAt != null &&
        now.difference(_lastCmdOutputAt!) < tun.commandDedupWindow) {
      return false;
    }
    // Dwell: avoid flapping output mode — Phase 2b: use adaptive dwell time
    final dwell = _getAdaptiveDwellTime();
    if (!force &&
        _lastOutputSwitchAt != null &&
        _lastCmdOutput != null &&
        _lastCmdOutput != desired &&
        now.difference(_lastOutputSwitchAt!) < dwell) {
      LogService.log(
          '⏳ HEMS: skip switch to ${_modeName(desired)} (reason=$reason) — dwell ${dwell.inMinutes}m active');
      return false;
    }

    final modeInt = desired == _Out.sbu ? 2 : 0;
    await provider.setMode(modeInt);
    if (_lastCmdOutput != desired) {
      _lastOutputSwitchAt = now;
    }
    _lastCmdOutput = desired;
    _lastCmdOutputAt = now;
    LogService.log('🔀 HEMS: output → ${_modeName(desired)} (reason=$reason)');
    return true;
  }

  Future<bool> _applyCharger(String desired, String reason) async {
    final now = DateTime.now();
    if (_lastCmdCharger == desired &&
        _lastCmdChargerAt != null &&
        now.difference(_lastCmdChargerAt!) < tun.commandDedupWindow) {
      return false;
    }
    await provider.changeSetting('chargerSourcePrioritySetting', desired);
    _lastCmdCharger = desired;
    _lastCmdChargerAt = now;
    LogService.log('🔌 HEMS: charger → ${_chgName(desired)} (reason=$reason)');
    return true;
  }

  String _modeName(String v) => v == _Out.sbu ? 'SBU' : 'USB';
  String _chgName(String v) => v == _Chg.snu ? 'SNU' : 'OSO';

  // ------------------------------------------------------------------
  // Battery activity + keepalive
  // ------------------------------------------------------------------
  void _trackBatteryActivity(InverterData data) {
    if (data.batteryPower.abs() > 50) {
      _lastBatteryActivityAt = DateTime.now();
    }
  }

  Future<bool> _batteryKeepalive(InverterData data) async {
    if (_keepaliveInProgress) return true;
    _trackBatteryActivity(data);
    if (data.batterySoc <= _keepaliveMinSoc) return false;

    final lastActivity = _lastBatteryActivityAt;
    if (lastActivity == null) {
      _lastBatteryActivityAt = DateTime.now();
      return false;
    }
    final inactiveDuration = DateTime.now().difference(lastActivity);
    if (inactiveDuration < _keepaliveInterval) return false;

    final currentOutput = _currentOutput(data);
    if (currentOutput == _Out.sbu) return false;

    _keepaliveInProgress = true;
    LogService.log(
        '🔋 Keepalive: battery idle ${inactiveDuration.inMinutes}m. Briefly switching to SBU.');
    await _applyOutput(_Out.sbu, 'keepalive', force: true);

    Future.delayed(_keepaliveDuration, () async {
      await _applyOutput(_Out.usb, 'keepalive_end', force: true);
      _lastBatteryActivityAt = DateTime.now();
      _keepaliveInProgress = false;
      LogService.log('🔋 Keepalive done. Returned to USB.');
    });
    return true;
  }

  // ------------------------------------------------------------------
  // 1. Acoustic comfort
  // ------------------------------------------------------------------
  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    final buzzerConfig = data.rawFields['fullConfigs']?['buzzerAlarmSetting'];
    final currentBuzzer = buzzerConfig?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1';

    if (currentBuzzer == null) return;
    if (currentBuzzer == desiredBuzzer || _lastAppliedBuzzer == desiredBuzzer) {
      return;
    }
    await provider.changeSetting('buzzerAlarmSetting', desiredBuzzer);
    final fullConfigs = data.rawFields['fullConfigs'];
    if (fullConfigs is Map<String, dynamic> &&
        fullConfigs['buzzerAlarmSetting'] is Map<String, dynamic>) {
      (fullConfigs['buzzerAlarmSetting'] as Map<String, dynamic>)['value'] =
          num.tryParse(desiredBuzzer) ?? desiredBuzzer;
    }
    _lastAppliedBuzzer = desiredBuzzer;
    LogService.log(isNight
        ? '🤫 Buzzer: night silence on'
        : '🔊 Buzzer: daytime sound on');
  }

  // ------------------------------------------------------------------
  // --- Phase 2b helpers: adaptive thresholds ---

  /// Adaptive dwell time between output mode switches (variance-aware)
  Duration _getAdaptiveDwellTime() {
    if (tuningService != null && _recentSurplusHistory.isNotEmpty) {
      return tuningService!.computeAdaptiveDwell(_recentSurplusHistory);
    }
    if (optimizationProfile != null) {
      return optimizationProfile!.getAdaptiveModeHold();
    }
    return tun.minModeHold;
  }

  /// Adaptive PV surplus threshold before entering SBU (variance-aware)
  double _getAdaptivePvSurplusEnter() {
    if (tuningService != null && _recentSurplusHistory.isNotEmpty) {
      return tuningService!.computeAdaptivePvSurplus(_recentSurplusHistory);
    }
    if (optimizationProfile != null) {
      return optimizationProfile!.getAdaptivePvSurplusEnter();
    }
    return tun.pvSurplusEnterW;
  }

  /// Get adaptive reserve SOC
  double _getAdaptiveReserveSoc() {
    if (optimizationProfile != null) {
      return tuningService?.computeAdaptiveReserveSoc(
            baseReserveSoc: tun.reserveSoc,
            isTimeOfUseTariff: optimizationProfile?.tariffForecast != null,
          ) ??
          optimizationProfile!.getAdaptiveReserveSoc();
    }
    return tun.reserveSoc;
  }

  /// Track rolling surplus for learning
  void _trackSurplus(double surplus) {
    _recentSurplusHistory.add(surplus);
    if (_recentSurplusHistory.length > _surplusHistorySize) {
      _recentSurplusHistory.removeAt(0);
    }
  }

  // --- 2. Adaptive mode (realtime + forecast hybrid)
  // ------------------------------------------------------------------
  Future<void> executeAdaptiveMode({
    required InverterData data,
    required double batteryCapacityAh,
    required Map<String, double> hourlyForecast,
    required Map<int, double> avgHourlyConsumptionStats,
    required double productionCoefficient,
    DateTime? nowOverride,
    bool useAstronomicalWindows = false,
    double latitude = 49.0,
    double longitude = 31.0,
    int manualDayStartHour = 7,
    int manualEveningStartHour = 17,
    int manualNightStartHour = 23,
  }) async {
    // Detect external/manual mode change before any decisions.
    _detectManualOverride(data);

    if (await _batteryKeepalive(data)) return;

    final now = nowOverride ?? DateTime.now();
    final currentHour = now.hour;
    final windows = _resolveWindows(
      now: now,
      useAstronomicalWindows: useAstronomicalWindows,
      latitude: latitude,
      longitude: longitude,
      manualDayStartHour: manualDayStartHour,
      manualEveningStartHour: manualEveningStartHour,
      manualNightStartHour: manualNightStartHour,
    );
    final currentOutput = _currentOutput(data);
    final currentCharger = _currentCharger(data);

    // Physical battery model
    const systemVoltage = 51.2;
    final maxBatteryCapacityWh = batteryCapacityAh * systemVoltage;
    // Phase 2c: use adaptive reserve SOC (battery age/health/strategy aware)
    final reserveSoc = _getAdaptiveReserveSoc();
    final reserveEnergyWh = maxBatteryCapacityWh * (reserveSoc / 100.0);
    final currentEnergyWh = maxBatteryCapacityWh * (data.batterySoc / 100.0);

    // Realtime signals
    final pv = data.pvPower; // W
    final load = data.loadPower; // W
    final surplus = pv - load; // W

    // Phase 2b: track surplus history for adaptive threshold learning
    _trackSurplus(surplus);

    // Phase 2b: adaptive PV surplus threshold (variance-aware)
    final adaptivePvSurplusEnter = _getAdaptivePvSurplusEnter();

    // -------- 0. SAFETY: hard floor --------
    if (data.batterySoc <= reserveSoc + 2.0) {
      await _applyOutput(_Out.usb, 'safety_low_soc', force: true);
      await _applyCharger(_Chg.snu, 'safety_low_soc');
      return;
    }

    // -------- 1. Manual override hold --------
    if (_isManualHoldActive) {
      LogService.log(
          'ℹ️ HEMS: manual hold active until ${_manualOverrideUntil!.toIso8601String()} — skipping output decisions.');
      // Still allow charger management when safe.
      if (currentHour >= 7 && currentHour < 23 && currentCharger != _Chg.oso) {
        await _applyCharger(_Chg.oso, 'day_solar_only');
      }
      return;
    }

    // -------- 2. Night tariff window --------
    if (currentHour >= windows.nightStart || currentHour < windows.dayStart) {
      await _applyOutput(_Out.usb, 'night_tariff');
      // Tomorrow forecast deficit drives charger choice
      final tomorrow = now.hour >= windows.nightStart
          ? now.add(const Duration(days: 1))
          : now;
      final deficitIfNoCharge = _simulateEnergyDeficit(
        startHour: 7,
        endHour: 23,
        targetDate: tomorrow,
        startBatteryWh: currentEnergyWh,
        maxBatteryCapacityWh: maxBatteryCapacityWh,
        reserveEnergyWh: reserveEnergyWh,
        hourlyForecast: hourlyForecast,
        avgHourlyConsumptionStats: avgHourlyConsumptionStats,
        productionCoefficient: productionCoefficient,
        liveLoadW: load,
      );
      if (deficitIfNoCharge > 0) {
        await _applyCharger(
            _Chg.snu, 'night_charge_deficit_${deficitIfNoCharge.toInt()}Wh');
      } else {
        await _applyCharger(_Chg.oso, 'night_no_grid_charge_needed');
      }
      return;
    }

    // -------- 3. Daytime / evening: realtime first --------
    // Always prefer solar-only charger when sun is up.
    if (currentCharger != _Chg.oso) {
      await _applyCharger(_Chg.oso, 'daytime_solar_only');
    }

    final socOk = data.batterySoc >= tun.minOperatingSoc;
    final pvActive = pv > 80; // panels actually producing

    // (a) Realtime SURPLUS — strong reason to use SBU now.
    // Phase 2b: use adaptive threshold instead of fixed tun.pvSurplusEnterW
    if (pvActive && socOk && surplus >= adaptivePvSurplusEnter) {
      await _applyOutput(_Out.sbu,
          'pv_surplus_${surplus.toInt()}W_soc_${data.batterySoc.toInt()}_thr_${adaptivePvSurplusEnter.toInt()}W');
      return;
    }

    // (b) Forecast-based fallback only when realtime is ambiguous.
    final deficitTillNight = _simulateEnergyDeficit(
      startHour: currentHour,
      endHour: 23,
      targetDate: now,
      startBatteryWh: currentEnergyWh,
      maxBatteryCapacityWh: maxBatteryCapacityWh,
      reserveEnergyWh: reserveEnergyWh,
      hourlyForecast: hourlyForecast,
      avgHourlyConsumptionStats: avgHourlyConsumptionStats,
      productionCoefficient: productionCoefficient,
      liveLoadW: load,
    );

    final isEvening =
        currentHour >= windows.eveningStart && currentHour < windows.nightStart;

    if (isEvening) {
      // Evening: protect reserve more aggressively
      final eveningSafetyWh = maxBatteryCapacityWh * 0.01;
      final availableEnergyWh = max(0.0, currentEnergyWh - reserveEnergyWh);
      final reserveProtectionActive = data.batterySoc <= (reserveSoc + 2.0) ||
          availableEnergyWh <= eveningSafetyWh ||
          deficitTillNight > 0;
      final batteryCanBeUsed = data.batterySoc >= (reserveSoc + 5.0) &&
          availableEnergyWh > eveningSafetyWh &&
          deficitTillNight == 0;

      if (reserveProtectionActive) {
        await _applyOutput(_Out.usb,
            'evening_reserve_def_${deficitTillNight.toInt()}Wh_soc_${data.batterySoc.toInt()}');
      } else if (batteryCanBeUsed) {
        await _applyOutput(
            _Out.sbu, 'evening_battery_avail_${availableEnergyWh.toInt()}Wh');
      }
      return;
    }

    // Daytime, ambiguous realtime
    if (deficitTillNight == 0) {
      await _applyOutput(_Out.sbu, 'day_forecast_ok');
    } else if (surplus <= tun.pvSurplusExitW && data.batterySoc < tun.midSoc) {
      // Real PV deficit + low-mid SOC: use grid, save sun for battery.
      await _applyOutput(_Out.usb,
          'day_forecast_deficit_${deficitTillNight.toInt()}Wh_low_soc');
    } else {
      // Otherwise: keep current state. Don't fight realtime small-surplus.
      LogService.log('ℹ️ HEMS: hold ${_modeName(currentOutput ?? _Out.usb)} '
          '(pv=${pv.toInt()}W load=${load.toInt()}W surplus=${surplus.toInt()}W '
          'soc=${data.batterySoc.toStringAsFixed(0)}% '
          'def=${deficitTillNight.toInt()}Wh '
          'thr=${adaptivePvSurplusEnter.toInt()}W)');
    }
  }

  // ------------------------------------------------------------------
  // Simulation helper (extracted, with live-load bias)
  // ------------------------------------------------------------------
  double _simulateEnergyDeficit({
    required int startHour,
    required int endHour,
    required DateTime targetDate,
    required double startBatteryWh,
    required double maxBatteryCapacityWh,
    required double reserveEnergyWh,
    required Map<String, double> hourlyForecast,
    required Map<int, double> avgHourlyConsumptionStats,
    required double productionCoefficient,
    required double liveLoadW,
  }) {
    final formatter = DateFormat('yyyy-MM-dd HH:mm');
    var simulatedBatteryWh = startBatteryWh;
    var totalDeficitWh = 0.0;

    for (var h = startHour; h < endHour; h++) {
      // Smarter fallback: blend stat with live load to avoid 500W flat bias.
      final statLoad = avgHourlyConsumptionStats[h];
      final loadWh =
          statLoad ?? (liveLoadW > 0 ? liveLoadW.clamp(200.0, 3000.0) : 500.0);

      final timeKey = formatter.format(
          DateTime(targetDate.year, targetDate.month, targetDate.day, h, 0));
      final rawSolarWh = hourlyForecast[timeKey] ??
          _fuzzyForecastLookup(hourlyForecast, targetDate, h);
      final realSolarWh = rawSolarWh * productionCoefficient;

      simulatedBatteryWh += realSolarWh - loadWh;
      if (simulatedBatteryWh > maxBatteryCapacityWh) {
        simulatedBatteryWh = maxBatteryCapacityWh;
      }
      if (simulatedBatteryWh < reserveEnergyWh) {
        totalDeficitWh += (reserveEnergyWh - simulatedBatteryWh);
        simulatedBatteryWh = reserveEnergyWh;
      }
    }
    return totalDeficitWh;
  }

  /// Try alternative key formats (timezone/minute drift) before giving up.
  double _fuzzyForecastLookup(
      Map<String, double> forecast, DateTime date, int hour) {
    final candidates = <String>[
      DateFormat('yyyy-MM-ddTHH:mm')
          .format(DateTime(date.year, date.month, date.day, hour, 0)),
      DateFormat('yyyy-MM-dd HH:00')
          .format(DateTime(date.year, date.month, date.day, hour, 0)),
      DateFormat('yyyy-MM-dd HH')
          .format(DateTime(date.year, date.month, date.day, hour, 0)),
    ];
    for (final k in candidates) {
      final v = forecast[k];
      if (v != null) return v;
    }
    return 0.0;
  }

  // ------------------------------------------------------------------
  // 3. Night arbitrage
  // ------------------------------------------------------------------
  Future<void> executeNightArbitrage(InverterData data) async {
    _detectManualOverride(data);
    if (_isManualHoldActive) {
      LogService.log('ℹ️ NightArb: manual hold active — skipping.');
      return;
    }
    if (await _batteryKeepalive(data)) return;

    final hour = DateTime.now().hour;
    if (hour >= 23 || hour < 7) {
      await _applyOutput(_Out.usb, 'night_tariff');
      await _applyCharger(_Chg.snu, 'night_charge');
    } else {
      // Daytime: keep realtime-aware behaviour to avoid flapping.
      final surplus = data.pvPower - data.loadPower;
      if (data.pvPower > 80 &&
          data.batterySoc >= tun.minOperatingSoc &&
          surplus >= tun.pvSurplusEnterW) {
        await _applyOutput(_Out.sbu, 'day_pv_surplus');
      }
      await _applyCharger(_Chg.oso, 'day_solar_only');
    }
  }

  // ------------------------------------------------------------------
  // 4. Storm / reserve
  // ------------------------------------------------------------------
  Future<void> executeStormMode(InverterData data) async {
    _trackBatteryActivity(data);
    await _applyOutput(_Out.usb, 'storm_mode', force: true);
    await _applyCharger(_Chg.snu, 'storm_mode');
  }

  // ------------------------------------------------------------------
  // External: arm manual override (e.g. when user toggles from UI)
  // ------------------------------------------------------------------
  void armManualOverride([Duration? d]) {
    _manualOverrideUntil = DateTime.now().add(d ?? tun.manualOverrideHold);
    LogService.log(
        '✋ HEMS: manual override armed for ${(d ?? tun.manualOverrideHold).inMinutes}m.');
  }

  ({int dayStart, int eveningStart, int nightStart}) _resolveWindows({
    required DateTime now,
    required bool useAstronomicalWindows,
    required double latitude,
    required double longitude,
    required int manualDayStartHour,
    required int manualEveningStartHour,
    required int manualNightStartHour,
  }) {
    if (!useAstronomicalWindows) {
      return (
        dayStart: manualDayStartHour.clamp(0, 23),
        eveningStart: manualEveningStartHour.clamp(0, 23),
        nightStart: manualNightStartHour.clamp(0, 23),
      );
    }

    final dayOfYear = now.difference(DateTime(now.year, 1, 1)).inDays + 1;
    final latRad = latitude * pi / 180.0;
    final decl = 0.409 * sin((2 * pi / 365.0) * dayOfYear - 1.39);
    final cosH =
        ((sin(-0.01454) - sin(latRad) * sin(decl)) / (cos(latRad) * cos(decl)))
            .clamp(-1.0, 1.0);
    final h = acos(cosH);
    final daylightHours = (2 * h) * 24 / (2 * pi);
    final sunrise = 12.0 - daylightHours / 2.0 - (longitude / 15.0);
    final sunset = 12.0 + daylightHours / 2.0 - (longitude / 15.0);

    final sunriseHour = sunrise.floor().clamp(0, 23);
    final sunsetHour = sunset.floor().clamp(0, 23);

    return (
      dayStart: (sunriseHour - 1).clamp(4, 12),
      eveningStart: (sunsetHour - 1).clamp(14, 22),
      nightStart: (sunsetHour + 1).clamp(18, 23),
    );
  }
}

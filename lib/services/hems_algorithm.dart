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

/// Machine-searchable reason codes for control writes and skip paths.
class _Reason {
  static const manualOverrideDetected = 'manual_override_detected';
  static const manualOverrideHoldSkip = 'manual_override_hold_skip';
  static const dedupSkipOutput = 'dedup_skip_output';
  static const dedupSkipCharger = 'dedup_skip_charger';
  static const dwellLock = 'dwell_lock';
  static const reserveSocProtection = 'reserve_soc_protection';
  static const nightWindowUsb = 'night_window_usb';
  static const tariffExpensiveDefer = 'tariff_expensive_defer';
  static const nightChargeDeficitCheapNow = 'night_charge_deficit_cheap_now';
  static const nightChargeNoCheapWindow = 'night_charge_no_cheap_window';
  static const nightNoGridChargeNeeded = 'night_no_grid_charge_needed';
  static const chargerDaySolarOnly = 'charger_day_solar_only';
  static const surplusEnterSbu = 'surplus_enter_sbu';
  static const eveningReserveProtection = 'evening_reserve_protection';
  static const eveningBatteryUse = 'evening_battery_use';
  static const dayForecastOk = 'day_forecast_ok';
  static const dayForecastDeficitLowSoc = 'day_forecast_deficit_low_soc';
  static const holdCurrentState = 'hold_current_state';
  static const keepaliveStart = 'keepalive_start';
  static const keepaliveEnd = 'keepalive_end';
  static const gridOutagePrecharge = 'grid_outage_precharge';
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

/// Read-only diagnostics values for showing current HEMS adaptive decisions in UI.
class HemsDiagnosticsSnapshot {
  final DateTime capturedAt;
  final int dayStartHour;
  final int eveningStartHour;
  final int nightStartHour;
  final double adaptivePvSurplusEnterW;
  final Duration adaptiveDwell;
  final double adaptiveReserveSoc;
  final bool tariffForecastActive;
  final bool chargingCheapNow;
  final DateTime? nextCheapChargingWindow;

  const HemsDiagnosticsSnapshot({
    required this.capturedAt,
    required this.dayStartHour,
    required this.eveningStartHour,
    required this.nightStartHour,
    required this.adaptivePvSurplusEnterW,
    required this.adaptiveDwell,
    required this.adaptiveReserveSoc,
    required this.tariffForecastActive,
    required this.chargingCheapNow,
    required this.nextCheapChargingWindow,
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

  // Backoff circuit breaker for control writes when backend is timing out.
  int _consecutiveControlWriteFailures = 0;
  DateTime? _controlWriteBlockedUntil;
  DateTime? _lastControlWriteBlockedLogAt;

  final List<double> _recentSurplusHistory = <double>[];
  static const int _surplusHistorySize = 30;

  HemsAlgorithmService(
    this.provider, {
    this.tun = const HemsTunables(),
    this.optimizationProfile,
    this.tuningService,
  });

  /// Builds current adaptive/runtime values that can be displayed in settings.
  HemsDiagnosticsSnapshot buildDiagnosticsSnapshot({DateTime? now}) {
    final ts = now ?? DateTime.now();
    final windows = _resolveWindows(
      now: ts,
      useAstronomicalWindows: provider.useAstronomicalWindows,
      latitude: provider.siteLatitude,
      longitude: provider.siteLongitude,
      manualDayStartHour: provider.manualDayStartHour,
      manualEveningStartHour: provider.manualEveningStartHour,
      manualNightStartHour: provider.manualNightStartHour,
    );

    final tariffActive = optimizationProfile?.tariffForecast != null;
    return HemsDiagnosticsSnapshot(
      capturedAt: ts,
      dayStartHour: windows.dayStart,
      eveningStartHour: windows.eveningStart,
      nightStartHour: windows.nightStart,
      adaptivePvSurplusEnterW: _getAdaptivePvSurplusEnter(),
      adaptiveDwell: _getAdaptiveDwellTime(),
      adaptiveReserveSoc: _getAdaptiveReserveSoc(),
      tariffForecastActive: tariffActive,
      chargingCheapNow: tariffActive ? _isChargingCheapNow(ts) : true,
      nextCheapChargingWindow:
          tariffActive ? _getNextCheapChargingWindow(ts) : null,
    );
  }

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
          '✋ HEMS: detected manual override (reason=${_Reason.manualOverrideDetected}, mode=$cur, was cmd=$_lastCmdOutput). Holding ${tun.manualOverrideHold.inMinutes}m.');
      // Treat the user value as the new baseline so we don't loop.
      _lastCmdOutput = cur;
      _lastCmdOutputAt = DateTime.now();
    }
  }

  bool get _isManualHoldActive =>
      _manualOverrideUntil != null &&
      DateTime.now().isBefore(_manualOverrideUntil!);

  ({double? gridV, bool unavailable}) _gridDebugState() {
    final v = provider.data?.gridVoltage;
    final unavailable = v != null && v < 120.0;
    return (gridV: v, unavailable: unavailable);
  }

  bool _isControlWriteBlocked() {
    final until = _controlWriteBlockedUntil;
    if (until == null) return false;
    final now = DateTime.now();
    if (!now.isBefore(until)) {
      _controlWriteBlockedUntil = null;
      return false;
    }

    if (_lastControlWriteBlockedLogAt == null ||
        now.difference(_lastControlWriteBlockedLogAt!) >=
            const Duration(seconds: 12)) {
      _lastControlWriteBlockedLogAt = now;
      final remaining = until.difference(now).inSeconds.clamp(1, 999);
      LogService.log(
          '⏸️ HEMS: control writes paused for ${remaining}s after repeated API failures (reason=control_write_backoff)');
    }
    return true;
  }

  void _recordControlWriteResult({
    required bool success,
    required String target,
    required String reason,
  }) {
    if (success) {
      if (_consecutiveControlWriteFailures > 0) {
        LogService.log(
            '✅ HEMS: control write recovered (target=$target, failures=$_consecutiveControlWriteFailures)');
      }
      _consecutiveControlWriteFailures = 0;
      _controlWriteBlockedUntil = null;
      return;
    }

    _consecutiveControlWriteFailures++;
    final backoffSeconds = switch (_consecutiveControlWriteFailures) {
      <= 2 => 5,
      3 => 12,
      4 => 25,
      _ => 45,
    };
    _controlWriteBlockedUntil =
        DateTime.now().add(Duration(seconds: backoffSeconds));
    LogService.logCritical(
        'HEMS control write failed: target=$target reason=$reason '
        'failures=$_consecutiveControlWriteFailures backoff=${backoffSeconds}s',
        category: 'CONTROL_WRITE');
  }

  // ------------------------------------------------------------------
  // Helpers: command application with dedup + dwell
  // ------------------------------------------------------------------
  /// Apply output mode with intelligent state sync.
  /// After sending command, re-fetch inverter state to detect external changes
  /// (e.g., another app or user changing mode).
  Future<bool> _applyOutput(String desired, String reason,
      {bool force = false}) async {
    if (_isControlWriteBlocked()) {
      return false;
    }

    final now = DateTime.now();
    final gridState = _gridDebugState();
    final currentOutput =
        provider.data?.rawFields['outputSourcePriority']?['value']?.toString();

    if (desired == _Out.usb) {
      LogService.log(
          '🔎 HEMS output request: target=USB reason=$reason force=$force '
          'current=$currentOutput gridV=${gridState.gridV?.toStringAsFixed(1) ?? '-'}V '
          'gridUnavailable=${gridState.unavailable}');
      if (gridState.unavailable) {
        LogService.logCritical(
            'DEBUG USB REQUEST WITH GRID OUTAGE: reason=$reason '
            'gridV=${gridState.gridV?.toStringAsFixed(1) ?? '-'}V, current=$currentOutput. '
            'USB priority can be written, but real import from grid is impossible until mains returns.',
            category: 'GRID_OUTAGE');
      }
    }

    // Dedup identical command in short window
    if (_lastCmdOutput == desired &&
        _lastCmdOutputAt != null &&
        now.difference(_lastCmdOutputAt!) < tun.commandDedupWindow) {
      LogService.log(
          '⏭️ HEMS: skip output write (reason=${_Reason.dedupSkipOutput}, target=${_modeName(desired)}, '
          'requested=$reason, gridV=${gridState.gridV?.toStringAsFixed(1) ?? '-'}V, '
          'gridUnavailable=${gridState.unavailable})');
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
          '⏳ HEMS: skip switch to ${_modeName(desired)} (reason=${_Reason.dwellLock}, requested=$reason, '
          'dwell=${dwell.inMinutes}m, gridV=${gridState.gridV?.toStringAsFixed(1) ?? '-'}V, '
          'gridUnavailable=${gridState.unavailable})');
      return false;
    }

    final modeInt = desired == _Out.sbu ? 2 : 0;
    final applied = await provider.setMode(modeInt);
    if (!applied) {
      _recordControlWriteResult(
          success: false,
          target: 'output:${_modeName(desired)}',
          reason: reason);
      LogService.logCritical(
          '❌ HEMS: output write failed (target=${_modeName(desired)}, reason=$reason, '
          'gridV=${gridState.gridV?.toStringAsFixed(1) ?? '-'}V, gridUnavailable=${gridState.unavailable})',
          category: 'CONTROL_WRITE');
      return false;
    }
    _recordControlWriteResult(
        success: true, target: 'output:${_modeName(desired)}', reason: reason);
    if (_lastCmdOutput != desired) {
      _lastOutputSwitchAt = now;
    }
    _lastCmdOutput = desired;
    _lastCmdOutputAt = now;
    LogService.log('🔀 HEMS: output → ${_modeName(desired)} (reason=$reason)');

    // *** Multi-app sync: After sending command, fetch fresh state to detect
    // external changes (another app, web UI, physical button, etc.)
    Future.delayed(const Duration(milliseconds: 500), () async {
      try {
        final fresh = await provider.service.getRealTimeData();
        if (fresh != null) {
          final actualOutput =
              fresh.rawFields['outputSourcePriority']?['value']?.toString();
          if (actualOutput != null && actualOutput != _lastCmdOutput) {
            LogService.log('🔄 HEMS: detected external mode change! '
                'sent=$desired, but device shows=$actualOutput → syncing local state');
            LogService.logCritical(
                'EXTERNAL MODE CHANGE: sent=${_modeName(desired)}, '
                'device shows=${_modeName(actualOutput)} → sync conflict detected!',
                category: 'MODE_CONFLICT');
            _lastCmdOutput = actualOutput;
            _lastCmdOutputAt = DateTime.now();
            _detectManualOverride(fresh);
          }
        }
      } catch (e) {
        // Non-critical — just log
        LogService.log(
            'ℹ️ HEMS: post-command sync failed (likely API throttle): $e');
      }
    });

    return true;
  }

  Future<bool> _applyCharger(String desired, String reason) async {
    if (_isControlWriteBlocked()) {
      return false;
    }

    final now = DateTime.now();
    if (_lastCmdCharger == desired &&
        _lastCmdChargerAt != null &&
        now.difference(_lastCmdChargerAt!) < tun.commandDedupWindow) {
      LogService.log(
          '⏭️ HEMS: skip charger write (reason=${_Reason.dedupSkipCharger}, target=${_chgName(desired)})');
      return false;
    }
    final applied =
        await provider.changeSetting('chargerSourcePrioritySetting', desired);
    if (!applied) {
      _recordControlWriteResult(
          success: false,
          target: 'charger:${_chgName(desired)}',
          reason: reason);
      LogService.logCritical(
          '❌ HEMS: charger write failed (target=${_chgName(desired)}, reason=$reason)',
          category: 'CONTROL_WRITE');
      return false;
    }
    _recordControlWriteResult(
        success: true, target: 'charger:${_chgName(desired)}', reason: reason);
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
    await _applyOutput(_Out.sbu, _Reason.keepaliveStart, force: true);

    Future.delayed(_keepaliveDuration, () async {
      await _applyOutput(_Out.usb, _Reason.keepaliveEnd, force: true);
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
    final applied =
        await provider.changeSetting('buzzerAlarmSetting', desiredBuzzer);
    if (!applied) {
      LogService.log(
          'ℹ️ Buzzer write skipped: backend unavailable (desired=$desiredBuzzer)');
      return;
    }
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

  // ------------------------------------------------------------------
  // --- Phase 3a helpers: tariff-aware charging ---

  /// Returns true if the current hour is cheap enough for grid charging.
  /// Falls back to true (allow charging) when no tariff data is available.
  bool _isChargingCheapNow(DateTime now) {
    try {
      final tf = optimizationProfile?.tariffForecast;
      if (tf == null) return true;
      final priceMap = tf.pricePerKwh as Map<DateTime, double>;
      if (priceMap.isEmpty) return true;
      final currentKey = DateTime(now.year, now.month, now.day, now.hour);
      final currentPrice = priceMap[currentKey];
      if (currentPrice == null) return true;
      final avg = priceMap.values.reduce((a, b) => a + b) / priceMap.length;
      final isCheap = currentPrice <= avg * 1.05;
      if (!isCheap) {
        LogService.log(
            '💰 HEMS: tariff check — current ${currentPrice.toStringAsFixed(2)}'
            ' > avg ${avg.toStringAsFixed(2)} UAH/kWh → expensive hour (reason=tariff_expensive_hour)');
      }
      return isCheap;
    } catch (_) {
      return true; // fail-safe: allow charging
    }
  }

  /// Returns the next DateTime where a 2-hour cheap charging window starts,
  /// or null if no such window exists in the forecast.
  DateTime? _getNextCheapChargingWindow(DateTime now) {
    try {
      final tf = optimizationProfile?.tariffForecast;
      if (tf == null) return null;
      // priceMargin=1.0 → keep only hours at/below average forecast price.
      return tf.getNextCheapWindow(
        const Duration(hours: 2),
        1.0,
        from: now,
      ) as DateTime?;
    } catch (_) {
      return null;
    }
  }

  // ------------------------------------------------------------------
  // --- Phase 3b helper: demand-forecast-aware load estimate ---

  /// Returns forecasted load (Wh) for the given hour.
  /// Prefers DemandForecastData from the optimizationProfile when available;
  /// falls back to historical stats or live-load estimate.
  double _getLoadForecastWh(
    int hour,
    Map<int, double> stats,
    DateTime targetDate,
    double liveLoadW,
  ) {
    try {
      final df = optimizationProfile?.demandForecast;
      if (df != null) {
        final isWeekend = targetDate.weekday >= 6;
        final predicted =
            (df.predictLoad(hour, isWeekend: isWeekend) as num).toDouble();
        return predicted.clamp(100.0, 8000.0);
      }
    } catch (_) {}
    final statLoad = stats[hour];
    return statLoad ?? (liveLoadW > 0 ? liveLoadW.clamp(200.0, 3000.0) : 500.0);
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
      await _applyOutput(_Out.usb, _Reason.reserveSocProtection, force: true);
      await _applyCharger(_Chg.snu, _Reason.reserveSocProtection);
      LogService.logCritical(
          'HARD FLOOR: SOC=${data.batterySoc.toStringAsFixed(1)}% ≤ ${(reserveSoc + 2.0).toStringAsFixed(1)}% '
          '→ Force USB+SNU',
          category: 'BATTERY_SAFETY');
      return;
    }

    // -------- 1. Manual override hold --------
    if (_isManualHoldActive) {
      LogService.log(
          'ℹ️ HEMS: manual hold active until ${_manualOverrideUntil!.toIso8601String()} (reason=${_Reason.manualOverrideHoldSkip}) — skipping output decisions.');
      // Still allow charger management when safe.
      if (currentHour >= 7 && currentHour < 23 && currentCharger != _Chg.oso) {
        await _applyCharger(_Chg.oso, _Reason.chargerDaySolarOnly);
      }
      return;
    }

    // -------- 2. Night tariff window --------
    if (currentHour >= windows.nightStart || currentHour < windows.dayStart) {
      await _applyOutput(_Out.usb, _Reason.nightWindowUsb);
      // Tomorrow forecast deficit drives charger choice
      final tomorrow = now.hour >= windows.nightStart
          ? now.add(const Duration(days: 1))
          : now;
      final cheapNow = _isChargingCheapNow(now);
      final cheapAt = _getNextCheapChargingWindow(now);
      final hoursToCheap =
          cheapAt == null ? null : cheapAt.difference(now).inMinutes / 60.0;
      LogService.log(
          '🌙 HEMS night.ctx: now=${now.toIso8601String()} soc=${data.batterySoc.toStringAsFixed(1)}% '
          'energy=${currentEnergyWh.toStringAsFixed(0)}Wh reserve=${reserveEnergyWh.toStringAsFixed(0)}Wh '
          'cheapNow=$cheapNow cheapAt=${cheapAt?.toIso8601String() ?? '-'} '
          'hoursToCheap=${hoursToCheap?.toStringAsFixed(2) ?? '-'}');
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
      LogService.log(
          '🌙 HEMS night.deficit: value=${deficitIfNoCharge.toStringAsFixed(0)}Wh '
          'targetDate=${tomorrow.toIso8601String().substring(0, 10)} '
          'reason=${deficitIfNoCharge > 0 ? 'charge_needed' : 'no_grid_charge_needed'}');
      // Phase 3a: tariff-aware night charging decision
      if (deficitIfNoCharge > 0) {
        if (cheapNow) {
          LogService.log(
              '🌙 HEMS night.action: charger=SNU (reason=${_Reason.nightChargeDeficitCheapNow}) '
              'deficit=${deficitIfNoCharge.toStringAsFixed(0)}Wh');
          await _applyCharger(_Chg.snu, _Reason.nightChargeDeficitCheapNow);
        } else {
          if (cheapAt != null &&
              cheapAt.isAfter(now) &&
              cheapAt.difference(now) <= const Duration(hours: 4)) {
            // Upcoming cheap window is within 4 hours — defer grid charge
            await _applyCharger(_Chg.oso, _Reason.tariffExpensiveDefer);
            LogService.log(
                '💰 HEMS: tariff deferral (reason=${_Reason.tariffExpensiveDefer}) — deficit=${deficitIfNoCharge.toInt()}Wh,'
                ' cheap window at ${cheapAt.hour}:00');
          } else {
            // No near-future cheap window — charge now despite higher price
            LogService.log(
                '🌙 HEMS night.action: charger=SNU (reason=${_Reason.nightChargeNoCheapWindow}) '
                'deficit=${deficitIfNoCharge.toStringAsFixed(0)}Wh cheapAt=${cheapAt?.toIso8601String() ?? '-'}');
            await _applyCharger(_Chg.snu, _Reason.nightChargeNoCheapWindow);
          }
        }
      } else {
        final batteryFullish = data.batterySoc >= 99.5;
        final noDeficitReason =
            batteryFullish ? 'battery_full' : 'forecast_sufficient';
        LogService.log(
            '🌙 HEMS night.action: charger=OSO (reason=${_Reason.nightNoGridChargeNeeded}, detail=$noDeficitReason) '
            'soc=${data.batterySoc.toStringAsFixed(1)}% deficit=${deficitIfNoCharge.toStringAsFixed(0)}Wh');
        await _applyCharger(_Chg.oso, _Reason.nightNoGridChargeNeeded);
      }
      return;
    }

    // -------- 3. Daytime / evening: realtime first --------
    // Always prefer solar-only charger when sun is up.
    if (currentCharger != _Chg.oso) {
      await _applyCharger(_Chg.oso, _Reason.chargerDaySolarOnly);
    }

    final socOk = data.batterySoc >= tun.minOperatingSoc;
    final pvActive = pv > 80; // panels actually producing

    // ***CRITICAL: BATTERY RECOVERY PHASE***
    // If SOC is below 35%, we MUST stay on USB (grid-powered operation)
    // until SOC recovers to 45% or higher. This prevents cascade failures.
    if (data.batterySoc < 35.0) {
      await _applyOutput(_Out.usb, _Reason.reserveSocProtection, force: true);
      await _applyCharger(_Chg.snu, _Reason.reserveSocProtection);
      LogService.logCritical(
          'CRITICAL RECOVERY: SOC=${data.batterySoc.toStringAsFixed(1)}% < 35% '
          '→ Force USB+SNU until 45%+',
          category: 'BATTERY_RECOVERY');
      return;
    }
    // Hysteresis: once recovered above 45%, normal logic resumes
    if (data.batterySoc < 45.0 && currentOutput == _Out.usb) {
      // Stay in recovery mode but allow charger switch if safe
      await _applyCharger(_Chg.snu, _Reason.reserveSocProtection);
      LogService.logCritical(
          'HYSTERESIS: SOC=${data.batterySoc.toStringAsFixed(1)}% < 45% '
          'on USB → maintain SNU charging',
          category: 'BATTERY_RECOVERY');
      return;
    }

    // (a) Realtime SURPLUS — strong reason to use SBU now.
    // Phase 2b: use adaptive threshold instead of fixed tun.pvSurplusEnterW
    if (pvActive && socOk && surplus >= adaptivePvSurplusEnter) {
      await _applyOutput(_Out.sbu, _Reason.surplusEnterSbu);
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
      // Safety margin: 30 min of avg consumption or 5% battery capacity (whichever is higher)
      final avgConsumptionWh =
          (load > 0 ? load : 500.0) * 0.5; // 30 min at current load
      final capacityMarginWh = maxBatteryCapacityWh * 0.05; // 5% capacity
      final eveningSafetyWh = max(avgConsumptionWh, capacityMarginWh);

      final availableEnergyWh = max(0.0, currentEnergyWh - reserveEnergyWh);

      // Log evening context for debugging
      LogService.log(
          '🌅 HEMS evening: SOC=${data.batterySoc.toStringAsFixed(1)}%, '
          'available=${availableEnergyWh.toStringAsFixed(0)}Wh, '
          'safety=${eveningSafetyWh.toStringAsFixed(0)}Wh, '
          'load=${load.toInt()}W, deficit=${deficitTillNight.toInt()}Wh');

      // Condition 1: Critical low SOC — always USB + charge
      if (data.batterySoc <= reserveSoc + 1.0) {
        LogService.log('🆘 Evening critical: SOC near reserve → USB+charge');
        LogService.logCritical(
            'EVENING CRITICAL: SOC=${data.batterySoc.toStringAsFixed(1)}% ≤ ${(reserveSoc + 1.0).toStringAsFixed(1)}% '
            'at hour=$currentHour → Emergency USB+SNU',
            category: 'EVENING_PROTECTION');
        await _applyOutput(_Out.usb, _Reason.eveningReserveProtection);
        await _applyCharger(_Chg.snu, _Reason.eveningReserveProtection);
        return;
      }

      // Condition 2: Available energy is very low — stay on USB to preserve battery
      if (availableEnergyWh <= eveningSafetyWh) {
        LogService.log(
            '⚠️ Evening low energy: available ≤ safety margin → USB');
        LogService.logCritical(
            'EVENING LOW ENERGY: available=${availableEnergyWh.toStringAsFixed(0)}Wh ≤ '
            'safety=${eveningSafetyWh.toStringAsFixed(0)}Wh at hour=$currentHour → USB',
            category: 'EVENING_PROTECTION');
        await _applyOutput(_Out.usb, _Reason.eveningReserveProtection);
        return;
      }

      // Condition 3: Forecast deficit — only if we have time before night and sufficient buffer
      if (deficitTillNight > availableEnergyWh * 0.3) {
        // Deficit is >30% of available energy → risky, use grid
        LogService.log(
            '📉 Evening deficit risk: deficit=${deficitTillNight.toInt()}Wh '
            '> 30% of available=(${(availableEnergyWh * 0.3).toInt()}Wh) → USB');
        LogService.logCritical(
            'EVENING DEFICIT RISK: deficit=${deficitTillNight.toInt()}Wh '
            '> 30%*(${availableEnergyWh.toStringAsFixed(0)}Wh) at hour=$currentHour → USB',
            category: 'EVENING_PROTECTION');
        await _applyOutput(_Out.usb, _Reason.eveningReserveProtection);
        return;
      }

      // Condition 4: Safe to use battery if forecast is good or fully charged
      if (data.batterySoc >= reserveSoc + 10.0 &&
          deficitTillNight <= availableEnergyWh * 0.2) {
        // Forecast is manageable, buffer is good → use battery to reduce grid
        LogService.log(
            '✅ Evening battery safe: SOC=${data.batterySoc.toStringAsFixed(1)}%, '
            'deficit manageable → SBU');
        await _applyOutput(_Out.sbu, _Reason.eveningBatteryUse);
        return;
      }

      // Condition 5: Default evening behavior — stay on USB for safety
      LogService.log(
          '🔒 Evening default: stay on ${_modeName(currentOutput ?? _Out.usb)} '
          '(ambiguous conditions)');
      // Keep current state unless risky
      if (currentOutput != _Out.usb && deficitTillNight > 0) {
        await _applyOutput(_Out.usb, _Reason.eveningReserveProtection);
      }
      return;
    }

    // Daytime, ambiguous realtime
    if (deficitTillNight == 0) {
      await _applyOutput(_Out.sbu, _Reason.dayForecastOk);
    } else if (surplus <= tun.pvSurplusExitW && data.batterySoc < tun.midSoc) {
      // Real PV deficit + low-mid SOC: use grid, save sun for battery.
      await _applyOutput(_Out.usb, _Reason.dayForecastDeficitLowSoc);
    } else {
      // Otherwise: keep current state. Don't fight realtime small-surplus.
      LogService.log(
          'ℹ️ HEMS: hold ${_modeName(currentOutput ?? _Out.usb)} (reason=${_Reason.holdCurrentState}) '
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
      // Phase 3b: prefer demand forecast from profile when available
      final loadWh = _getLoadForecastWh(
          h, avgHourlyConsumptionStats, targetDate, liveLoadW);

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
      LogService.log(
          'ℹ️ NightArb: manual hold active (reason=${_Reason.manualOverrideHoldSkip}) — skipping.');
      return;
    }
    if (await _batteryKeepalive(data)) return;

    final hour = DateTime.now().hour;
    if (hour >= 23 || hour < 7) {
      LogService.log(
          '🌙 NightArb: forcing night strategy (soc=${data.batterySoc.toStringAsFixed(1)}%, hour=$hour) '
          '-> output=${_Out.usb}, charger=${_Chg.snu}');
      await _applyOutput(_Out.usb, _Reason.nightWindowUsb);
      await _applyCharger(_Chg.snu, _Reason.nightChargeDeficitCheapNow);
    } else {
      // Daytime: keep realtime-aware behaviour to avoid flapping.
      final surplus = data.pvPower - data.loadPower;
      if (data.pvPower > 80 &&
          data.batterySoc >= tun.minOperatingSoc &&
          surplus >= tun.pvSurplusEnterW) {
        await _applyOutput(_Out.sbu, _Reason.surplusEnterSbu);
      }
      await _applyCharger(_Chg.oso, _Reason.chargerDaySolarOnly);
    }
  }

  // ------------------------------------------------------------------
  // 4. Storm / reserve
  // ------------------------------------------------------------------
  Future<void> executeStormMode(InverterData data) async {
    _trackBatteryActivity(data);
    await _applyOutput(_Out.usb, _Reason.gridOutagePrecharge, force: true);
    await _applyCharger(_Chg.snu, _Reason.gridOutagePrecharge);
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
    final tzOffsetHours = now.timeZoneOffset.inMinutes / 60.0;
    final solarNoonLocal = 12.0 + tzOffsetHours - (longitude / 15.0);
    final sunrise = solarNoonLocal - daylightHours / 2.0;
    final sunset = solarNoonLocal + daylightHours / 2.0;

    final sunriseHour = sunrise.floor().clamp(0, 23);
    final sunsetHour = sunset.floor().clamp(0, 23);

    return (
      dayStart: (sunriseHour - 1).clamp(4, 12),
      eveningStart: (sunsetHour - 1).clamp(14, 22),
      nightStart: (sunsetHour + 1).clamp(18, 23),
    );
  }
}

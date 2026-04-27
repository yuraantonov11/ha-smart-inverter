import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:system_tray/system_tray.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import 'package:window_manager/window_manager.dart';
import '../l10n/app_localizations.dart';
import '../services/inverter_service.dart';
import '../services/hems_algorithm.dart';
import '../services/hems_tuning_service.dart';
import '../services/tariff_forecast_service.dart';
import '../services/demand_forecast_service.dart';
import '../services/weather_service.dart';
import '../services/log_service.dart';
import '../services/update_service.dart';
import '../services/secure_storage_service.dart';
import '../models/inverter_data.dart';
import '../models/hems_optimization_profile.dart';

class AppStateProvider extends ChangeNotifier {
  static const String _defaultAppVersionLabel = 'Version --';
  static const String _lastSnapshotKey = 'last_inverter_snapshot';
  static const String _skippedUpdateVersionKey = 'skipped_update_version';
  static const String _startInTrayKey = 'start_in_tray';
  final InverterService service = InverterService();
  final WeatherService weatherService = WeatherService();
  final TariffForecastService tariffForecastService = TariffForecastService();
  final DemandForecastService demandForecastService = DemandForecastService();
  late HemsAlgorithmService hemsService;
  final SystemTray systemTray = SystemTray();

  bool get _supportsDesktopIntegrations =>
      Platform.isWindows || Platform.isLinux || Platform.isMacOS;

  InverterData? data;
  bool isDataLoading = false;
  String statusMessage = '';

  Timer? _dataTimer;
  Timer? _automationTimer;
  Timer? _weatherTimer;

  // 0 = Адаптивний (Auto), 1 = Нічний арбітраж, 2 = Шторм/Резерв
  int smartMode = 0;
  double batteryCapacityAh = 230.0;
  double pvTotalCapacityW = 3000.0;
  double inverterMaxPowerW = 5000.0;

  /// Battery installation date — used by BatteryHealthModel for adaptive reserve SOC.
  DateTime batteryInstallDate = DateTime(DateTime.now().year - 2, 1, 1);

  /// HEMS optimization strategy selected by user.
  HemsOptimizationStrategy hemsStrategy = HemsOptimizationStrategy.hybrid;

  // Geo settings are editable by user; defaults are practical for Ukraine.
  double siteLatitude = 49.0;
  double siteLongitude = 31.0;
  String siteTimeZone = 'Europe/Kyiv';

  // HEMS time windows: user can choose astronomical (auto) or manual hours.
  bool useAstronomicalWindows = true;
  int manualDayStartHour = 7;
  int manualEveningStartHour = 17;
  int manualNightStartHour = 23;

  // Phase 3c: manual grid reliability input (no external API).
  bool plannedOutageEnabled = false;
  DateTime? plannedOutageStartAt;
  DateTime? plannedOutageEndAt;

  // Economics (monthly estimate cards on dashboard)
  double dayTariffUahPerKwh = 4.32;
  double nightTariffUahPerKwh = 2.16;
  double nightEnergySharePercent = 35.0;
  double? _monthLoadWh;
  double? _monthGridWh;
  List<({int day, double payableUah, double savedUah})> _monthDailyEconomics =
      const [];
  DateTime? _lastEconomicsRefreshAt;

  String solcastApiKey = '';
  String solcastResourceId = '';

  // --- НОВІ ЗМІННІ ДЛЯ РОЗУМНОГО АЛГОРИТМУ ---
  Map<String, double> hourlyForecast = {};
  Map<int, double> avgHourlyConsumptionStats = {};

  Map<String, double> historicalPvData = {};

  ThemeMode themeMode = ThemeMode.dark;
  String lang = 'en';

  bool get isEn => lang == 'en';
  bool isAutostartEnabled = false;
  bool isStartInTrayEnabled = false;
  String? savedEmail;
  String? userName;

  Map<String, dynamic>? userData;

  bool isAuthenticated = false;
  bool isCheckingAuth = true;

  bool isDeveloperMode = false;
  int _versionClickCount = 0;
  String _appVersionLabel = _defaultAppVersionLabel;

  bool _isSettingChanging = false;
  bool _isPollingInBackground = false;
  bool _timersStarted = false;
  int _consecutiveRealtimeNulls = 0;
  int _consecutiveDeviceNotFoundCount = 0;
  DateTime? _lastRealtimeNullLogAt;
  DateTime? _lastDeviceNotFoundLogAt;
  DateTime? _lastRecoveryAttemptAt;
  DateTime? _lastDeviceRecoveryAttemptAt;
  DateTime? _lastSuccessfulRealtimeAt;
  InverterData? _cachedSnapshotData;

  UpdateInfo? _updateInfo;
  bool _isCheckingForUpdates = false;
  DateTime? _lastUpdateCheckAt;
  String? _skippedUpdateVersion;

  bool get isSettingChanging => _isSettingChanging;
  bool get isConfigLoading => _isPollingInBackground;
  String get appVersionLabel => _appVersionLabel;
  bool get isInverterOffline => service.lastRealtimeOffline;
  DateTime? get lastSuccessfulRealtimeAt => _lastSuccessfulRealtimeAt;
  InverterData? get cachedSnapshotData => _cachedSnapshotData;
  UpdateInfo? get updateInfo => _updateInfo;
  bool get isCheckingForUpdates => _isCheckingForUpdates;
  DateTime? get lastUpdateCheckAt => _lastUpdateCheckAt;
  String? get skippedUpdateVersion => _skippedUpdateVersion;
  bool get hasPendingUpdate =>
      _updateInfo != null &&
      _updateInfo!.hasUpdate &&
      _updateInfo!.latestVersion != _skippedUpdateVersion;
  double? get monthLoadKwh =>
      _monthLoadWh == null ? null : _monthLoadWh! / 1000.0;
  double? get monthGridKwh =>
      _monthGridWh == null ? null : _monthGridWh! / 1000.0;
  double get nightEnergyShareFraction =>
      (nightEnergySharePercent / 100.0).clamp(0.0, 1.0).toDouble();
  double get effectiveTariffUahPerKwh =>
      (dayTariffUahPerKwh * (1.0 - nightEnergyShareFraction)) +
      (nightTariffUahPerKwh * nightEnergyShareFraction);
  // Backward compatibility for existing UI/usage.
  double get tariffUahPerKwh => effectiveTariffUahPerKwh;

  double? get monthToPayUah {
    final grid = monthGridKwh;
    if (grid == null) return null;
    final dayPart = grid * (1.0 - nightEnergyShareFraction);
    final nightPart = grid * nightEnergyShareFraction;
    return (dayPart * dayTariffUahPerKwh) + (nightPart * nightTariffUahPerKwh);
  }

  double? get monthSelfConsumedKwh {
    if (monthLoadKwh == null || monthGridKwh == null) return null;
    return (monthLoadKwh! - monthGridKwh!)
        .clamp(0.0, double.infinity)
        .toDouble();
  }

  double? get monthSavedUah {
    final selfConsumed = monthSelfConsumedKwh;
    if (selfConsumed == null) return null;
    return selfConsumed * effectiveTariffUahPerKwh;
  }

  List<({int day, double payableUah, double savedUah})>
      get monthDailyEconomics => _monthDailyEconomics;

  double get _monthProgressFraction {
    final now = DateTime.now();
    final daysInMonth = DateTime(now.year, now.month + 1, 0).day;
    final elapsed = (now.day - 1) + (now.hour / 24.0) + (now.minute / 1440.0);
    // Keep a small non-zero floor to avoid extreme division during app startup.
    return (elapsed / daysInMonth).clamp(1.0 / daysInMonth, 1.0).toDouble();
  }

  double get monthProgressFraction => _monthProgressFraction;
  int get monthProgressPercent => (monthProgressFraction * 100).round();

  double? get projectedMonthToPayUah {
    final actual = monthToPayUah;
    if (actual == null) return null;
    final projected = actual / _monthProgressFraction;
    return projected.isFinite ? projected : null;
  }

  double? get projectedMonthSavedUah {
    final actual = monthSavedUah;
    if (actual == null) return null;
    final projected = actual / _monthProgressFraction;
    return projected.isFinite ? projected : null;
  }

  AppStateProvider() {
    // Initial build with defaults; will be rebuilt after loadSettings().
    _rebuildHemsService();
  }

  /// (Re)creates HemsOptimizationProfile + HemsTuningService from current
  /// provider fields and wires them into hemsService.
  /// Call this after loading or changing any HEMS-relevant setting.
  void _rebuildHemsService() {
    final tariffForecast = tariffForecastService.buildDayNightForecast(
      dayTariffUahPerKwh: dayTariffUahPerKwh,
      nightTariffUahPerKwh: nightTariffUahPerKwh,
      dayStartHour: manualDayStartHour,
      nightStartHour: manualNightStartHour,
    );
    final demandForecast =
        demandForecastService.toDemandForecastData(avgHourlyConsumptionStats);
    final plannedOutages = <GridOutageEvent>[];
    if (plannedOutageEnabled &&
        plannedOutageStartAt != null &&
        plannedOutageEndAt != null &&
        plannedOutageEndAt!.isAfter(plannedOutageStartAt!)) {
      plannedOutages.add(
        GridOutageEvent(
          startTime: plannedOutageStartAt!,
          endTime: plannedOutageEndAt!,
          reason: 'manual_planned_outage',
        ),
      );
    }
    final gridForecast = GridReliabilityForecast(
      plannedOutages: plannedOutages,
      instabilityZones: const [],
    );

    final profile = HemsOptimizationProfile(
      systemId: 'home',
      pvPeakW: pvTotalCapacityW,
      batteryCapacityAh: batteryCapacityAh,
      optimizationStrategy: hemsStrategy,
      batteryHealth: BatteryHealthModel(installationDate: batteryInstallDate),
      tariffForecast: tariffForecast,
      demandForecast: demandForecast,
      gridForecast: gridForecast,
    );
    final tuning = HemsTuningService(profile);
    hemsService = HemsAlgorithmService(
      this,
      optimizationProfile: profile,
      tuningService: tuning,
    );
    LogService.log(
      '⚙️ HEMS profile rebuilt: pv=${pvTotalCapacityW.toInt()}W bat=${batteryCapacityAh.toInt()}Ah '
      'strategy=${hemsStrategy.name} installDate=${batteryInstallDate.year}',
    );
  }

  Future<AppLocalizations> _getL10n() async {
    return await AppLocalizations.delegate.load(Locale(lang));
  }

  Future<void> fetchProfile() async {
    if (service.userId == null) {
      LogService.log('⚠️ profile.fetch skipped: userId is null');
      return;
    }

    LogService.log('👤 profile.fetch start: userId=${service.userId}');

    final data = await service.getUserInfo();
    if (data.isNotEmpty) {
      userData = data;
      LogService.log(
          '✅ profile.fetch success: account=${data['account']}, uid=${data['uid']}');
      notifyListeners();
    } else {
      LogService.log('⚠️ profile.fetch empty response');
    }
  }

  Future<void> loadSettings() async {
    final prefs = await SharedPreferences.getInstance();

    isDeveloperMode = prefs.getBool('is_developer_mode') ?? false;

    final isDark = prefs.getBool('is_dark_theme') ?? true;
    themeMode = isDark ? ThemeMode.dark : ThemeMode.light;

    batteryCapacityAh = prefs.getDouble('battery_capacity_ah') ?? 230.0;
    pvTotalCapacityW = prefs.getDouble('pv_total_capacity_w') ?? 3000.0;
    inverterMaxPowerW = prefs.getDouble('inverter_max_power_w') ?? 5000.0;
    siteLatitude = prefs.getDouble('site_latitude') ?? 49.0;
    siteLongitude = prefs.getDouble('site_longitude') ?? 31.0;
    siteTimeZone = prefs.getString('site_time_zone') ?? 'Europe/Kyiv';
    useAstronomicalWindows = prefs.getBool('use_astronomical_windows') ?? true;
    manualDayStartHour = prefs.getInt('manual_day_start_hour') ?? 7;
    manualEveningStartHour = prefs.getInt('manual_evening_start_hour') ?? 17;
    manualNightStartHour = prefs.getInt('manual_night_start_hour') ?? 23;
    plannedOutageEnabled = prefs.getBool('planned_outage_enabled') ?? false;
    final plannedStartMs = prefs.getInt('planned_outage_start_ms');
    final plannedEndMs = prefs.getInt('planned_outage_end_ms');
    plannedOutageStartAt = plannedStartMs == null
        ? null
        : DateTime.fromMillisecondsSinceEpoch(plannedStartMs);
    plannedOutageEndAt = plannedEndMs == null
        ? null
        : DateTime.fromMillisecondsSinceEpoch(plannedEndMs);
    final legacyTariff = prefs.getDouble('energy_tariff_uah_per_kwh');
    dayTariffUahPerKwh = prefs.getDouble('energy_tariff_day_uah_per_kwh') ??
        legacyTariff ??
        4.32;
    nightTariffUahPerKwh = prefs.getDouble('energy_tariff_night_uah_per_kwh') ??
        ((legacyTariff ?? dayTariffUahPerKwh) * 0.5);
    nightEnergySharePercent =
        prefs.getDouble('energy_night_share_percent') ?? 35.0;

    // Battery health
    final installMs = prefs.getInt('battery_install_date_ms');
    if (installMs != null) {
      batteryInstallDate = DateTime.fromMillisecondsSinceEpoch(installMs);
    }
    // HEMS strategy
    final strategyIdx = prefs.getInt('hems_strategy_index') ?? 4; // hybrid
    hemsStrategy = HemsOptimizationStrategy.values[
        strategyIdx.clamp(0, HemsOptimizationStrategy.values.length - 1)];

    // Rebuild service now that real settings are loaded.
    _rebuildHemsService();

    smartMode = prefs.getInt('smart_mode') ?? 0;
    lang = prefs.getString('app_lang') ?? 'en';
    if (_supportsDesktopIntegrations) {
      isAutostartEnabled = await launchAtStartup.isEnabled();
    } else {
      isAutostartEnabled = false;
    }
    isStartInTrayEnabled = prefs.getBool(_startInTrayKey) ?? false;
    final l10n = await _getL10n();
    userName = prefs.getString('user_name') ?? l10n.userNameDefault;
    savedEmail = prefs.getString('saved_email');
    _skippedUpdateVersion = prefs.getString(_skippedUpdateVersionKey);

    // Load historical PV data
    final historicalDataStr = prefs.getString('historical_pv_data');
    if (historicalDataStr != null) {
      try {
        final decoded = json.decode(historicalDataStr) as Map<String, dynamic>;
        historicalPvData = decoded
            .map((key, value) => MapEntry(key, (value as num).toDouble()));
      } catch (e) {
        LogService.log('Error loading historical PV data: $e');
        historicalPvData = {};
      }
    }

    final consumptionProfileStr = prefs.getString('consumption_profile_ewma');
    if (consumptionProfileStr != null && consumptionProfileStr.isNotEmpty) {
      try {
        final decoded =
            json.decode(consumptionProfileStr) as Map<String, dynamic>;
        avgHourlyConsumptionStats = decoded.map(
          (k, v) => MapEntry(int.tryParse(k) ?? 0, (v as num).toDouble()),
        );
      } catch (e) {
        LogService.log('Error loading EWMA consumption profile: $e');
        avgHourlyConsumptionStats = demandForecastService.buildDefaultProfile();
      }
    }

    // Load last successful realtime snapshot (used when inverter is offline).
    final snapshotStr = prefs.getString(_lastSnapshotKey);
    if (snapshotStr != null && snapshotStr.isNotEmpty) {
      try {
        final decoded = json.decode(snapshotStr) as Map<String, dynamic>;
        _cachedSnapshotData = InverterData.fromCacheMap(decoded);
      } catch (e) {
        LogService.log('Error loading cached inverter snapshot: $e');
        _cachedSnapshotData = null;
      }
    }

    // Перевірка автологіну при старті
    var loggedIn = await autoLogin();
    isAuthenticated = loggedIn;
    isCheckingAuth = false;

    if (loggedIn) {
      startTimers();
      await fetchProfile();
    }

    await _loadAppVersion();
    await _updateWeatherForecast();
    if (loggedIn) {
      await _updateMonthlyEconomics(force: true);
    }
    await _updateStatusMessage(true);
    // Fire-and-forget startup check for a non-intrusive update badge in Settings.
    // ignore: unawaited_futures
    checkForUpdates();
    notifyListeners();
  }

  Future<UpdateInfo> checkForUpdates({bool force = false}) async {
    if (_isCheckingForUpdates) {
      return _updateInfo ??
          const UpdateInfo(
            hasUpdate: false,
            currentVersion: '--',
            latestVersion: '--',
          );
    }

    _isCheckingForUpdates = true;
    notifyListeners();
    try {
      final info = await UpdateService.fetchUpdateInfo();
      _updateInfo = info;
      _lastUpdateCheckAt = DateTime.now();

      if (force && _skippedUpdateVersion == info.latestVersion) {
        _skippedUpdateVersion = null;
        final prefs = await SharedPreferences.getInstance();
        await prefs.remove(_skippedUpdateVersionKey);
      }

      return info;
    } finally {
      _isCheckingForUpdates = false;
      notifyListeners();
    }
  }

  Future<void> skipLatestUpdate() async {
    final info = _updateInfo;
    if (info == null || !info.hasUpdate) return;
    _skippedUpdateVersion = info.latestVersion;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_skippedUpdateVersionKey, info.latestVersion);
    notifyListeners();
  }

  Future<void> clearSkippedUpdate() async {
    _skippedUpdateVersion = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_skippedUpdateVersionKey);
    notifyListeners();
  }

  Future<void> _loadAppVersion() async {
    try {
      final packageInfo = await PackageInfo.fromPlatform();
      final version = packageInfo.version.trim();
      final build = packageInfo.buildNumber.trim();
      if (version.isNotEmpty && build.isNotEmpty) {
        _appVersionLabel = 'Version $version+$build';
      } else if (version.isNotEmpty) {
        _appVersionLabel = 'Version $version';
      } else {
        _appVersionLabel = _defaultAppVersionLabel;
      }
    } catch (e) {
      LogService.log('Failed to load app version: $e');
      _appVersionLabel = _defaultAppVersionLabel;
    }
  }

  Future<void> saveTariffUahPerKwh(double tariff) async {
    final clamped = tariff.clamp(0.0, 999.0).toDouble();
    dayTariffUahPerKwh = clamped;
    nightTariffUahPerKwh = (clamped * 0.5).clamp(0.0, 999.0).toDouble();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('energy_tariff_uah_per_kwh', clamped);
    await prefs.setDouble('energy_tariff_day_uah_per_kwh', dayTariffUahPerKwh);
    await prefs.setDouble(
        'energy_tariff_night_uah_per_kwh', nightTariffUahPerKwh);
    notifyListeners();
    // Recompute money cards immediately with new tariff.
    await _updateMonthlyEconomics(force: false);
  }

  Future<void> saveTimeOfUseTariffs({
    required double dayTariff,
    required double nightTariff,
    required double nightSharePercent,
  }) async {
    dayTariffUahPerKwh = dayTariff.clamp(0.0, 999.0).toDouble();
    nightTariffUahPerKwh = nightTariff.clamp(0.0, 999.0).toDouble();
    nightEnergySharePercent = nightSharePercent.clamp(0.0, 100.0).toDouble();

    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('energy_tariff_day_uah_per_kwh', dayTariffUahPerKwh);
    await prefs.setDouble(
        'energy_tariff_night_uah_per_kwh', nightTariffUahPerKwh);
    await prefs.setDouble(
        'energy_night_share_percent', nightEnergySharePercent);
    // Keep legacy key in sync for older builds/migrations.
    await prefs.setDouble(
        'energy_tariff_uah_per_kwh', effectiveTariffUahPerKwh);

    notifyListeners();
    await _updateMonthlyEconomics(force: false);
  }

  double estimateNightEnergySharePercent() {
    if (avgHourlyConsumptionStats.isEmpty) {
      return nightEnergySharePercent;
    }
    var total = 0.0;
    var night = 0.0;
    var day = 0.0;
    for (var h = 0; h < 24; h++) {
      final raw = avgHourlyConsumptionStats[h] ?? 0.0;
      final v = raw.isFinite ? raw.clamp(0.0, 1e9).toDouble() : 0.0;
      total += v;
      if (h >= 23 || h < 7) {
        night += v;
      } else {
        day += v;
      }
    }
    // Keep existing value when profile is too sparse/biased (prevents accidental 0%).
    if (total < 100 || night <= 0 || day <= 0) return nightEnergySharePercent;
    final estimated = (night / total) * 100.0;
    if (!estimated.isFinite) return nightEnergySharePercent;
    return estimated.clamp(1.0, 99.0).toDouble();
  }

  Future<void> _updateMonthlyEconomics({bool force = false}) async {
    if (service.currentStationId == null) return;
    final now = DateTime.now();
    if (!force &&
        _lastEconomicsRefreshAt != null &&
        now.difference(_lastEconomicsRefreshAt!) <
            const Duration(minutes: 20)) {
      return;
    }
    try {
      final summary = await service.getMonthlyEnergySummary(now);
      final dailyEnergy = await service.getMonthlyDailyEnergy(now);

      _monthDailyEconomics = dailyEnergy.map((e) {
        final gridKwh = e.gridWh / 1000.0;
        final loadKwh = e.loadWh / 1000.0;
        final selfKwh = (loadKwh - gridKwh).clamp(0.0, double.infinity);
        final dayPart = gridKwh * (1.0 - nightEnergyShareFraction);
        final nightPart = gridKwh * nightEnergyShareFraction;
        final payable =
            (dayPart * dayTariffUahPerKwh) + (nightPart * nightTariffUahPerKwh);
        final saved = selfKwh * effectiveTariffUahPerKwh;
        return (day: e.day, payableUah: payable, savedUah: saved);
      }).toList(growable: false);

      if (summary != null) {
        _monthLoadWh = summary.loadWh;
        _monthGridWh = summary.gridWh;
      } else if (dailyEnergy.isNotEmpty) {
        _monthLoadWh =
            dailyEnergy.fold<double>(0.0, (sum, e) => sum + e.loadWh);
        _monthGridWh =
            dailyEnergy.fold<double>(0.0, (sum, e) => sum + e.gridWh);
      }

      _lastEconomicsRefreshAt = now;
      notifyListeners();
    } catch (e) {
      LogService.log('⚠️ monthly economics refresh failed', error: e);
    }
  }

  Future<void> saveHardwareSettings(double battery, double pv, double inverter,
      {DateTime? installDate}) async {
    final prefs = await SharedPreferences.getInstance();
    batteryCapacityAh = battery;
    pvTotalCapacityW = pv;
    inverterMaxPowerW = inverter;
    await prefs.setDouble('battery_capacity_ah', battery);
    await prefs.setDouble('pv_total_capacity_w', pv);
    await prefs.setDouble('inverter_max_power_w', inverter);
    if (installDate != null) {
      batteryInstallDate = installDate;
      await prefs.setInt(
          'battery_install_date_ms', installDate.millisecondsSinceEpoch);
    }
    _rebuildHemsService();
    notifyListeners();
    await _updateWeatherForecast();
  }

  Future<void> saveHemsStrategy(HemsOptimizationStrategy strategy) async {
    hemsStrategy = strategy;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('hems_strategy_index',
        HemsOptimizationStrategy.values.indexOf(strategy));
    _rebuildHemsService();
    notifyListeners();
    LogService.log('⚙️ HEMS strategy changed: ${strategy.name}');
  }

  Future<void> saveGeoSettings({
    required double latitude,
    required double longitude,
    required String timeZone,
    bool? useAstronomical,
    int? dayStartHour,
    int? eveningStartHour,
    int? nightStartHour,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    siteLatitude = latitude;
    siteLongitude = longitude;
    siteTimeZone = timeZone.trim().isEmpty ? 'UTC' : timeZone.trim();
    if (useAstronomical != null) {
      useAstronomicalWindows = useAstronomical;
    }
    if (dayStartHour != null) {
      manualDayStartHour = dayStartHour.clamp(0, 23);
    }
    if (eveningStartHour != null) {
      manualEveningStartHour = eveningStartHour.clamp(0, 23);
    }
    if (nightStartHour != null) {
      manualNightStartHour = nightStartHour.clamp(0, 23);
    }
    await prefs.setDouble('site_latitude', siteLatitude);
    await prefs.setDouble('site_longitude', siteLongitude);
    await prefs.setString('site_time_zone', siteTimeZone);
    await prefs.setBool('use_astronomical_windows', useAstronomicalWindows);
    await prefs.setInt('manual_day_start_hour', manualDayStartHour);
    await prefs.setInt('manual_evening_start_hour', manualEveningStartHour);
    await prefs.setInt('manual_night_start_hour', manualNightStartHour);
    _rebuildHemsService();
    LogService.log(
        '📍 geo/settings updated: lat=${siteLatitude.toStringAsFixed(4)}, lon=${siteLongitude.toStringAsFixed(4)}, tz=$siteTimeZone, astro=$useAstronomicalWindows, manual=[$manualDayStartHour,$manualEveningStartHour,$manualNightStartHour]');
    notifyListeners();
  }

  Future<void> savePlannedOutage({
    required bool enabled,
    DateTime? startAt,
    DateTime? endAt,
  }) async {
    plannedOutageEnabled = enabled;
    plannedOutageStartAt = startAt;
    plannedOutageEndAt = endAt;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('planned_outage_enabled', plannedOutageEnabled);
    if (startAt != null) {
      await prefs.setInt(
          'planned_outage_start_ms', startAt.millisecondsSinceEpoch);
    } else {
      await prefs.remove('planned_outage_start_ms');
    }
    if (endAt != null) {
      await prefs.setInt('planned_outage_end_ms', endAt.millisecondsSinceEpoch);
    } else {
      await prefs.remove('planned_outage_end_ms');
    }
    _rebuildHemsService();
    notifyListeners();
  }

  Future<void> _updateWeatherForecast() async {
    final dynamicForecastMap = await weatherService.fetchLocalForecast(
      pvCapacityW: pvTotalCapacityW,
      efficiency: 0.85,
      historicalPvData: historicalPvData,
    );
    hourlyForecast = dynamicForecastMap;
    notifyListeners();
  }

  // --- ОНОВЛЕННЯ СТАТИСТИКИ СІМ'Ї ---
  Future<void> _updateConsumptionStats() async {
    if (service.currentStationId == null) return;

    try {
      if (avgHourlyConsumptionStats.isEmpty) {
        avgHourlyConsumptionStats = demandForecastService.buildDefaultProfile();
      }
      notifyListeners();
    } catch (e) {
      debugPrint('Помилка завантаження статистики: $e');
    }
  }

  Future<void> _persistConsumptionProfile() async {
    final prefs = await SharedPreferences.getInstance();
    final encoded = avgHourlyConsumptionStats.map(
      (k, v) => MapEntry(k.toString(), v),
    );
    await prefs.setString('consumption_profile_ewma', json.encode(encoded));
  }

  void _recordPvHistory(InverterData currentData) async {
    try {
      final now = DateTime.now();
      final timeKey =
          "${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}T${now.hour.toString().padLeft(2, '0')}:00";

      historicalPvData[timeKey] = currentData.pvPower;

      // Limit historical data to last 14 days to prevent unlimited growth
      final cutoffDate = now.subtract(const Duration(days: 14));
      historicalPvData.removeWhere((key, value) {
        try {
          final date = DateTime.parse(key.replaceAll(' ', 'T'));
          return date.isBefore(cutoffDate);
        } catch (_) {
          return true; // Remove invalid keys
        }
      });

      // Save to SharedPreferences
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
          'historical_pv_data', json.encode(historicalPvData));
    } catch (e, stack) {
      // БЕЗПЕКА: Правильна обробка помилок вместо fire-and-forget
      LogService.log('❌ Error recording PV history', error: e, stack: stack);
    }
  }

  void handleVersionClick() async {
    _versionClickCount++;
    if (_versionClickCount >= 7 && !isDeveloperMode) {
      isDeveloperMode = true;
      _versionClickCount = 0;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('is_developer_mode', true);
      notifyListeners();
    }
  }

  String get displayAccount => userData?['account'] ?? 'N/A';

  String get displayName => userData?['name'] ?? 'N/A';

  String get displayEmail => userData?['email'] ?? '';

  String get displayPhone => userData?['cellphone'] ?? '';

  void startTimers() {
    if (_timersStarted) return;
    _timersStarted = true;
    if (_supportsDesktopIntegrations) {
      // ignore: unawaited_futures
      _initTray();
    }
    fetchData();
    _dataTimer = Timer.periodic(const Duration(minutes: 1), (_) => fetchData());
    _automationTimer =
        Timer.periodic(const Duration(minutes: 1), (_) => _checkAutomations());
    _weatherTimer = Timer.periodic(
        const Duration(hours: 1), (_) => _updateWeatherForecast());
  }

  void stopTimers() {
    if (!_timersStarted) return;
    _timersStarted = false;
    _dataTimer?.cancel();
    _automationTimer?.cancel();
    _weatherTimer?.cancel();
    if (_supportsDesktopIntegrations) {
      systemTray.destroy();
    }
  }

  Future<void> setLanguage(String newLang) async {
    lang = newLang;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_lang', lang);
    await _updateStatusMessage(true);
    if (isAuthenticated && _supportsDesktopIntegrations) {
      await _updateTrayMenu();
    }
    notifyListeners();
  }

  Future<void> _updateStatusMessage(bool isSuccess) async {
    final l10n = await _getL10n();
    if (isSuccess) {
      final time = DateTime.now().toString().substring(11, 19);
      statusMessage = l10n.updatedAt(time);
    } else {
      statusMessage = l10n.updateFailed;
    }
  }

  Future<void> toggleTheme() async {
    themeMode = themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_dark_theme', themeMode == ThemeMode.dark);
    notifyListeners();
  }

  Future<void> setSmartMode(int mode) async {
    smartMode = mode;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('smart_mode', mode);
    notifyListeners();
    await _checkAutomations(isManualTrigger: true);
  }

  Future<void> toggleAutostart(bool val) async {
    if (!_supportsDesktopIntegrations) {
      isAutostartEnabled = false;
      notifyListeners();
      return;
    }
    if (val) {
      await launchAtStartup.enable();
    } else {
      await launchAtStartup.disable();
    }
    isAutostartEnabled = await launchAtStartup.isEnabled();
    if (isAuthenticated) await _updateTrayMenu();
    notifyListeners();
  }

  Future<void> toggleStartInTray(bool val) async {
    if (!_supportsDesktopIntegrations) {
      isStartInTrayEnabled = false;
      notifyListeners();
      return;
    }
    isStartInTrayEnabled = val;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_startInTrayKey, val);
    if (isAuthenticated) await _updateTrayMenu();
    notifyListeners();
  }

  Future<void> updateProfile(String newName) async {
    userName = newName;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('user_name', newName);
    notifyListeners();
  }

  String get userId => service.userId ?? 'N/A';

  Future<bool> autoLogin() async {
    final prefs = await SharedPreferences.getInstance();
    final email = prefs.getString('saved_email');
    var pass = await SecureStorageService.getPassword();

    // Backward compatibility: previous builds stored password in SharedPreferences.
    if ((pass == null || pass.isEmpty)) {
      final legacyPass = prefs.getString('saved_pass');
      if (legacyPass != null && legacyPass.isNotEmpty) {
        try {
          await SecureStorageService.savePassword(legacyPass);
          await prefs.remove('saved_pass');
        } catch (e) {
          LogService.log('Failed to migrate legacy password to secure storage',
              error: e);
        }
        pass = legacyPass;
      }
    }

    if (email != null && email.isNotEmpty && pass != null && pass.isNotEmpty) {
      final success = await service.login(email, pass);
      if (success) {
        final token = service.accessToken;
        if (token != null && token.isNotEmpty) {
          try {
            await SecureStorageService.saveToken(token);
          } catch (e) {
            LogService.log('Failed to persist access token after auto login',
                error: e);
          }
        }
      }
      return success;
    }
    return false;
  }

  Future<bool> login(String email, String pass) async {
    var success = await service.login(email, pass);
    if (success) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('saved_email', email);
      await prefs.remove('saved_pass');
      try {
        await SecureStorageService.savePassword(pass);
      } catch (e) {
        LogService.log('Failed to persist password securely after login',
            error: e);
      }
      final token = service.accessToken;
      if (token != null && token.isNotEmpty) {
        try {
          await SecureStorageService.saveToken(token);
        } catch (e) {
          LogService.log('Failed to persist access token after login',
              error: e);
        }
      }
      savedEmail = email;

      isAuthenticated = true;
      startTimers();
      await fetchProfile();
      notifyListeners();
    }
    return success;
  }

  Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('saved_email');
    await prefs.remove('saved_pass');
    try {
      await SecureStorageService.deletePassword();
      await SecureStorageService.deleteToken();
    } catch (e) {
      LogService.log('Failed to clear secure session data during logout',
          error: e);
    }
    savedEmail = null;
    service.accessToken = null;
    service.userId = null;
    userData = null;
    data = null;
    _lastSuccessfulRealtimeAt = null;
    _cachedSnapshotData = null;
    await prefs.remove(_lastSnapshotKey);

    stopTimers();
    isAuthenticated = false;
    notifyListeners();
  }

  Future<void> _initTray() async {
    if (!_supportsDesktopIntegrations) return;
    await systemTray.initSystemTray(
      title: 'Inverter',
      iconPath:
          Platform.isWindows ? 'assets/app_icon.ico' : 'assets/app_icon.png',
    );
    systemTray.registerSystemTrayEventHandler((eventName) {
      if (eventName == kSystemTrayEventClick) {
        windowManager.show();
      } else if (eventName == kSystemTrayEventRightClick) {
        systemTray.popUpContextMenu();
      }
    });
  }

  Future<void> _updateTrayMenu() async {
    if (!_supportsDesktopIntegrations) return;
    final l10n = await _getL10n();
    final menu = Menu();
    await menu.buildFrom([
      MenuItemLabel(label: l10n.enableSolar, onClicked: (_) => setMode(2)),
      MenuItemLabel(label: l10n.enableGrid, onClicked: (_) => setMode(0)),
      MenuSeparator(),
      MenuItemCheckbox(
        label: l10n.startWithWindows,
        checked: isAutostartEnabled,
        onClicked: (item) async => await toggleAutostart(!isAutostartEnabled),
      ),
      MenuItemCheckbox(
        label: l10n.startInTray,
        checked: isStartInTrayEnabled,
        onClicked: (item) async =>
            await toggleStartInTray(!isStartInTrayEnabled),
      ),
      MenuSeparator(),
      MenuItemLabel(
          label: l10n.showApp,
          onClicked: (_) async {
            await windowManager.show();
            await windowManager.focus();
          }),
      MenuItemLabel(
          label: l10n.exit,
          onClicked: (_) async {
            stopTimers();
            await windowManager.destroy();
            exit(0);
          }),
    ]);
    await systemTray.setContextMenu(menu);
    await systemTray.setTitle(
        l10n.batteryLevel(data?.batterySoc.toStringAsFixed(0) ?? '--'));
  }

  Future<void> fetchData() async {
    if (isDataLoading) return;

    final hasDevice = await service.ensureDeviceSelected();
    if (!hasDevice) {
      final now = DateTime.now();
      _consecutiveDeviceNotFoundCount++;
      if (_lastDeviceNotFoundLogAt == null ||
          now.difference(_lastDeviceNotFoundLogAt!) >
              const Duration(minutes: 15)) {
        LogService.log(
            '⚠️ fetchData: пристрій не знайдено, deviceSn=${service.deviceSn}, count=$_consecutiveDeviceNotFoundCount');
        _lastDeviceNotFoundLogAt = now;
      }

      // Auto-recovery: after 3 consecutive failures try re-selecting device,
      // after 10 failures re-login entirely (token may have expired).
      final recoveryCooldown = _lastDeviceRecoveryAttemptAt == null ||
          now.difference(_lastDeviceRecoveryAttemptAt!) >
              const Duration(minutes: 10);
      if (recoveryCooldown) {
        if (_consecutiveDeviceNotFoundCount >= 10) {
          _lastDeviceRecoveryAttemptAt = now;
          LogService.log(
              '🔑 fetchData: device not found for $_consecutiveDeviceNotFoundCount cycles, attempting re-login…');
          final prefs = await SharedPreferences.getInstance();
          final email = prefs.getString('saved_email');
          final pass = await SecureStorageService.getPassword();
          if (email != null && pass != null) {
            final ok = await service.login(email, pass);
            LogService.log('🔑 re-login result=$ok');
          }
        } else if (_consecutiveDeviceNotFoundCount >= 3) {
          _lastDeviceRecoveryAttemptAt = now;
          service.deviceSn = null;
          service.currentStationId = null;
          service.invalidateConfigCache();
          final reselected = await service.ensureDeviceSelected();
          LogService.log(
              '🔁 fetchData: forced device reselection after $_consecutiveDeviceNotFoundCount failures → ${reselected ? "ok, sn=${service.deviceSn}" : "failed"}');
        }
      }

      await _updateStatusMessage(false);
      notifyListeners();
      return;
    }
    _consecutiveDeviceNotFoundCount = 0;

    final previousConfigs = data?.rawFields['fullConfigs'];

    isDataLoading = true;
    notifyListeners();

    final newData = await service.getRealTimeData();

    if (newData != null) {
      _consecutiveRealtimeNulls = 0;
      _lastSuccessfulRealtimeAt = DateTime.now();
      if (previousConfigs != null) {
        newData.rawFields['fullConfigs'] = previousConfigs;
      }
      data = newData;
      _cachedSnapshotData = newData;
      _recordPvHistory(newData);
      avgHourlyConsumptionStats = demandForecastService.updateEwmaProfile(
        avgHourlyConsumptionStats,
        timestamp: DateTime.now(),
        loadW: newData.loadPower,
      );
      await _persistConsumptionProfile();
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
          _lastSnapshotKey, json.encode(newData.toCacheMap()));
      await _updateStatusMessage(true);
      if (isAuthenticated && _supportsDesktopIntegrations) {
        await _updateTrayMenu();
      }

      if (avgHourlyConsumptionStats.isEmpty &&
          service.currentStationId != null) {
        await _updateConsumptionStats();
      }
      await _updateMonthlyEconomics();
    } else {
      _consecutiveRealtimeNulls++;
      final now = DateTime.now();
      if (_lastRealtimeNullLogAt == null ||
          now.difference(_lastRealtimeNullLogAt!) >
              const Duration(seconds: 75)) {
        LogService.log(
            '⚠️ fetchData: getRealTimeData повернув null, deviceSn=${service.deviceSn}, nulls=$_consecutiveRealtimeNulls');
        _lastRealtimeNullLogAt = now;
      }

      // Recover from stale/mismatched device assignment after repeated null cycles.
      final recoveryCooldownPassed = _lastRecoveryAttemptAt == null ||
          now.difference(_lastRecoveryAttemptAt!) > const Duration(minutes: 5);
      if (_consecutiveRealtimeNulls >= 3 &&
          recoveryCooldownPassed &&
          !service.lastRealtimeOffline) {
        _lastRecoveryAttemptAt = now;
        service.deviceSn = null;
        service.currentStationId = null;
        service.invalidateConfigCache();
        final reselectionOk = await service.ensureDeviceSelected();
        LogService.log(
            '🔁 fetchData recovery: device reselection=${reselectionOk ? 'ok' : 'failed'}, newDeviceSn=${service.deviceSn}');
      } else if (service.lastRealtimeOffline &&
          _consecutiveRealtimeNulls >= 3) {
        statusMessage = 'Інвертор офлайн. Очікуємо відновлення зв\'язку...';
      }
      if (!service.lastRealtimeOffline) {
        await _updateStatusMessage(false);
      }
    }

    isDataLoading = false;
    notifyListeners();

    if (newData != null) {
      final hasConfigs = data?.rawFields['fullConfigs']
              is Map<String, dynamic> &&
          (data!.rawFields['fullConfigs'] as Map<String, dynamic>).isNotEmpty;
      if (hasConfigs) return;

      // Fire-and-forget — don't block the fetch cycle waiting for configs
      // ignore: unawaited_futures
      _fetchConfigsInBackground(newData);
    }
  }

  Future<void> _fetchConfigsInBackground(InverterData currentData) async {
    await Future.delayed(const Duration(seconds: 2));

    // Quick check — may return from cache or trigger the batch
    final initial = await service.getDeviceFullConfigs();
    if (initial != null) {
      data?.rawFields['fullConfigs'] = initial;
      notifyListeners();
      return;
    }

    // Batch was triggered but data not ready; poll in background
    if (_isPollingInBackground) return;
    _isPollingInBackground = true;
    notifyListeners();
    try {
      final polled = await service.pollForConfigsBackground();
      if (polled != null) {
        data?.rawFields['fullConfigs'] = polled;
      }
    } finally {
      _isPollingInBackground = false;
      notifyListeners();
    }
  }

  Future<void> changeInverterSetting(String key, String value) async {
    _isSettingChanging = true;
    notifyListeners();

    final success = await service.updateSetting(key, value);

    if (success) {
      // Update local cache (configAttributeStates format has {value, valueDisplay, ...})
      final fullConfigs = data?.rawFields['fullConfigs'];
      if (fullConfigs is Map<String, dynamic> && fullConfigs.containsKey(key)) {
        final entry = fullConfigs[key];
        if (entry is Map<String, dynamic>) {
          entry['value'] = num.tryParse(value) ?? value;
          entry['valueDisplay'] = value;
        } else {
          fullConfigs[key] = value;
        }
      }
      await Future.delayed(const Duration(seconds: 5));
    }

    _isSettingChanging = false;
    notifyListeners();

    Future.delayed(const Duration(seconds: 65), () => fetchData());
  }

  /// Force re-fetch device configs (invalidates cooldown cache)
  Future<void> refreshDeviceConfigs() async {
    if (_isPollingInBackground) return; // already running
    service.invalidateConfigCache();
    if (data == null) return;
    await _fetchConfigsInBackground(data!);
  }

  Future<void> changeSetting(String key, String value) async {
    final l10n = await _getL10n();
    final oldFields = data?.rawFields != null
        ? Map<String, dynamic>.from(data!.rawFields)
        : null;

    final localKey = key.replaceAll('Setting', '');
    if (data != null && data!.rawFields.containsKey(localKey)) {
      data!.rawFields[localKey]['value'] = value;
      notifyListeners();
    }

    var success = await service.setConfigItem(key, value);

    if (success) {
      statusMessage = l10n.updated;
      await fetchData();
    } else {
      if (oldFields != null && data != null) {
        data!.rawFields = oldFields;
      }
      statusMessage = l10n.updateFailed;
      notifyListeners();
    }
  }

  Future<void> setMode(int mode) async {
    await changeSetting('outputSourcePrioritySetting', mode.toString());
  }

  Future<void> _checkAutomations({bool isManualTrigger = false}) async {
    if (data == null) return;

    // Emergency battery protection: if data is stale (device unreachable for
    // >30 min) and battery SOC is low, force USB (grid) mode to prevent drain.
    final staleThreshold = const Duration(minutes: 30);
    final isDataStale = _lastSuccessfulRealtimeAt != null &&
        DateTime.now().difference(_lastSuccessfulRealtimeAt!) > staleThreshold;
    if (isDataStale) {
      final soc = data!.batterySoc;
      final hour = DateTime.now().hour;
      final isNight = hour >= 22 || hour < 7;
      if (soc < 30 || isNight) {
        final currentOutput =
            data!.rawFields['outputSourcePriority']?['value']?.toString();
        if (currentOutput != '0') {
          LogService.log(
              '🆘 ЗАХИСТ АКБ: дані застарілі (${DateTime.now().difference(_lastSuccessfulRealtimeAt!).inMinutes} хв), '
              'SOC=$soc%, нічний=$isNight → примусово USB (мережа) для захисту акумулятора!',
              level: LogLevel.error);
          await service.setMode(0);
        } else {
          LogService.log('🛡️ ЗАХИСТ АКБ: дані застарілі, вже на USB — добре.',
              level: LogLevel.warn);
        }
      }
      return; // skip HEMS when data is stale
    }

    if (!isManualTrigger) {
      await hemsService.enforceAcousticComfort(data!);
    }

    // Phase 3c: if a planned outage is near, precharge by forcing Storm mode.
    if (plannedOutageEnabled && plannedOutageStartAt != null) {
      final now = DateTime.now();
      final untilOutage = plannedOutageStartAt!.difference(now);
      if (untilOutage.inMinutes >= 0 &&
          untilOutage <= const Duration(hours: 6)) {
        LogService.log(
            '⚠️ Grid alert: planned outage in ${untilOutage.inMinutes}m — forcing Storm mode precharge.');
        await hemsService.executeStormMode(data!);
        return;
      }
    }

    switch (smartMode) {
      case 0: // Адаптивний (Прогноз + Піки)
        // Викликаємо метод, передаючи параметри, сумісні з твоєю версією алгоритму
        await hemsService.executeAdaptiveMode(
          data: data!,
          batteryCapacityAh: batteryCapacityAh,
          hourlyForecast: hourlyForecast,
          avgHourlyConsumptionStats: avgHourlyConsumptionStats,
          productionCoefficient: 0.85,
          useAstronomicalWindows: useAstronomicalWindows,
          latitude: siteLatitude,
          longitude: siteLongitude,
          manualDayStartHour: manualDayStartHour,
          manualEveningStartHour: manualEveningStartHour,
          manualNightStartHour: manualNightStartHour,
        );
        break;
      case 1: // Нічний арбітраж
        await hemsService.executeNightArbitrage(data!);
        break;
      case 2: // Шторм / Резерв
        await hemsService.executeStormMode(data!);
        break;
    }
  }
}

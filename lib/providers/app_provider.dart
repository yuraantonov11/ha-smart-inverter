import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:system_tray/system_tray.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import 'package:window_manager/window_manager.dart'; // Використовуємо window_manager для трея
import '../l10n/app_localizations.dart';
import '../services/inverter_service.dart';
import '../services/hems_algorithm.dart';
import '../services/weather_service.dart';
import '../models/inverter_data.dart';

class AppStateProvider extends ChangeNotifier {
  static const String appVersion = '1.0.0';
  final InverterService service = InverterService();
  final WeatherService weatherService = WeatherService();
  late HemsAlgorithmService hemsService;
  final SystemTray systemTray = SystemTray();

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
  String? savedEmail;
  String? userName;

  Map<String, dynamic>? userData;

  bool isAuthenticated = false;
  bool isCheckingAuth = true;

  bool isDeveloperMode = false;
  int _versionClickCount = 0;

  bool _isSettingChanging = false;

  bool get isSettingChanging => _isSettingChanging;

  AppStateProvider() {
    hemsService = HemsAlgorithmService(this);
  }

  Future<AppLocalizations> _getL10n() async {
    return await AppLocalizations.delegate.load(Locale(lang));
  }

  Future<void> fetchProfile() async {
    if (service.userId == null) return;

    final data = await service.getUserInfo(service.userId!);
    if (data.isNotEmpty) {
      userData = data;
      notifyListeners();
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

    solcastApiKey = prefs.getString('solcast_api_key') ??
        '2VEPTJd53ZSHJyNF3ZcY4o2kusVLi52N';
    solcastResourceId =
        prefs.getString('solcast_resource_id') ?? '12e1-6b6b-cf67-00bd';

    smartMode = prefs.getInt('smart_mode') ?? 0;
    lang = prefs.getString('app_lang') ?? 'en';
    isAutostartEnabled = await launchAtStartup.isEnabled();
    final l10n = await _getL10n();
    userName = prefs.getString('user_name') ?? l10n.userNameDefault;
    savedEmail = prefs.getString('saved_email');

    // Перевірка автологіну при старті
    var loggedIn = await autoLogin();
    isAuthenticated = loggedIn;
    isCheckingAuth = false;

    if (loggedIn) {
      startTimers();
    }

    await _updateWeatherForecast();
    await _updateStatusMessage(true);
    notifyListeners();
  }

  Future<void> saveSolcastSettings(String apiKey, String resourceId) async {
    final prefs = await SharedPreferences.getInstance();

    solcastApiKey = apiKey;
    solcastResourceId = resourceId;

    await prefs.setString('solcast_api_key', apiKey);
    await prefs.setString('solcast_resource_id', resourceId);

    // Після введення нових ключів, видаляємо кеш, щоб примусово завантажити новий прогноз
    final todayStr = DateTime.now().toIso8601String().substring(0, 10);
    await prefs.remove('solcast_cache_$todayStr');

    notifyListeners();
    await _updateWeatherForecast();
  }

  Future<void> saveHardwareSettings(
      double battery, double pv, double inverter) async {
    final prefs = await SharedPreferences.getInstance();
    batteryCapacityAh = battery;
    pvTotalCapacityW = pv;
    inverterMaxPowerW = inverter;
    await prefs.setDouble('battery_capacity_ah', battery);
    await prefs.setDouble('pv_total_capacity_w', pv);
    await prefs.setDouble('inverter_max_power_w', inverter);
    notifyListeners();
    await _updateWeatherForecast();
  }

  Future<void> _updateWeatherForecast() async {
    // ТЕПЕР МИ ВИКЛИКАЄМО НАШ НОВИЙ АПІ, ПЕРЕДАЮЧИ ПОТУЖНІСТЬ ПАНЕЛЕЙ (pvTotalCapacityW)
    final dynamicForecastMap = await weatherService.fetchLocalForecast(
      pvCapacityW:
          pvTotalCapacityW, // Беремо значення з налаштувань вашого додатка
      efficiency: 0.85, // ККД системи (85%)
    );

    hourlyForecast = dynamicForecastMap;
    notifyListeners();
  }

  // --- ОНОВЛЕННЯ СТАТИСТИКИ СІМ'Ї ---
  Future<void> _updateConsumptionStats() async {
    if (service.currentStationId == null) return;

    try {
      // Оскільки HemsDataMiner не використовується в поточній версії hems_algorithm,
      // задаємо базову статистику родини.
      avgHourlyConsumptionStats = {
        0: 250, 1: 200, 2: 200, 3: 200, 4: 200, 5: 250, 6: 500, // Ніч
        7: 1500, 8: 1200, 9: 600, 10: 500, 11: 500, 12: 500, // Ранок-Обід
        13: 500, 14: 600, 15: 800, 16: 900, 17: 1500, 18: 2000, // День-Вечір
        19: 3000, 20: 2500, 21: 2000, 22: 1000, 23: 500 // Пік
      };
      notifyListeners();
    } catch (e) {
      debugPrint('Помилка завантаження статистики: $e');
    }
  }

  void _recordPvHistory(InverterData currentData) {
    final now = DateTime.now();
    final timeKey =
        "${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}T${now.hour.toString().padLeft(2, '0')}:00";

    historicalPvData[timeKey] = currentData.pvPower;
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
    _initTray();
    fetchData();
    _dataTimer = Timer.periodic(const Duration(minutes: 1), (_) => fetchData());
    _automationTimer =
        Timer.periodic(const Duration(minutes: 1), (_) => _checkAutomations());
    _weatherTimer = Timer.periodic(
        const Duration(hours: 1), (_) => _updateWeatherForecast());
  }

  void stopTimers() {
    _dataTimer?.cancel();
    _automationTimer?.cancel();
    _weatherTimer?.cancel();
    systemTray.destroy();
  }

  Future<void> setLanguage(String newLang) async {
    lang = newLang;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_lang', lang);
    await _updateStatusMessage(true);
    if (isAuthenticated) await _updateTrayMenu();
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
    if (val) {
      await launchAtStartup.enable();
    } else {
      await launchAtStartup.disable();
    }
    isAutostartEnabled = await launchAtStartup.isEnabled();
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
    final pass = prefs.getString('saved_pass');
    if (email != null && pass != null) return await service.login(email, pass);
    return false;
  }

  Future<bool> login(String email, String pass) async {
    var success = await service.login(email, pass);
    if (success) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('saved_email', email);
      await prefs.setString('saved_pass', pass);
      savedEmail = email;

      isAuthenticated = true;
      startTimers();
      notifyListeners();
    }
    return success;
  }

  Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('saved_email');
    await prefs.remove('saved_pass');
    savedEmail = null;
    service.accessToken = null;
    service.userId = null;
    data = null;

    stopTimers();
    isAuthenticated = false;
    notifyListeners();
  }

  Future<void> _initTray() async {
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
    if (isDataLoading || service.deviceSn == null) return;

    isDataLoading = true;
    notifyListeners();

    final newData = await service.getRealTimeData();

    if (newData != null) {
      data = newData;
      _recordPvHistory(newData);
      await _updateStatusMessage(true);
      if (isAuthenticated) await _updateTrayMenu();

      // Додано await для уникнення помилки unawaited_futures
      if (avgHourlyConsumptionStats.isEmpty &&
          service.currentStationId != null) {
        await _updateConsumptionStats();
      }
    } else {
      await _updateStatusMessage(false);
    }

    isDataLoading = false;
    notifyListeners();

    if (newData != null) {
      await _fetchConfigsInBackground(newData);
    }
  }

  Future<void> _fetchConfigsInBackground(InverterData currentData) async {
    await Future.delayed(const Duration(seconds: 2));

    final fullConfigs = await service.getDeviceFullConfigs();
    if (fullConfigs != null) {
      currentData.rawFields['fullConfigs'] = fullConfigs;
      notifyListeners();
    }
  }

  Future<void> changeInverterSetting(String key, String value) async {
    _isSettingChanging = true;
    notifyListeners();

    final success = await service.updateSetting(key, value);

    if (success) {
      if (data?.rawFields['fullConfigs'] != null) {
        data!.rawFields['fullConfigs'][key] = value;
      }
      await Future.delayed(const Duration(seconds: 5));
    }

    _isSettingChanging = false;
    notifyListeners();

    Future.delayed(const Duration(seconds: 65), () => fetchData());
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

    if (!isManualTrigger) {
      await hemsService.enforceAcousticComfort(data!);
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

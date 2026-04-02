import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:system_tray/system_tray.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import 'package:window_manager/window_manager.dart'; // Використовуємо window_manager для трея
import '../services/inverter_service.dart';
import '../services/hems_algorithm.dart';
import '../models/inverter_data.dart';

class AppStateProvider extends ChangeNotifier {
  static const String appVersion = '1.0.0';
  final InverterService service = InverterService();
  late HemsAlgorithmService hemsService;
  final SystemTray systemTray = SystemTray();

  InverterData? data;
  bool isDataLoading = false;
  String statusMessage = '';

  Timer? _dataTimer;
  Timer? _automationTimer;

  // 0 = Адаптивний (Auto), 1 = Нічний арбітраж, 2 = Шторм/Резерв
  int smartMode = 0;

  ThemeMode themeMode = ThemeMode.dark;
  String lang = 'en';

  bool get isEn => lang == 'en';
  bool isAutostartEnabled = false;
  String? savedEmail;

  // Змінні для правильного реактивного роутингу (Логін <-> Дашборд)
  bool isAuthenticated = false;
  bool isCheckingAuth = true;

  AppStateProvider() {
    // Ініціалізуємо сервіс алгоритмів, передаючи посилання на провайдер
    // для доступу до методу changeSetting та оптимістичного оновлення UI
    hemsService = HemsAlgorithmService(this);
  }

  Future<void> loadSettings() async {
    final prefs = await SharedPreferences.getInstance();

    final isDark = prefs.getBool('is_dark_theme') ?? true;
    themeMode = isDark ? ThemeMode.dark : ThemeMode.light;

    smartMode = prefs.getInt('smart_mode') ?? 0;
    lang = prefs.getString('app_lang') ?? 'en';
    isAutostartEnabled = await launchAtStartup.isEnabled();
    savedEmail = prefs.getString('saved_email');

    // Перевірка автологіну при старті
    var loggedIn = await autoLogin();
    isAuthenticated = loggedIn;
    isCheckingAuth = false;

    if (loggedIn) {
      startTimers();
    }

    _updateStatusMessage(true);
    notifyListeners();
  }

  void startTimers() {
    _initTray();
    fetchData();
    _dataTimer =
        Timer.periodic(const Duration(seconds: 15), (_) => fetchData());
    _automationTimer =
        Timer.periodic(const Duration(minutes: 1), (_) => _checkAutomations());
  }

  void stopTimers() {
    _dataTimer?.cancel();
    _automationTimer?.cancel();
    systemTray.destroy();
  }

  Future<void> setLanguage(String newLang) async {
    lang = newLang;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_lang', lang);
    _updateStatusMessage(true);
    if (isAuthenticated) await _updateTrayMenu();
    notifyListeners();
  }

  void _updateStatusMessage(bool isSuccess) {
    if (isSuccess) {
      statusMessage = isEn
          ? 'Updated at ${DateTime.now().toString().substring(11, 19)}'
          : 'Оновлено о ${DateTime.now().toString().substring(11, 19)}';
    } else {
      statusMessage = isEn ? 'Update failed' : 'Помилка оновлення';
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
    await _checkAutomations(); // Одразу перевіряємо правила після зміни режиму
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
    final menu = Menu();
    await menu.buildFrom([
      MenuItemLabel(
          label: isEn ? 'Enable SOLAR (SBU)' : 'Увімкнути СОНЦЕ (SBU)',
          onClicked: (_) => setMode(2)),
      MenuItemLabel(
          label: isEn ? 'Enable GRID (USB)' : 'Увімкнути МЕРЕЖУ (USB)',
          onClicked: (_) => setMode(0)),
      MenuSeparator(),
      MenuItemCheckbox(
        label: isEn ? 'Start with Windows' : 'Автозапуск з Windows',
        checked: isAutostartEnabled,
        onClicked: (item) async => await toggleAutostart(!isAutostartEnabled),
      ),
      MenuSeparator(),
      MenuItemLabel(
          label: isEn ? 'Show App' : 'Показати вікно',
          onClicked: (_) async {
            await windowManager.show();
            await windowManager.focus();
          }),
      MenuItemLabel(
          label: isEn ? 'Exit' : 'Вийти',
          onClicked: (_) async {
            stopTimers();
            await windowManager.destroy();
            exit(0);
          }),
    ]);
    await systemTray.setContextMenu(menu);
    await systemTray.setTitle(
        '${isEn ? 'Battery' : 'АКБ'}: ${data?.batterySoc.toStringAsFixed(0) ?? '--'}%');
  }

  Future<void> fetchData() async {
    if (service.deviceSn == null) return;

    isDataLoading = true;
    notifyListeners();

    final newData = await service.getRealTimeData();
    if (newData != null) {
      data = newData;
      _updateStatusMessage(true);
      if (isAuthenticated) await _updateTrayMenu();
    } else {
      _updateStatusMessage(false);
    }

    isDataLoading = false;
    notifyListeners();
  }

  Future<void> changeSetting(String key, String value) async {
    // 1. Копіюємо старий стан для відкату
    final oldFields = data?.rawFields != null
        ? Map<String, dynamic>.from(data!.rawFields)
        : null;

    // 2. ОПТИМІСТИЧНЕ ОНОВЛЕННЯ: миттєво міняємо колір кнопок в UI
    final localKey = key.replaceAll('Setting', '');
    if (data != null && data!.rawFields.containsKey(localKey)) {
      data!.rawFields[localKey]['value'] = value;
      notifyListeners();
    }

    // 3. Запит до сервера
    var success = await service.setConfigItem(key, value);

    if (success) {
      statusMessage = isEn ? 'Updated!' : 'Оновлено!';
      await fetchData(); // Підтверджуємо дані з сервера
    } else {
      // 4. В разі помилки повертаємо як було
      if (oldFields != null && data != null) {
        data!.rawFields = oldFields;
      }
      statusMessage = isEn ? 'Failed to update' : 'Помилка оновлення';
      notifyListeners();
    }
  }

  Future<void> setMode(int mode) async {
    await changeSetting('outputSourcePrioritySetting', mode.toString());
  }

  Future<void> _checkAutomations() async {
    if (data == null) return;

    // Вкажіть реальну ємність вашої збірки, наприклад 230Ah
    const myBatteryCapacityAh = 230.0;

    await hemsService.enforceAcousticComfort(data!);

    switch (smartMode) {
      case 0: // Предиктивний Адаптивний інтелект
        await hemsService.executeAdaptiveMode(data!, myBatteryCapacityAh);
        break;
      case 1: // Нічний арбітраж
        await hemsService.executeNightArbitrage(data!, myBatteryCapacityAh);
        await hemsService.executeSmartLoadShedding(data!);
        break;
      case 2: // Резерв / Шторм
        await hemsService.executeStormMode(data!);
        break;
    }
  }
}

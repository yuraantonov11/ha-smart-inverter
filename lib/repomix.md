This file is a merged representation of the entire codebase, combined into a single document by Repomix.

# File Summary

## Purpose
This file contains a packed representation of the entire repository's contents.
It is designed to be easily consumable by AI systems for analysis, code review,
or other automated processes.

## File Format
The content is organized as follows:
1. This summary section
2. Repository information
3. Directory structure
4. Repository files (if enabled)
5. Multiple file entries, each consisting of:
  a. A header with the file path (## File: path/to/file)
  b. The full contents of the file in a code block

## Usage Guidelines
- This file should be treated as read-only. Any changes should be made to the
  original repository files, not this packed version.
- When processing this file, use the file path to distinguish
  between different files in the repository.
- Be aware that this file may contain sensitive information. Handle it with
  the same level of security as you would the original repository.

## Notes
- Some files may have been excluded based on .gitignore rules and Repomix's configuration
- Binary files are not included in this packed representation. Please refer to the Repository Structure section for a complete list of file paths, including binary files
- Files matching patterns in .gitignore are excluded
- Files matching default ignore patterns are excluded
- Files are sorted by Git change count (files with more changes are at the bottom)

# Directory Structure
```
l10n/app_en.arb
l10n/app_localizations_en.dart
l10n/app_localizations_uk.dart
l10n/app_localizations.dart
l10n/app_uk.arb
main.dart
models/inverter_data.dart
providers/app_provider.dart
screens/auth_screen.dart
screens/automation_tab.dart
screens/dashboard_tab.dart
screens/details_tab.dart
screens/main_screen.dart
screens/settings_tab.dart
services/inverter_service.dart
theme/app_theme.dart
widgets/control_panel.dart
widgets/energy_flow.dart
```

# Files

## File: l10n/app_en.arb
````
{
  "appTitle": "Smart Inverter",
  "login": "Login",
  "logout": "Logout",
  "settings": "Settings",
  "theme": "Theme",
  "language": "Language",
  "dashboard": "Dashboard",
  "details": "Details",
  "automation": "Automation"
}
````

## File: l10n/app_localizations_en.dart
````dart
// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appTitle => 'Smart Inverter';

  @override
  String get login => 'Login';

  @override
  String get logout => 'Logout';

  @override
  String get settings => 'Settings';

  @override
  String get theme => 'Theme';

  @override
  String get language => 'Language';

  @override
  String get dashboard => 'Dashboard';

  @override
  String get details => 'Details';

  @override
  String get automation => 'Automation';
}
````

## File: l10n/app_localizations_uk.dart
````dart
// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Ukrainian (`uk`).
class AppLocalizationsUk extends AppLocalizations {
  AppLocalizationsUk([String locale = 'uk']) : super(locale);

  @override
  String get appTitle => 'Смарт Інвертор';

  @override
  String get login => 'Увійти';

  @override
  String get logout => 'Вийти';

  @override
  String get settings => 'Налаштування';

  @override
  String get theme => 'Тема';

  @override
  String get language => 'Мова';

  @override
  String get dashboard => 'Панель';

  @override
  String get details => 'Деталі';

  @override
  String get automation => 'Автоматизація';
}
````

## File: l10n/app_localizations.dart
````dart
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_uk.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
      : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations? of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations);
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
    delegate,
    GlobalMaterialLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
  ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('uk')
  ];

  /// No description provided for @appTitle.
  ///
  /// In en, this message translates to:
  /// **'Smart Inverter'**
  String get appTitle;

  /// No description provided for @login.
  ///
  /// In en, this message translates to:
  /// **'Login'**
  String get login;

  /// No description provided for @logout.
  ///
  /// In en, this message translates to:
  /// **'Logout'**
  String get logout;

  /// No description provided for @settings.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get settings;

  /// No description provided for @theme.
  ///
  /// In en, this message translates to:
  /// **'Theme'**
  String get theme;

  /// No description provided for @language.
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get language;

  /// No description provided for @dashboard.
  ///
  /// In en, this message translates to:
  /// **'Dashboard'**
  String get dashboard;

  /// No description provided for @details.
  ///
  /// In en, this message translates to:
  /// **'Details'**
  String get details;

  /// No description provided for @automation.
  ///
  /// In en, this message translates to:
  /// **'Automation'**
  String get automation;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'uk'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'uk':
      return AppLocalizationsUk();
  }

  throw FlutterError(
      'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
      'an issue with the localizations generation tool. Please file an issue '
      'on GitHub with a reproducible sample app and the gen-l10n configuration '
      'that was used.');
}
````

## File: l10n/app_uk.arb
````
{
  "appTitle": "Смарт Інвертор",
  "login": "Увійти",
  "logout": "Вийти",
  "settings": "Налаштування",
  "theme": "Тема",
  "language": "Мова",
  "dashboard": "Панель",
  "details": "Деталі",
  "automation": "Автоматизація"
}
````

## File: main.dart
````dart
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'l10n/app_localizations.dart';
import 'providers/app_provider.dart';
import 'theme/app_theme.dart';
import 'screens/auth_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  launchAtStartup.setup(
    appName: 'SmartInverter',
    appPath: Platform.resolvedExecutable,
  );

  final provider = AppStateProvider();
  await provider.loadSettings();

  runApp(
    MultiProvider(
      providers: [ChangeNotifierProvider.value(value: provider)],
      child: const InverterApp(),
    ),
  );
}

class InverterApp extends StatelessWidget {
  const InverterApp({super.key});

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<AppStateProvider>();

    return MaterialApp(
      title: 'Smart Inverter',
      debugShowCheckedModeBanner: false,
      themeMode: provider.themeMode,
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,

      // Налаштування локалізації
      locale: Locale(provider.lang),
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: const [
        Locale('en', ''),
        Locale('uk', ''),
      ],

      home: const AuthGate(),
    );
  }
}
````

## File: models/inverter_data.dart
````dart
class InverterData {
  final double pvPower;
  final double gridPower;
  final double batteryPower;
  final double loadPower;
  final double batterySoc;

  // Додаткові детальні дані
  final double pvVoltage;
  final double gridVoltage;
  final double batteryVoltage;
  final double loadPercentage;
  final String workingMode;

  final String deviceSn;
  final String currentModeStr;

  // Зберігаємо абсолютно всі поля для вкладки "Деталі"
  final Map<String, dynamic> rawFields;

  InverterData({
    required this.pvPower,
    required this.gridPower,
    required this.batteryPower,
    required this.loadPower,
    required this.batterySoc,
    required this.pvVoltage,
    required this.gridVoltage,
    required this.batteryVoltage,
    required this.loadPercentage,
    required this.workingMode,
    required this.deviceSn,
    required this.currentModeStr,
    required this.rawFields,
  });

  static double _parseDouble(dynamic fieldObject, {bool isKw = false}) {
    if (fieldObject == null) return 0.0;
    double val = 0.0;
    if (fieldObject is num) {
      val = fieldObject.toDouble();
    } else if (fieldObject is String) {
      val = double.tryParse(fieldObject) ?? 0.0;
    } else if (fieldObject is Map) {
      final rawValue = fieldObject['value'] ?? fieldObject['valueDisplay'] ?? 0.0;
      if (rawValue is num) val = rawValue.toDouble();
      if (rawValue is String) val = double.tryParse(rawValue) ?? 0.0;
    }
    return isKw ? val * 1000 : val;
  }

  static String _parseString(dynamic fieldObject) {
    if (fieldObject == null) return "N/A";
    if (fieldObject is String) return fieldObject;
    if (fieldObject is Map) {
      return fieldObject['valueDisplay']?.toString() ?? fieldObject['value']?.toString() ?? "N/A";
    }
    return fieldObject.toString();
  }

  factory InverterData.fromJson(Map<String, dynamic> json, String deviceSn, String currentModeStr) {
    final fields = json['deviceAttributeState']?['fields'] ?? {};

    double pv = _parseDouble(fields['pvInputPower'] ?? fields['generationPower']);
    double load = _parseDouble(fields['acOutputActivePower'], isKw: true);
    double soc = _parseDouble(fields['batteryCapacity']);

    double gridVolt = _parseDouble(fields['acInputVoltage']);
    double loadPct = _parseDouble(fields['loadPercentage']);
    double batVolt = _parseDouble(fields['batteryVoltage']);
    double pvVolt = _parseDouble(fields['pvInputVoltage']);

    // Розрахунок мережі (споживання або віддача)
    double grid = gridVolt > 0 ? (load - pv > 0 ? load - pv : 0.0) : 0.0;

    // Струм батареї (заряд або розряд)
    double batCharge = _parseDouble(fields['batteryChargingCurrent']);
    double batDischarge = _parseDouble(fields['batteryDischargeCurrent']);
    double batPower = (batCharge > 0) ? batCharge * batVolt : (batDischarge > 0 ? -batDischarge * batVolt : 0.0);

    return InverterData(
      pvPower: pv,
      gridPower: grid,
      batteryPower: batPower,
      loadPower: load,
      batterySoc: soc,
      pvVoltage: pvVolt,
      gridVoltage: gridVolt,
      batteryVoltage: batVolt,
      loadPercentage: loadPct,
      workingMode: _parseString(fields['workingStates']),
      deviceSn: deviceSn,
      currentModeStr: currentModeStr,
      rawFields: fields, // Зберігаємо всі дані
    );
  }
}
````

## File: providers/app_provider.dart
````dart
import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:system_tray/system_tray.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import '../services/inverter_service.dart';
import '../models/inverter_data.dart';

class AppStateProvider extends ChangeNotifier {
  final InverterService service = InverterService();
  final SystemTray systemTray = SystemTray();
  final AppWindow appWindow = AppWindow();

  InverterData? data;
  bool isDataLoading = false;
  String statusMessage = "";

  Timer? _dataTimer;
  Timer? _automationTimer;

  int smartMode = 0; // 0 = Off, 1 = Winter, 2 = Summer
  bool isAutostartEnabled = false;
  String? savedEmail;

  bool isScheduleEnabled = false;
  bool isWeatherEnabled = false;

  ThemeMode themeMode = ThemeMode.dark;
  String lang = 'en';
  bool get isEn => lang == 'en';

  Future<void> loadSettings() async {
    final prefs = await SharedPreferences.getInstance();

    final isDark = prefs.getBool('is_dark_theme') ?? true;
    themeMode = isDark ? ThemeMode.dark : ThemeMode.light;

    isScheduleEnabled = prefs.getBool('is_schedule') ?? false;
    isWeatherEnabled = prefs.getBool('is_weather') ?? false;

    lang = prefs.getString('app_lang') ?? 'en';

    isAutostartEnabled = await launchAtStartup.isEnabled();
    savedEmail = prefs.getString('saved_email');

    _updateStatusMessage(true);
    notifyListeners();
  }

  Future<void> setLanguage(String newLang) async {
    lang = newLang;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('app_lang', lang);
    _updateStatusMessage(true);
    await _updateTrayMenu();
    notifyListeners();
  }

  void _updateStatusMessage(bool isSuccess) {
    if (isSuccess) {
      statusMessage = isEn ? "Updated at ${DateTime.now().toString().substring(11, 19)}" : "Оновлено о ${DateTime.now().toString().substring(11, 19)}";
    } else {
      statusMessage = isEn ? "Update failed" : "Помилка оновлення";
    }
  }

  Future<void> toggleTheme() async {
    themeMode = themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_dark_theme', themeMode == ThemeMode.dark);
    notifyListeners();
  }

  Future<void> toggleSchedule(bool val) async {
    isScheduleEnabled = val;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_schedule', val);
    notifyListeners();
  }

  Future<void> toggleWeather(bool val) async {
    isWeatherEnabled = val;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_weather', val);
    notifyListeners();
  }

  Future<void> toggleAutostart(bool val) async {
    if (val) {
      await launchAtStartup.enable();
    } else {
      await launchAtStartup.disable();
    }
    isAutostartEnabled = await launchAtStartup.isEnabled();
    await _updateTrayMenu();
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
    bool success = await service.login(email, pass);
    if (success) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('saved_email', email);
      await prefs.setString('saved_pass', pass);
      savedEmail = email;
    }
    return success;
  }

  void startTimers() {
    _initTray();
    fetchData();
    _dataTimer = Timer.periodic(const Duration(seconds: 15), (_) => fetchData());
    _automationTimer = Timer.periodic(const Duration(minutes: 1), (_) => _checkAutomations());
  }

  void stopTimers() {
    _dataTimer?.cancel();
    _automationTimer?.cancel();
    systemTray.destroy();
  }

  Future<void> fetchData() async {
    if (service.deviceSn == null) return;
    isDataLoading = true;
    notifyListeners();

    final newData = await service.getRealTimeData();
    if (newData != null) {
      data = newData;
      _updateStatusMessage(true);
      _updateTrayMenu();
    } else {
      _updateStatusMessage(false);
    }
    isDataLoading = false;
    notifyListeners();
  }

  Future<void> setMode(int mode) async {
    bool success = await service.setMode(mode);
    if (success) {
      await fetchData();
    } else {
      statusMessage = isEn ? "Mode change failed" : "Помилка зміни режиму";
      notifyListeners();
    }
  }

  Future<void> changeSetting(String key, String value) async {
    bool success = await service.setConfigItem(key, value);
    if (success) {
      statusMessage = isEn ? "Settings updated!" : "Налаштування успішно змінено!";
      await fetchData();
    } else {
      statusMessage = isEn ? "Settings update failed" : "Не вдалося змінити налаштування";
      notifyListeners();
    }
  }

  void _checkAutomations() {
    if (smartMode == 0 || data == null) return;

    final now = DateTime.now();
    final isNight = now.hour >= 23 || now.hour < 7;

    // Зчитуємо поточні налаштування з інвертора, щоб не спамити API
    final currentOutput = data!.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger = data!.rawFields['chargerSourcePriority']?['value']?.toString();

    if (smartMode == 1) {
      // --- ЗИМОВИЙ СЦЕНАРІЙ ---
      if (isNight) {
        // Ніч: Мережа (0), Зарядка від мережі (SNU = 1)
        if (currentOutput != "0") setMode(0);
        if (currentCharger != "1") changeSetting("chargerSourcePrioritySetting", "1");
      } else {
        // День/Вечір: Сонце/АКБ (SBU = 2), Зарядка пріоритет сонце (CSO = 0)
        if (currentOutput != "2") setMode(2);
        if (currentCharger != "0") changeSetting("chargerSourcePrioritySetting", "0");
      }
    } else if (smartMode == 2) {
      // --- ЛІТНІЙ СЦЕНАРІЙ ---
      if (isNight) {
        // Ніч: Мережа (0), Зарядка ТІЛЬКИ сонце (щоб не брати з мережі) (OSO = 2)
        if (currentOutput != "0") setMode(0);
        if (currentCharger != "2") changeSetting("chargerSourcePrioritySetting", "2");
      } else {
        // День: Сонце/АКБ (SBU = 2), Зарядка ТІЛЬКИ сонце (OSO = 2)
        if (currentOutput != "2") setMode(2);
        if (currentCharger != "2") changeSetting("chargerSourcePrioritySetting", "2");
      }
    }
  }

  Future<void> _initTray() async {
    await systemTray.initSystemTray(
      title: "Inverter",
      iconPath: Platform.isWindows ? 'assets/app_icon.ico' : 'assets/app_icon.png',
    );
    systemTray.registerSystemTrayEventHandler((eventName) {
      if (eventName == kSystemTrayEventClick) Platform.isWindows ? appWindow.show() : systemTray.popUpContextMenu();
      else if (eventName == kSystemTrayEventRightClick) systemTray.popUpContextMenu();
    });
  }

  Future<void> _updateTrayMenu() async {
    final menu = Menu();
    await menu.buildFrom([
      MenuItemLabel(label: isEn ? 'Enable SOLAR (SBU)' : 'Увімкнути СОНЦЕ (SBU)', onClicked: (_) => setMode(2)),
      MenuItemLabel(label: isEn ? 'Enable GRID (USB)' : 'Увімкнути МЕРЕЖУ (USB)', onClicked: (_) => setMode(0)),
      MenuSeparator(),
      MenuItemCheckbox(
        label: isEn ? 'Start with Windows' : 'Автозапуск з Windows',
        checked: isAutostartEnabled,
        onClicked: (item) async => await toggleAutostart(!isAutostartEnabled),
      ),
      MenuSeparator(),
      MenuItemLabel(label: isEn ? 'Show App' : 'Показати вікно', onClicked: (_) => appWindow.show()),
      MenuItemLabel(label: isEn ? 'Exit' : 'Вийти', onClicked: (_) => exit(0)),
    ]);
    await systemTray.setContextMenu(menu);
    await systemTray.setTitle("${isEn ? 'Battery' : 'Заряд'}: ${data?.batterySoc.toStringAsFixed(0) ?? '--'}%");
  }

  Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('saved_email');
    await prefs.remove('saved_pass');
    savedEmail = null;
    stopTimers();
    service.accessToken = null;
    notifyListeners();
  }
}
````

## File: screens/auth_screen.dart
````dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import 'main_screen.dart';

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});
  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  @override
  void initState() {
    super.initState();
    context.read<AppStateProvider>().autoLogin().then((success) {
      if (!mounted) return;
      if (success) {
        Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const MainScreen()));
      } else {
        Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const LoginScreen()));
      }
    });
  }
  @override
  Widget build(BuildContext context) => const Scaffold(body: Center(child: CircularProgressIndicator(color: Colors.amber)));
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailController = TextEditingController();
  final _passController = TextEditingController();
  bool _isLoading = false;

  void _doLogin() async {
    setState(() => _isLoading = true);
    final success = await context.read<AppStateProvider>().login(_emailController.text.trim(), _passController.text);
    if (!mounted) return;
    setState(() => _isLoading = false);
    if (success) {
      Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const MainScreen()));
    } else {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Помилка авторизації.")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Container(
          width: 380,
          padding: const EdgeInsets.all(32.0),
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
            boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.5), blurRadius: 20, offset: const Offset(0, 10))],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.solar_power_rounded, size: 80, color: Colors.amber),
              const SizedBox(height: 20),
              const Text("Smart Inverter", style: TextStyle(fontSize: 26, fontWeight: FontWeight.bold)),
              const SizedBox(height: 30),
              TextField(controller: _emailController, decoration: InputDecoration(labelText: "Email", prefixIcon: const Icon(Icons.email_outlined), border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)))),
              const SizedBox(height: 16),
              TextField(controller: _passController, obscureText: true, decoration: InputDecoration(labelText: "Пароль", prefixIcon: const Icon(Icons.lock_outline), border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)))),
              const SizedBox(height: 30),
              SizedBox(
                width: double.infinity,
                height: 50,
                child: FilledButton(
                  onPressed: _isLoading ? null : _doLogin,
                  style: FilledButton.styleFrom(backgroundColor: Colors.amber, foregroundColor: Colors.black, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12))),
                  child: _isLoading ? const CircularProgressIndicator(color: Colors.black) : const Text("УВІЙТИ", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
````

## File: screens/automation_tab.dart
````dart
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';

class AutomationTab extends StatelessWidget {
  final AppStateProvider provider;

  const AutomationTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 12, left: 4),
          child: Text(l10n.smartModes, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.grey)),
        ),

        _buildRadioCard(
          context,
          title: l10n.modeOff,
          subtitle: "",
          icon: Icons.pan_tool_rounded,
          color: Colors.grey,
          value: 0,
        ),
        const SizedBox(height: 16),
        _buildRadioCard(
          context,
          title: l10n.modeWinter,
          subtitle: l10n.modeWinterDesc,
          icon: Icons.ac_unit_rounded,
          color: Colors.lightBlueAccent,
          value: 1,
        ),
        const SizedBox(height: 16),
        _buildRadioCard(
          context,
          title: l10n.modeSummer,
          subtitle: l10n.modeSummerDesc,
          icon: Icons.wb_sunny_rounded,
          color: Colors.amber,
          value: 2,
        ),
      ],
    );
  }

  Widget _buildRadioCard(BuildContext context, {required String title, required String subtitle, required IconData icon, required Color color, required int value}) {
    final isSelected = provider.smartMode == value;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return GestureDetector(
      onTap: () => provider.setSmartMode(value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          color: isSelected ? color.withValues(alpha: 0.1) : Theme.of(context).cardColor,
          borderRadius: BorderRadius.circular(24),
          border: Border.all(color: isSelected ? color : Colors.transparent, width: 2),
          boxShadow: isDark ? [] : [BoxShadow(color: Colors.black.withValues(alpha: 0.05), blurRadius: 10)],
        ),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: color.withValues(alpha: 0.15), shape: BoxShape.circle),
                child: Icon(icon, color: color),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 8),
                    Text(title, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: isSelected ? color : null)),
                    if (subtitle.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Text(subtitle, style: const TextStyle(fontSize: 13, height: 1.4, color: Colors.grey)),
                    ]
                  ],
                ),
              ),
              Radio<int>(
                value: value,
                groupValue: provider.smartMode,
                activeColor: color,
                onChanged: (val) => provider.setSmartMode(val!),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
````

## File: screens/dashboard_tab.dart
````dart
import 'package:flutter/material.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../widgets/energy_flow.dart';
import '../widgets/control_panel.dart';

class DashboardTab extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const DashboardTab({super.key, required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      color: Colors.amber,
      onRefresh: provider.fetchData,
      child: ListView(
        padding: const EdgeInsets.all(20.0),
        children: [
          _buildStatusBanner(provider.statusMessage),
          const SizedBox(height: 24),
          EnergyFlowDiagram(data: data),
          const SizedBox(height: 24),
          ControlPanel(provider: provider),
        ],
      ),
    );
  }

  Widget _buildStatusBanner(String message) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
      decoration: BoxDecoration(
        color: Colors.amber.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.sync, color: Colors.amber, size: 20),
          const SizedBox(width: 12),
          Expanded(child: Text(message, style: const TextStyle(color: Colors.amber, fontWeight: FontWeight.w600, fontSize: 13))),
        ],
      ),
    );
  }
}
````

## File: screens/details_tab.dart
````dart
import 'package:flutter/material.dart';

import '../models/inverter_data.dart';

// ВИПРАВЛЕНО: Прибрано "_" щоб зробити клас публічним
class DetailsTab extends StatelessWidget {
  final InverterData data;
  const DetailsTab({super.key, required this.data});

  @override
  Widget build(BuildContext context) {
    final fields = data.rawFields;
    final keys = fields.keys.toList()..sort();

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: keys.length,
      itemBuilder: (context, index) {
        final key = keys[index];
        final fieldData = fields[key];

        String name = key;
        String val = "N/A";
        String unit = "";

        if (fieldData is Map) {
          name = fieldData['nameDisplay'] ?? key;
          val = fieldData['valueDisplay']?.toString() ?? fieldData['value']?.toString() ?? "N/A";
          unit = fieldData['unit']?.toString() ?? "";
        } else {
          val = fieldData.toString();
        }

        return Card(
          color: const Color(0xFF1E293B),
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            title: Text(name, style: const TextStyle(fontSize: 14, color: Colors.white70)),
            trailing: Text("$val $unit".trim(), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.amber)),
          ),
        );
      },
    );
  }
}
````

## File: screens/main_screen.dart
````dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import 'dashboard_tab.dart';
import 'automation_tab.dart';
import 'details_tab.dart';
import 'settings_tab.dart';

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});
  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      if (mounted) context.read<AppStateProvider>().startTimers();
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<AppStateProvider>();
    final data = provider.data;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final isEn = provider.isEn;

    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: const Text("Smart Inverter", style: TextStyle(fontWeight: FontWeight.bold)),
          actions: [
            IconButton(
              icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
              onPressed: provider.toggleTheme,
            ),
            IconButton(icon: const Icon(Icons.refresh), onPressed: provider.fetchData),
          ],
          bottom: TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            indicatorColor: Colors.amber,
            labelColor: Colors.amber,
            tabs: [
              Tab(icon: const Icon(Icons.dashboard_rounded), text: isEn ? "Dashboard" : "Дашборд"),
              Tab(icon: const Icon(Icons.smart_toy_rounded), text: isEn ? "Automation" : "Автоматика"),
              Tab(icon: const Icon(Icons.list_alt_rounded), text: isEn ? "Data" : "Дані"),
              Tab(icon: const Icon(Icons.settings), text: isEn ? "Settings" : "Налаштування"),
            ],
          ),
        ),
        body: data == null
            ? const Center(child: CircularProgressIndicator(color: Colors.amber))
            : TabBarView(
          children: [
            DashboardTab(provider: provider, data: data),
            AutomationTab(provider: provider),
            DetailsTab(data: data),
            SettingsTab(provider: provider),
          ],
        ),
      ),
    );
  }
}
````

## File: screens/settings_tab.dart
````dart
import 'package:flutter/material.dart';
import '../providers/app_provider.dart';

class SettingsTab extends StatelessWidget {
  final AppStateProvider provider;

  const SettingsTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final isEn = provider.isEn;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // Блок Акаунта
        _buildSectionTitle(isEn ? "Account" : "Акаунт"),
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Column(
            children: [
              const CircleAvatar(
                radius: 40,
                backgroundColor: Colors.amber,
                child: Icon(Icons.person, size: 40, color: Colors.black),
              ),
              const SizedBox(height: 16),
              Text(provider.savedEmail ?? "User", style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: () => provider.logout(),
                  icon: const Icon(Icons.logout, color: Colors.redAccent),
                  label: Text(isEn ? "Log Out" : "Вийти з акаунту", style: const TextStyle(color: Colors.redAccent)),
                  style: OutlinedButton.styleFrom(
                    side: const BorderSide(color: Colors.redAccent),
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                ),
              )
            ],
          ),
        ),

        const SizedBox(height: 24),

        // Блок Налаштувань Додатка
        _buildSectionTitle(isEn ? "Application Settings" : "Налаштування застосунку"),
        Container(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Column(
            children: [
              // Вибір мови
              ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                leading: const Icon(Icons.language, color: Colors.blueAccent),
                title: Text(isEn ? "Language" : "Мова"),
                trailing: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: provider.lang,
                    items: const [
                      DropdownMenuItem(value: 'en', child: Text("English")),
                      DropdownMenuItem(value: 'uk', child: Text("Українська")),
                    ],
                    onChanged: (val) {
                      if (val != null) provider.setLanguage(val);
                    },
                  ),
                ),
              ),
              const Divider(height: 1),
              // Автозапуск
              SwitchListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                secondary: const Icon(Icons.power_settings_new, color: Colors.greenAccent),
                title: Text(isEn ? "Start with Windows" : "Автозапуск з Windows"),
                value: provider.isAutostartEnabled,
                activeThumbColor: Colors.greenAccent,
                activeTrackColor: Colors.greenAccent.withValues(alpha: 0.3),
                onChanged: provider.toggleAutostart,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildSectionTitle(String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12, left: 4),
      child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.grey)),
    );
  }
}
````

## File: services/inverter_service.dart
````dart
import 'package:dio/dio.dart';
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/inverter_data.dart';

class InverterService {
  final String _appId = 'rBrTRfAPXz';
  final String _encryptedAppSecret = 'I4D0KRr2339z3pQ/at91V9BpFAOe54DaTafwSm6suIQ=';

  late final Dio _dio;
  String? accessToken;
  String? userId;
  String? deviceSn;
  int? currentMode;

  late final String _appSecret = _decryptAppSecret(_appId, _encryptedAppSecret);

  InverterService() {
    _dio = Dio(BaseOptions(
      baseUrl: "https://solar.siseli.com",
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
    ));

    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        final nonce = _generateNonce(32);
        final bodyHash = _calculateBodyHash(options.method, options.data);
        final sign = _calculateAppSign(
          appId: _appId,
          nonce: nonce,
          bodyHash: bodyHash,
          appSecret: _appSecret,
        );

        options.headers.addAll({
          'IOT-Open-AppID': _appId,
          'IOT-Open-Nonce': nonce,
          'IOT-Open-Body-Hash': bodyHash,
          'IOT-Open-Sign': sign,
          'IOT-Time-Zone': 'Europe/Kyiv',
          'Accept': 'application/json, text/plain, */*',
          'Content-Type': 'application/json; charset=utf-8',
          'Origin': 'https://solar.siseli.com',
          'Referer': 'https://solar.siseli.com/',
          'IOT-Token': (accessToken?.isNotEmpty == true) ? accessToken : 'null',
        });
        return handler.next(options);
      },
    ));
  }

  String _generateNonce(int length) {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    final random = Random.secure();
    return String.fromCharCodes(Iterable.generate(length, (_) => chars.codeUnitAt(random.nextInt(chars.length))));
  }

  String _decryptAppSecret(String appId, String encryptedSecret) {
    final md5AppId = md5.convert(utf8.encode(appId)).toString().toLowerCase();
    final keyHex = md5AppId.substring(0, 16);
    final ivHex = md5AppId.substring(16);
    final key = encrypt.Key.fromUtf8(keyHex);
    final iv = encrypt.IV.fromUtf8(ivHex);
    final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc, padding: null));
    return encrypter.decrypt64(encryptedSecret, iv: iv).replaceAll(RegExp(r'\x00+$'), '').trim();
  }

  String _calculateBodyHash(String method, Object? body) {
    if (method.trim().toUpperCase() == 'GET' || body == null) {
      return sha256.convert(utf8.encode('{}')).toString().toLowerCase();
    }
    return sha256.convert(utf8.encode(body is String ? body : jsonEncode(body))).toString().toLowerCase();
  }

  String _calculateAppSign({required String appId, required String appSecret, required String bodyHash, required String nonce}) {
    final payload = {'IOT-Open-AppID': appId, 'IOT-Open-Body-Hash': bodyHash, 'IOT-Open-Nonce': nonce};
    final sortedKeys = payload.keys.toList()..sort();
    final queryString = sortedKeys.map((k) => '$k=${payload[k]}').join('&');
    final hmac = Hmac(sha256, utf8.encode(appSecret));
    return md5.convert(hmac.convert(utf8.encode(base64.encode(utf8.encode(queryString)))).bytes).toString().toLowerCase();
  }

  Future<bool> login(String email, String password) async {
    try {
      final passwordMd5 = (password.length == 32) ? password.toLowerCase() : md5.convert(utf8.encode(password)).toString().toLowerCase();
      final response = await _dio.post("/apis/login/account", data: {"account": email, "password": passwordMd5});

      if (response.data['code'] == 0 || response.data['success'] == true) {
        final data = response.data['data'];
        accessToken = data['accessToken'] ?? data['token'];
        userId = data['userId']?.toString();
        await _fetchDeviceList();
        return true;
      }
      return false;
    } catch (_) { return false; }
  }

  Future<void> _fetchDeviceList() async {
    if (userId == null) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      deviceSn = prefs.getString('saved_device_sn');

      final response = await _dio.post("/apis/device/list", data: {
        "page": 1, "count": 10, "serialNumber": "", "name": "", "stationId": "", "state": "", "exportType": 0, "applyModeCategory": 1
      });

      if ((response.data['code'] == 0 || response.data['success'] == true) && response.data['data'] != null) {
        final dataInfo = response.data['data'];
        List devices = dataInfo is List ? dataInfo : (dataInfo['list'] ?? dataInfo['records'] ?? dataInfo['data'] ?? []);
        if (devices.isNotEmpty) {
          final firstDevice = devices[0];
          final extractedId = firstDevice['id']?.toString() ?? firstDevice['deviceId']?.toString() ?? firstDevice['deviceSn']?.toString();
          if (extractedId != null && extractedId.isNotEmpty) {
            deviceSn = extractedId;
            await prefs.setString('saved_device_sn', deviceSn!);
          }
        }
      }
    } catch (_) {}
  }

  Future<InverterData?> getRealTimeData() async {
    if (userId == null || deviceSn == null || deviceSn!.isEmpty) return null;
    try {
      final response = await _dio.get("/apis/deviceState/simple/energy/flow/v1", queryParameters: {
        "deviceId": deviceSn,
        "dataSource": 1,
      });

      if ((response.data['code'] == 0 || response.data['success'] == true) && response.data['data'] != null) {
        return InverterData.fromJson(response.data['data'], deviceSn!, currentMode?.toString() ?? "");
      }
    } catch (_) {}
    return null;
  }

  Future<bool> setConfigItem(String key, String value) async {
    if (accessToken == null || deviceSn == null) return false;
    try {
      final response = await _dio.post(
        "/apis/remote/device/config/write",
        queryParameters: {"deviceId": deviceSn},
        data: {"id": deviceSn, "key": key, "value": value},
      );
      return response.data['code'] == 0 || response.data['success'] == true;
    } catch (_) {
      return false;
    }
  }

  Future<bool> setMode(int mode) async {
    bool success = await setConfigItem("outputSourcePrioritySetting", mode.toString());
    if (success) {
      currentMode = mode;
      return true;
    }
    return false;
  }
}
````

## File: theme/app_theme.dart
````dart
import 'package:flutter/material.dart';

class AppTheme {
  static ThemeData get lightTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      colorSchemeSeed: Colors.amber,
      scaffoldBackgroundColor: const Color(0xFFF1F5F9), // Світло-сірий фон
      cardColor: Colors.white,
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFFF1F5F9),
        foregroundColor: Colors.black87,
        elevation: 0,
      ),
    );
  }

  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorSchemeSeed: Colors.amber,
      scaffoldBackgroundColor: const Color(0xFF0F172A), // Глибокий синій
      cardColor: const Color(0xFF1E293B),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFF0F172A),
        foregroundColor: Colors.white,
        elevation: 0,
      ),
    );
  }
}
````

## File: widgets/control_panel.dart
````dart
import 'package:flutter/material.dart';
import '../providers/app_provider.dart';

class ControlPanel extends StatelessWidget {
  final AppStateProvider provider;

  const ControlPanel({super.key, required this.provider});

  void _showSettingsModal(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).cardColor,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
            child: _SettingsModal(provider: provider),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final isEn = provider.isEn;
    final currentOutputPriority = provider.data?.rawFields['outputSourcePriority']?['value']?.toString() ?? "2";

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: isDark ? [] : [BoxShadow(color: Colors.black.withValues(alpha: 0.05), blurRadius: 10)],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(isEn ? 'Inverter Mode' : 'Режим інвертора', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              IconButton(
                icon: const Icon(Icons.settings_outlined, color: Colors.grey),
                tooltip: isEn ? 'Advanced Settings' : 'Розширені налаштування',
                onPressed: () => _showSettingsModal(context),
              )
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: _SwitchCard(
                  title: isEn ? "SOLAR (SBU)" : "СОНЦЕ (SBU)",
                  icon: Icons.wb_sunny_rounded,
                  isActive: currentOutputPriority == "2",
                  activeColor: Colors.amber,
                  onTap: () => provider.setMode(2),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: _SwitchCard(
                  title: isEn ? "GRID (USB)" : "МЕРЕЖА (USB)",
                  icon: Icons.power_rounded,
                  isActive: currentOutputPriority == "0",
                  activeColor: Colors.blueAccent,
                  onTap: () => provider.setMode(0),
                ),
              ),
            ],
          )
        ],
      ),
    );
  }
}

class _SwitchCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final bool isActive;
  final Color activeColor;
  final VoidCallback onTap;

  const _SwitchCard({required this.title, required this.icon, required this.isActive, required this.activeColor, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final baseColor = isDark ? Colors.white : Colors.black;

    return GestureDetector(
      onTap: isActive ? null : onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 300),
        padding: const EdgeInsets.symmetric(vertical: 20),
        decoration: BoxDecoration(
          color: isActive ? activeColor.withValues(alpha: 0.15) : (isDark ? Colors.black26 : Colors.grey.shade100),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: isActive ? activeColor : (isDark ? Colors.white12 : Colors.grey.shade300), width: isActive ? 2 : 1),
        ),
        child: Column(
          children: [
            Icon(icon, color: isActive ? activeColor : baseColor.withValues(alpha: 0.4), size: 36),
            const SizedBox(height: 12),
            Text(title, style: TextStyle(color: isActive ? activeColor : baseColor.withValues(alpha: 0.6), fontWeight: isActive ? FontWeight.bold : FontWeight.w500)),
          ],
        ),
      ),
    );
  }
}

class _SettingsModal extends StatelessWidget {
  final AppStateProvider provider;

  const _SettingsModal({required this.provider});

  @override
  Widget build(BuildContext context) {
    final isEn = provider.isEn;
    final fields = provider.data?.rawFields ?? {};
    final outputPriority = fields['outputSourcePriority']?['value']?.toString() ?? "2";
    final chargerPriority = fields['chargerSourcePriority']?['value']?.toString() ?? "0";

    return Padding(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(isEn ? "Advanced Settings" : "Розширені налаштування", style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          const SizedBox(height: 24),

          Text(isEn ? "Output Source Priority" : "Пріоритет виходу (Output)", style: const TextStyle(fontSize: 14, color: Colors.grey)),
          const SizedBox(height: 8),
          _buildDropdown(
            context,
            value: outputPriority,
            items: [
              DropdownMenuItem(value: "0", child: Text(isEn ? "Utility First (USB)" : "Мережа (Utility First / USB)")),
              DropdownMenuItem(value: "1", child: Text(isEn ? "Solar First (SUB)" : "Сонце (Solar First / SUB)")),
              DropdownMenuItem(value: "2", child: Text("SBU Priority")),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.changeSetting("outputSourcePrioritySetting", val);
                Navigator.pop(context);
              }
            },
          ),

          const SizedBox(height: 24),

          Text(isEn ? "Charger Source Priority" : "Пріоритет зарядки (Charger)", style: const TextStyle(fontSize: 14, color: Colors.grey)),
          const SizedBox(height: 8),
          _buildDropdown(
            context,
            value: chargerPriority,
            items: [
              DropdownMenuItem(value: "0", child: Text(isEn ? "Solar First (CSO)" : "Сонце пріоритет (CSO)")),
              DropdownMenuItem(value: "1", child: Text(isEn ? "Solar + Utility (SNU)" : "Сонце + Мережа (SNU)")),
              DropdownMenuItem(value: "2", child: Text(isEn ? "Only Solar (OSO)" : "Тільки Сонце (OSO)")),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.changeSetting("chargerSourcePrioritySetting", val);
                Navigator.pop(context);
              }
            },
          ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }

  Widget _buildDropdown(BuildContext context, {required String value, required List<DropdownMenuItem<String>> items, required void Function(String?) onChanged}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: Theme.of(context).scaffoldBackgroundColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.grey.withValues(alpha: 0.2)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: items.any((item) => item.value == value) ? value : items.first.value,
          isExpanded: true,
          icon: const Icon(Icons.arrow_drop_down_rounded, color: Colors.amber),
          items: items,
          onChanged: onChanged,
        ),
      ),
    );
  }
}
````

## File: widgets/energy_flow.dart
````dart
import 'package:flutter/material.dart';
import '../models/inverter_data.dart';

class EnergyFlowDiagram extends StatefulWidget {
  final InverterData data;
  const EnergyFlowDiagram({super.key, required this.data});

  @override
  State<EnergyFlowDiagram> createState() => _EnergyFlowDiagramState();
}

class _EnergyFlowDiagramState extends State<EnergyFlowDiagram> with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(seconds: 2))..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 320,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: Theme.of(context).brightness == Brightness.dark
            ? []
            : [BoxShadow(color: Colors.black.withValues(alpha: 0.05), blurRadius: 15, offset: const Offset(0, 5))],
      ),
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) {
          return CustomPaint(
            painter: _FlowPainter(animationValue: _controller.value, data: widget.data),
            child: _buildNodes(widget.data, Theme.of(context).scaffoldBackgroundColor),
          );
        },
      ),
    );
  }

  Widget _buildNodes(InverterData data, Color centerColor) {
    return Stack(
      children: [
        Align(alignment: Alignment.topLeft, child: _NodeWidget(icon: Icons.solar_power, color: Colors.amber, title: "Сонце", value: "${data.pvPower.toStringAsFixed(0)} W")),
        Align(alignment: Alignment.topRight, child: _NodeWidget(icon: Icons.electric_bolt, color: Colors.blueAccent, title: "Мережа", value: "${data.gridVoltage.toStringAsFixed(1)} V")),
        Align(alignment: Alignment.bottomLeft, child: _NodeWidget(icon: Icons.battery_charging_full, color: Colors.greenAccent, title: "АКБ", value: "${data.batterySoc.toStringAsFixed(0)}%")),
        Align(alignment: Alignment.bottomRight, child: _NodeWidget(icon: Icons.home_rounded, color: Colors.purpleAccent, title: "Будинок", value: "${data.loadPower.toStringAsFixed(0)} W")),
        Align(
          alignment: Alignment.center,
          child: Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: centerColor,
              border: Border.all(color: Colors.grey.withValues(alpha: 0.2), width: 2),
            ),
            child: const Icon(Icons.sync_alt, size: 40, color: Colors.grey),
          ),
        ),
      ],
    );
  }
}

class _NodeWidget extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String title;
  final String value;

  const _NodeWidget({required this.icon, required this.color, required this.title, required this.value});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 90,
      height: 100,
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: color, size: 28),
          const SizedBox(height: 6),
          Text(title, style: const TextStyle(fontSize: 11, color: Colors.grey)),
          Text(value, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: color)),
        ],
      ),
    );
  }
}

class _FlowPainter extends CustomPainter {
  final double animationValue;
  final InverterData data;

  _FlowPainter({required this.animationValue, required this.data});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final pvPos = const Offset(45, 50);
    final gridPos = Offset(size.width - 45, 50);
    final batPos = Offset(45, size.height - 50);
    final loadPos = Offset(size.width - 45, size.height - 50);

    final linePaint = Paint()..color = Colors.grey.withValues(alpha: 0.1)..strokeWidth = 3..style = PaintingStyle.stroke;
    canvas.drawLine(pvPos, center, linePaint);
    canvas.drawLine(gridPos, center, linePaint);
    canvas.drawLine(batPos, center, linePaint);
    canvas.drawLine(center, loadPos, linePaint);

    if (data.pvPower > 0) _drawParticles(canvas, pvPos, center, Colors.amber);
    if (data.gridVoltage > 0) _drawParticles(canvas, gridPos, center, Colors.blueAccent);
    if (data.loadPower > 0) _drawParticles(canvas, center, loadPos, Colors.purpleAccent);

    if (data.batteryPower > 0) {
      _drawParticles(canvas, center, batPos, Colors.greenAccent);
    } else if (data.batteryPower < 0) {
      _drawParticles(canvas, batPos, center, Colors.greenAccent);
    }
  }

  void _drawParticles(Canvas canvas, Offset start, Offset end, Color color) {
    final particlePaint = Paint()..color = color..style = PaintingStyle.fill..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3);
    for (int i = 0; i < 3; i++) {
      double progress = (animationValue + (i * 0.33)) % 1.0;
      final x = start.dx + (end.dx - start.dx) * progress;
      final y = start.dy + (end.dy - start.dy) * progress;
      canvas.drawCircle(Offset(x, y), 4, particlePaint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}
````

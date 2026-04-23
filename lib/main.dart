import 'dart:io';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:launch_at_startup/launch_at_startup.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'l10n/app_localizations.dart';
import 'theme/app_theme.dart';
import 'providers/app_provider.dart';
import 'screens/auth_screen.dart';
import 'screens/main_screen.dart';
import 'services/log_service.dart';
import 'services/secure_storage_service.dart';

void main() async {
  FlutterError.onError = (details) {
    LogService.log('FLUTTER ERROR',
        error: details.exception, stack: details.stack);
    FlutterError.presentError(details);
  };

  // Перехоплення асинхронних помилок поза Flutter
  PlatformDispatcher.instance.onError = (error, stack) {
    LogService.log('PLATFORM ERROR', error: error, stack: stack);
    return true;
  };

  WidgetsFlutterBinding.ensureInitialized();

  LogService.log('Додаток запускається...');

  // 1. Ініціалізація автозапуску (тепер безпечно)
  try {
    var packageInfo = await PackageInfo.fromPlatform();
    launchAtStartup.setup(
      appName: packageInfo.appName,
      appPath: Platform.resolvedExecutable,
    );
  } catch (e) {
    debugPrint('Помилка ініціалізації автозапуску: $e');
  }

  // 2. Ініціалізація керування вікном
  await windowManager.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  final startInTray = prefs.getBool('start_in_tray') ?? false;
  final savedPass = await SecureStorageService.getPassword();
  final hasSavedSession =
      (prefs.getString('saved_email')?.isNotEmpty ?? false) &&
          (savedPass?.isNotEmpty ?? false);
  final shouldStartHidden = startInTray && hasSavedSession;

  const windowOptions = WindowOptions(
    size: Size(1100, 800),
    minimumSize: Size(900, 650),
    center: true,
    title: 'Smart Inverter',
    backgroundColor: Colors.transparent,
  );

  await windowManager.waitUntilReadyToShow(windowOptions, () async {
    if (Platform.isWindows) {
      try {
        await windowManager.setIcon('assets/app_icon.ico');
      } catch (e) {
        LogService.log('⚠️ window icon setup failed', error: e);
      }
    }
    if (shouldStartHidden) {
      await windowManager.hide();
      LogService.log('🧩 startup: app hidden to tray by user setting');
    } else {
      await windowManager.show();
      await windowManager.focus();
    }
    await windowManager.setPreventClose(true);
  });

  // 3. Запуск додатку
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider(
          create: (_) => AppStateProvider()..loadSettings(),
        ),
      ],
      child: const MyApp(), // Тут тепер не має бути помилки
    ),
  );
} // <--- ПЕРЕВІРТЕ: Ця дужка має закривати функцію main()

// --- КЛАС МАЄ ПОЧИНАТИСЯ ПІСЛЯ ЗАКРИТТЯ main() ---

class MyApp extends StatefulWidget {
  const MyApp({super.key});

  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> with WindowListener {
  @override
  void initState() {
    windowManager.addListener(this);
    super.initState();
  }

  @override
  void dispose() {
    windowManager.removeListener(this);
    super.dispose();
  }

  @override
  void onWindowClose() async {
    var isPreventClose = await windowManager.isPreventClose();
    if (isPreventClose) {
      await windowManager.hide();
    }
  }

  @override
  Widget build(BuildContext context) {
    // Використовуємо select або watch для реактивності
    final provider = context.watch<AppStateProvider>();

    return MaterialApp(
      title: 'Smart Inverter',
      debugShowCheckedModeBanner: false,
      themeMode: provider.themeMode,
      theme: AppTheme.lightThemeForLanguage(provider.lang),
      darkTheme: AppTheme.darkThemeForLanguage(provider.lang),
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
      home: provider.isCheckingAuth
          ? Scaffold(
              body: Center(
                child: CircularProgressIndicator(
                  color: Theme.of(context).colorScheme.primary,
                ),
              ),
            )
          : (provider.isAuthenticated
              ? const MainScreen()
              : const AuthScreen()),
    );
  }
}

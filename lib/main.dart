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
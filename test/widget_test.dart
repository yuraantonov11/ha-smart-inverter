import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:inverter_app/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import 'package:inverter_app/main.dart';
import 'package:inverter_app/providers/app_provider.dart';
import 'package:inverter_app/screens/main_screen.dart';
import 'package:inverter_app/theme/app_theme.dart';

class _TestAppStateProvider extends AppStateProvider {
  @override
  void startTimers() {}

  @override
  Future<void> fetchData() async {}

  @override
  Future<void> toggleTheme() async {
    themeMode = themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    notifyListeners();
  }
}

Future<void> _pumpMainScreen(
  WidgetTester tester,
  AppStateProvider provider, {
  required Size size,
}) async {
  await tester.binding.setSurfaceSize(size);
  addTearDown(() => tester.binding.setSurfaceSize(null));

  await tester.pumpWidget(
    ChangeNotifierProvider<AppStateProvider>.value(
      value: provider,
      child: MaterialApp(
        theme: AppTheme.lightTheme,
        darkTheme: AppTheme.darkTheme,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const MainScreen(),
      ),
    ),
  );

  await tester.pumpAndSettle();
}

void main() {
  testWidgets('App shows loading indicator on startup',
      (WidgetTester tester) async {
    await tester.pumpWidget(
      ChangeNotifierProvider(
        create: (_) => AppStateProvider(),
        child: const MyApp(),
      ),
    );

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('MainScreen uses NavigationRail on wide layouts',
      (WidgetTester tester) async {
    final provider = _TestAppStateProvider();

    await _pumpMainScreen(
      tester,
      provider,
      size: const Size(1400, 900),
    );

    expect(find.byType(NavigationRail), findsOneWidget);
    expect(find.byType(NavigationBar), findsNothing);

    await tester.tap(find.text('Automation'));
    await tester.pumpAndSettle();

    expect(find.text('Intelligent HEMS Modes'), findsOneWidget);
  });

  testWidgets('MainScreen uses NavigationBar on compact layouts',
      (WidgetTester tester) async {
    final provider = _TestAppStateProvider();

    await _pumpMainScreen(
      tester,
      provider,
      size: const Size(820, 900),
    );

    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.byType(NavigationRail), findsNothing);

    await tester.tap(find.text('Automation'));
    await tester.pumpAndSettle();

    expect(find.text('Intelligent HEMS Modes'), findsOneWidget);
  });

  testWidgets('MainScreen opens Settings from desktop rail',
      (WidgetTester tester) async {
    final provider = _TestAppStateProvider();

    await _pumpMainScreen(
      tester,
      provider,
      size: const Size(1400, 900),
    );

    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();

    expect(find.text('Application Settings'), findsOneWidget);
    expect(find.text('Updates'), findsOneWidget);
  });

  testWidgets('MainScreen compact rail does not overflow',
      (WidgetTester tester) async {
    final provider = _TestAppStateProvider();

    await _pumpMainScreen(
      tester,
      provider,
      size: const Size(1000, 560),
    );

    await tester.pump();
    expect(find.byType(NavigationRail), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

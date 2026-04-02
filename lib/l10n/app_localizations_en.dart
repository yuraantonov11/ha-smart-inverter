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

  @override
  String get smartModes => 'Smart Modes';

  @override
  String get modeOff => 'Off';

  @override
  String get modeWinter => 'Winter Mode';

  @override
  String get modeWinterDesc => 'Description for winter';

  @override
  String get modeSummer => 'Summer Mode';

  @override
  String get modeSummerDesc => 'Description for summer';
}

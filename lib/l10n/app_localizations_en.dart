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
  String get data => 'Data';

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

  @override
  String get energyOverview => 'Energy Overview';

  @override
  String get production => 'Production';

  @override
  String get consumption => 'Consumption';

  @override
  String get day => 'Day';

  @override
  String get week => 'Week';

  @override
  String get month => 'Month';

  @override
  String get mon => 'Mon';

  @override
  String get tue => 'Tue';

  @override
  String get wed => 'Wed';

  @override
  String get thu => 'Thu';

  @override
  String get fri => 'Fri';

  @override
  String get sat => 'Sat';

  @override
  String get sun => 'Sun';

  @override
  String get battery => 'Battery';

  @override
  String get grid => 'Grid';

  @override
  String get modeAdaptive => 'Adaptive Intelligence (Auto)';

  @override
  String get modeAdaptiveSubtitle => 'Balance of autonomy and savings (SBU)';

  @override
  String get modeAdaptiveDesc =>
      'Inverter independently maneuvers between solar energy and battery. Grid is used only at critical discharge.';

  @override
  String get modeArbitrage => 'Night Arbitrage';

  @override
  String get modeArbitrageSubtitle => 'Maximum financial savings';

  @override
  String get modeArbitrageDesc =>
      'At 23:00, inverter smoothly charges battery at night tariff. During the day, priority is given to sun. Dynamic boiler shutdown is configured at low charge.';

  @override
  String get modeStorm => 'Reserve / Storm';

  @override
  String get modeStormSubtitle => 'Preparation for rolling blackouts';

  @override
  String get modeStormDesc =>
      'Financial savings are ignored. Battery is forcibly maintained at 100% from grid (USB mode). Readiness for unpredictable blackouts.';

  @override
  String get account => 'Account';

  @override
  String get appSettings => 'Application Settings';

  @override
  String get editProfile => 'Edit Profile';

  @override
  String get cancel => 'Cancel';

  @override
  String get save => 'Save';

  @override
  String get userNameDefault => 'User';

  @override
  String userId(String id) {
    return 'ID: $id';
  }

  @override
  String get inverterMode => 'Inverter Mode';

  @override
  String get advancedSettings => 'Advanced Settings';

  @override
  String get outputSourcePriority => 'Output Source Priority';

  @override
  String get chargerSourcePriority => 'Charger Source Priority';

  @override
  String get sbuPriority => 'SBU Priority';

  @override
  String get onlySolar => 'Only Solar (OSO)';

  @override
  String get solarFirst => 'Solar First (CSO)';

  @override
  String get hemsTitle => 'Intelligent HEMS Modes';

  @override
  String get hemsSubtitle => 'Choose an energy management strategy';

  @override
  String get gotIt => 'Got it';

  @override
  String get welcomeBack => 'Welcome Back';

  @override
  String get email => 'Email';

  @override
  String get password => 'Password';

  @override
  String get loginError => 'Invalid login or password';

  @override
  String get startWithWindows => 'Start with Windows';

  @override
  String get name => 'Name';

  @override
  String get solarSbu => 'SOLAR (SBU)';

  @override
  String get gridUsb => 'GRID (USB)';

  @override
  String get utilityFirstUsb => 'Utility First (USB)';

  @override
  String get solarFirstSub => 'Solar First (SUB)';

  @override
  String get solarUtilitySnu => 'Solar + Utility (SNU)';

  @override
  String get signInCloud => 'Sign in to Siseli Cloud';

  @override
  String get loginFailed => 'Login failed. Check credentials.';

  @override
  String get today => 'Today';

  @override
  String get total => 'Total';

  @override
  String get solar => 'Solar';

  @override
  String get load => 'Load';

  @override
  String updatedAt(String time) {
    return 'Updated at $time';
  }

  @override
  String get updateFailed => 'Update failed';

  @override
  String get updated => 'Updated!';

  @override
  String get enableSolar => 'Enable SOLAR (SBU)';

  @override
  String get enableGrid => 'Enable GRID (USB)';

  @override
  String get showApp => 'Show App';

  @override
  String get exit => 'Exit';

  @override
  String batteryLevel(String level) {
    return 'Battery: $level%';
  }
}

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

  /// No description provided for @data.
  ///
  /// In en, this message translates to:
  /// **'Data'**
  String get data;

  /// No description provided for @smartModes.
  ///
  /// In en, this message translates to:
  /// **'Smart Modes'**
  String get smartModes;

  /// No description provided for @modeOff.
  ///
  /// In en, this message translates to:
  /// **'Off'**
  String get modeOff;

  /// No description provided for @modeWinter.
  ///
  /// In en, this message translates to:
  /// **'Winter Mode'**
  String get modeWinter;

  /// No description provided for @modeWinterDesc.
  ///
  /// In en, this message translates to:
  /// **'Description for winter'**
  String get modeWinterDesc;

  /// No description provided for @modeSummer.
  ///
  /// In en, this message translates to:
  /// **'Summer Mode'**
  String get modeSummer;

  /// No description provided for @modeSummerDesc.
  ///
  /// In en, this message translates to:
  /// **'Description for summer'**
  String get modeSummerDesc;

  /// No description provided for @energyOverview.
  ///
  /// In en, this message translates to:
  /// **'Energy Overview'**
  String get energyOverview;

  /// No description provided for @production.
  ///
  /// In en, this message translates to:
  /// **'Production'**
  String get production;

  /// No description provided for @consumption.
  ///
  /// In en, this message translates to:
  /// **'Consumption'**
  String get consumption;

  /// No description provided for @day.
  ///
  /// In en, this message translates to:
  /// **'Day'**
  String get day;

  /// No description provided for @week.
  ///
  /// In en, this message translates to:
  /// **'Week'**
  String get week;

  /// No description provided for @month.
  ///
  /// In en, this message translates to:
  /// **'Month'**
  String get month;

  /// No description provided for @mon.
  ///
  /// In en, this message translates to:
  /// **'Mon'**
  String get mon;

  /// No description provided for @tue.
  ///
  /// In en, this message translates to:
  /// **'Tue'**
  String get tue;

  /// No description provided for @wed.
  ///
  /// In en, this message translates to:
  /// **'Wed'**
  String get wed;

  /// No description provided for @thu.
  ///
  /// In en, this message translates to:
  /// **'Thu'**
  String get thu;

  /// No description provided for @fri.
  ///
  /// In en, this message translates to:
  /// **'Fri'**
  String get fri;

  /// No description provided for @sat.
  ///
  /// In en, this message translates to:
  /// **'Sat'**
  String get sat;

  /// No description provided for @sun.
  ///
  /// In en, this message translates to:
  /// **'Sun'**
  String get sun;

  /// No description provided for @battery.
  ///
  /// In en, this message translates to:
  /// **'Battery'**
  String get battery;

  /// No description provided for @grid.
  ///
  /// In en, this message translates to:
  /// **'Grid'**
  String get grid;

  /// No description provided for @modeAdaptive.
  ///
  /// In en, this message translates to:
  /// **'Adaptive Intelligence (Auto)'**
  String get modeAdaptive;

  /// No description provided for @modeAdaptiveSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Balance of autonomy and savings (SBU)'**
  String get modeAdaptiveSubtitle;

  /// No description provided for @modeAdaptiveDesc.
  ///
  /// In en, this message translates to:
  /// **'Inverter independently maneuvers between solar energy and battery. Grid is used only at critical discharge.'**
  String get modeAdaptiveDesc;

  /// No description provided for @modeArbitrage.
  ///
  /// In en, this message translates to:
  /// **'Night Arbitrage'**
  String get modeArbitrage;

  /// No description provided for @modeArbitrageSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Maximum financial savings'**
  String get modeArbitrageSubtitle;

  /// No description provided for @modeArbitrageDesc.
  ///
  /// In en, this message translates to:
  /// **'At 23:00, inverter smoothly charges battery at night tariff. During the day, priority is given to sun. Dynamic boiler shutdown is configured at low charge.'**
  String get modeArbitrageDesc;

  /// No description provided for @modeStorm.
  ///
  /// In en, this message translates to:
  /// **'Reserve / Storm'**
  String get modeStorm;

  /// No description provided for @modeStormSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Preparation for rolling blackouts'**
  String get modeStormSubtitle;

  /// No description provided for @modeStormDesc.
  ///
  /// In en, this message translates to:
  /// **'Financial savings are ignored. Battery is forcibly maintained at 100% from grid (USB mode). Readiness for unpredictable blackouts.'**
  String get modeStormDesc;

  /// No description provided for @account.
  ///
  /// In en, this message translates to:
  /// **'Account'**
  String get account;

  /// No description provided for @appSettings.
  ///
  /// In en, this message translates to:
  /// **'Application Settings'**
  String get appSettings;

  /// No description provided for @editProfile.
  ///
  /// In en, this message translates to:
  /// **'Edit Profile'**
  String get editProfile;

  /// No description provided for @cancel.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get cancel;

  /// No description provided for @save.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get save;

  /// No description provided for @userNameDefault.
  ///
  /// In en, this message translates to:
  /// **'User'**
  String get userNameDefault;

  /// No description provided for @userId.
  ///
  /// In en, this message translates to:
  /// **'ID: {id}'**
  String userId(String id);

  /// No description provided for @inverterMode.
  ///
  /// In en, this message translates to:
  /// **'Inverter Mode'**
  String get inverterMode;

  /// No description provided for @advancedSettings.
  ///
  /// In en, this message translates to:
  /// **'Advanced Settings'**
  String get advancedSettings;

  /// No description provided for @outputSourcePriority.
  ///
  /// In en, this message translates to:
  /// **'Output Source Priority'**
  String get outputSourcePriority;

  /// No description provided for @chargerSourcePriority.
  ///
  /// In en, this message translates to:
  /// **'Charger Source Priority'**
  String get chargerSourcePriority;

  /// No description provided for @sbuPriority.
  ///
  /// In en, this message translates to:
  /// **'SBU Priority'**
  String get sbuPriority;

  /// No description provided for @onlySolar.
  ///
  /// In en, this message translates to:
  /// **'Only Solar (OSO)'**
  String get onlySolar;

  /// No description provided for @solarFirst.
  ///
  /// In en, this message translates to:
  /// **'Solar First (CSO)'**
  String get solarFirst;

  /// No description provided for @hemsTitle.
  ///
  /// In en, this message translates to:
  /// **'Intelligent HEMS Modes'**
  String get hemsTitle;

  /// No description provided for @hemsSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Choose an energy management strategy'**
  String get hemsSubtitle;

  /// No description provided for @gotIt.
  ///
  /// In en, this message translates to:
  /// **'Got it'**
  String get gotIt;

  /// No description provided for @welcomeBack.
  ///
  /// In en, this message translates to:
  /// **'Welcome Back'**
  String get welcomeBack;

  /// No description provided for @email.
  ///
  /// In en, this message translates to:
  /// **'Email'**
  String get email;

  /// No description provided for @password.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get password;

  /// No description provided for @loginError.
  ///
  /// In en, this message translates to:
  /// **'Invalid login or password'**
  String get loginError;

  /// No description provided for @startWithWindows.
  ///
  /// In en, this message translates to:
  /// **'Start with Windows'**
  String get startWithWindows;

  /// No description provided for @name.
  ///
  /// In en, this message translates to:
  /// **'Name'**
  String get name;

  /// No description provided for @solarSbu.
  ///
  /// In en, this message translates to:
  /// **'SOLAR (SBU)'**
  String get solarSbu;

  /// No description provided for @gridUsb.
  ///
  /// In en, this message translates to:
  /// **'GRID (USB)'**
  String get gridUsb;

  /// No description provided for @utilityFirstUsb.
  ///
  /// In en, this message translates to:
  /// **'Utility First (USB)'**
  String get utilityFirstUsb;

  /// No description provided for @solarFirstSub.
  ///
  /// In en, this message translates to:
  /// **'Solar First (SUB)'**
  String get solarFirstSub;

  /// No description provided for @solarUtilitySnu.
  ///
  /// In en, this message translates to:
  /// **'Solar + Utility (SNU)'**
  String get solarUtilitySnu;

  /// No description provided for @signInCloud.
  ///
  /// In en, this message translates to:
  /// **'Sign in to Siseli Cloud'**
  String get signInCloud;

  /// No description provided for @loginFailed.
  ///
  /// In en, this message translates to:
  /// **'Login failed. Check credentials.'**
  String get loginFailed;

  /// No description provided for @today.
  ///
  /// In en, this message translates to:
  /// **'Today'**
  String get today;

  /// No description provided for @total.
  ///
  /// In en, this message translates to:
  /// **'Total'**
  String get total;

  /// No description provided for @solar.
  ///
  /// In en, this message translates to:
  /// **'Solar'**
  String get solar;

  /// No description provided for @load.
  ///
  /// In en, this message translates to:
  /// **'Load'**
  String get load;

  /// No description provided for @updatedAt.
  ///
  /// In en, this message translates to:
  /// **'Updated at {time}'**
  String updatedAt(String time);

  /// No description provided for @updateFailed.
  ///
  /// In en, this message translates to:
  /// **'Update failed'**
  String get updateFailed;

  /// No description provided for @updated.
  ///
  /// In en, this message translates to:
  /// **'Updated!'**
  String get updated;

  /// No description provided for @enableSolar.
  ///
  /// In en, this message translates to:
  /// **'Enable SOLAR (SBU)'**
  String get enableSolar;

  /// No description provided for @enableGrid.
  ///
  /// In en, this message translates to:
  /// **'Enable GRID (USB)'**
  String get enableGrid;

  /// No description provided for @showApp.
  ///
  /// In en, this message translates to:
  /// **'Show App'**
  String get showApp;

  /// No description provided for @exit.
  ///
  /// In en, this message translates to:
  /// **'Exit'**
  String get exit;

  /// No description provided for @batteryLevel.
  ///
  /// In en, this message translates to:
  /// **'Battery: {level}%'**
  String batteryLevel(String level);
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

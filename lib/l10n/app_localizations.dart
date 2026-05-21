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

  /// No description provided for @lightTheme.
  ///
  /// In en, this message translates to:
  /// **'Light theme'**
  String get lightTheme;

  /// No description provided for @darkTheme.
  ///
  /// In en, this message translates to:
  /// **'Dark theme'**
  String get darkTheme;

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

  /// No description provided for @forecast.
  ///
  /// In en, this message translates to:
  /// **'Forecast'**
  String get forecast;

  /// No description provided for @modeAdaptive.
  ///
  /// In en, this message translates to:
  /// **'Adaptive Intelligence (Auto)'**
  String get modeAdaptive;

  /// No description provided for @modeAdaptiveSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Dynamic mode: forecast, tariff, and battery state'**
  String get modeAdaptiveSubtitle;

  /// No description provided for @modeAdaptiveDesc.
  ///
  /// In en, this message translates to:
  /// **'Adaptive mode continuously analyzes:\n• real-time PV generation and house consumption\n• solar forecast for the rest of the day\n• battery SOC and safety reserve\n• time-of-use tariff windows (night/day/evening)\n\nWhat it does:\n• At night, it predicts whether tomorrow\'s solar will be enough and decides whether grid charging is needed\n• During daytime, it optimizes source priority to preserve energy for evening peak\n• In evening hours, it uses battery energy down to the configured reserve threshold\n• It automatically falls back to grid if battery safety or supply stability is at risk\n\nResult: fewer manual switches, lower energy cost, and more stable day-to-day operation.'**
  String get modeAdaptiveDesc;

  /// No description provided for @modeArbitrage.
  ///
  /// In en, this message translates to:
  /// **'Night Arbitrage'**
  String get modeArbitrage;

  /// No description provided for @modeArbitrageSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Strict cost optimization by tariff windows'**
  String get modeArbitrageSubtitle;

  /// No description provided for @modeArbitrageDesc.
  ///
  /// In en, this message translates to:
  /// **'This mode prioritizes minimum electricity cost:\n• During night tariff, it forces grid operation and charges battery\n• During day/evening, it prioritizes battery and solar\n• Grid charging is disabled outside night tariff\n\nBest when cost saving is the primary goal, even with more battery cycling.'**
  String get modeArbitrageDesc;

  /// No description provided for @modeStorm.
  ///
  /// In en, this message translates to:
  /// **'Reserve / Storm'**
  String get modeStorm;

  /// No description provided for @modeStormSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Maximum backup readiness for outages'**
  String get modeStormSubtitle;

  /// No description provided for @modeStormDesc.
  ///
  /// In en, this message translates to:
  /// **'This mode prioritizes reliability over savings:\n• Keeps battery as full as possible\n• Powers home from grid to preserve battery for emergency use\n• Ignores tariff optimization when reserve readiness is critical\n\nRecommended before storms, unstable grid periods, or expected long outages.'**
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

  /// No description provided for @confirm.
  ///
  /// In en, this message translates to:
  /// **'Confirm'**
  String get confirm;

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
  /// **'Choose your strategy: adaptive automation, savings, or backup reserve'**
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

  /// No description provided for @startInTray.
  ///
  /// In en, this message translates to:
  /// **'Start in tray'**
  String get startInTray;

  /// No description provided for @startInTraySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Starts minimized when a saved session exists'**
  String get startInTraySubtitle;

  /// No description provided for @name.
  ///
  /// In en, this message translates to:
  /// **'Name'**
  String get name;

  /// No description provided for @notProvided.
  ///
  /// In en, this message translates to:
  /// **'Not provided'**
  String get notProvided;

  /// No description provided for @unknownValue.
  ///
  /// In en, this message translates to:
  /// **'Unknown'**
  String get unknownValue;

  /// No description provided for @accountStatusLocal.
  ///
  /// In en, this message translates to:
  /// **'Local profile only'**
  String get accountStatusLocal;

  /// No description provided for @accountStatusSynced.
  ///
  /// In en, this message translates to:
  /// **'Cloud profile synced'**
  String get accountStatusSynced;

  /// No description provided for @cloudAccount.
  ///
  /// In en, this message translates to:
  /// **'Cloud account'**
  String get cloudAccount;

  /// No description provided for @phoneLabel.
  ///
  /// In en, this message translates to:
  /// **'Phone'**
  String get phoneLabel;

  /// No description provided for @sessionId.
  ///
  /// In en, this message translates to:
  /// **'Session ID'**
  String get sessionId;

  /// No description provided for @accountProfileHint.
  ///
  /// In en, this message translates to:
  /// **'Profile data is used for account display and inverter cloud access.'**
  String get accountProfileHint;

  /// No description provided for @logoutConfirmMessage.
  ///
  /// In en, this message translates to:
  /// **'Are you sure you want to sign out from this device?'**
  String get logoutConfirmMessage;

  /// No description provided for @dangerZone.
  ///
  /// In en, this message translates to:
  /// **'Danger zone'**
  String get dangerZone;

  /// No description provided for @nameCannotBeEmpty.
  ///
  /// In en, this message translates to:
  /// **'Name cannot be empty'**
  String get nameCannotBeEmpty;

  /// No description provided for @copy.
  ///
  /// In en, this message translates to:
  /// **'Copy'**
  String get copy;

  /// No description provided for @copiedToClipboard.
  ///
  /// In en, this message translates to:
  /// **'Copied to clipboard'**
  String get copiedToClipboard;

  /// No description provided for @diagnosticsSnapshot.
  ///
  /// In en, this message translates to:
  /// **'Diagnostics snapshot'**
  String get diagnosticsSnapshot;

  /// No description provided for @diagnosticsSnapshotHint.
  ///
  /// In en, this message translates to:
  /// **'Copy a compact report for troubleshooting or support.'**
  String get diagnosticsSnapshotHint;

  /// No description provided for @copyDiagnosticsSnapshot.
  ///
  /// In en, this message translates to:
  /// **'Copy snapshot'**
  String get copyDiagnosticsSnapshot;

  /// No description provided for @diagnosticsSnapshotCopied.
  ///
  /// In en, this message translates to:
  /// **'Diagnostics snapshot copied to clipboard'**
  String get diagnosticsSnapshotCopied;

  /// No description provided for @updatesTitle.
  ///
  /// In en, this message translates to:
  /// **'Updates'**
  String get updatesTitle;

  /// No description provided for @updatesCheckingBackground.
  ///
  /// In en, this message translates to:
  /// **'Checking updates in background...'**
  String get updatesCheckingBackground;

  /// No description provided for @updatesChecking.
  ///
  /// In en, this message translates to:
  /// **'Checking for updates...'**
  String get updatesChecking;

  /// No description provided for @updatesSubtitleDefault.
  ///
  /// In en, this message translates to:
  /// **'Check and install latest version'**
  String get updatesSubtitleDefault;

  /// No description provided for @updatesSubtitleAvailable.
  ///
  /// In en, this message translates to:
  /// **'New version {version} is available'**
  String updatesSubtitleAvailable(String version);

  /// No description provided for @updatesSubtitleSkipped.
  ///
  /// In en, this message translates to:
  /// **'Version {version} is skipped'**
  String updatesSubtitleSkipped(String version);

  /// No description provided for @updatesSubtitleUpToDate.
  ///
  /// In en, this message translates to:
  /// **'You are up to date ({version})'**
  String updatesSubtitleUpToDate(String version);

  /// No description provided for @updatesLastChecked.
  ///
  /// In en, this message translates to:
  /// **'Last checked: {time}'**
  String updatesLastChecked(String time);

  /// No description provided for @updatesSkippedBanner.
  ///
  /// In en, this message translates to:
  /// **'Version {version} is currently skipped.'**
  String updatesSkippedBanner(String version);

  /// No description provided for @updatesSkippedRestored.
  ///
  /// In en, this message translates to:
  /// **'Skipped version restored.'**
  String get updatesSkippedRestored;

  /// No description provided for @updatesRestore.
  ///
  /// In en, this message translates to:
  /// **'Restore'**
  String get updatesRestore;

  /// No description provided for @updatesBannerAvailable.
  ///
  /// In en, this message translates to:
  /// **'New update {version} is available'**
  String updatesBannerAvailable(String version);

  /// No description provided for @updatesCurrentVersion.
  ///
  /// In en, this message translates to:
  /// **'Current version: {version}'**
  String updatesCurrentVersion(String version);

  /// No description provided for @updatesView.
  ///
  /// In en, this message translates to:
  /// **'View'**
  String get updatesView;

  /// No description provided for @updatesSkip.
  ///
  /// In en, this message translates to:
  /// **'Skip'**
  String get updatesSkip;

  /// No description provided for @updatesSkippedNow.
  ///
  /// In en, this message translates to:
  /// **'Version {version} was skipped.'**
  String updatesSkippedNow(String version);

  /// No description provided for @updatesNoInstallerFound.
  ///
  /// In en, this message translates to:
  /// **'No compatible installer was found in this release.'**
  String get updatesNoInstallerFound;

  /// No description provided for @updatesDialogAvailableTitle.
  ///
  /// In en, this message translates to:
  /// **'Update Available'**
  String get updatesDialogAvailableTitle;

  /// No description provided for @updatesDialogCurrent.
  ///
  /// In en, this message translates to:
  /// **'Current: {version}'**
  String updatesDialogCurrent(String version);

  /// No description provided for @updatesDialogLatest.
  ///
  /// In en, this message translates to:
  /// **'Latest: {version}'**
  String updatesDialogLatest(String version);

  /// No description provided for @updatesDialogPublished.
  ///
  /// In en, this message translates to:
  /// **'Published: {time}'**
  String updatesDialogPublished(String time);

  /// No description provided for @updatesDialogPackage.
  ///
  /// In en, this message translates to:
  /// **'Package: {name}'**
  String updatesDialogPackage(String name);

  /// No description provided for @updatesDialogSkipVersion.
  ///
  /// In en, this message translates to:
  /// **'Skip this version'**
  String get updatesDialogSkipVersion;

  /// No description provided for @updatesDialogLater.
  ///
  /// In en, this message translates to:
  /// **'Later'**
  String get updatesDialogLater;

  /// No description provided for @updatesDialogDownload.
  ///
  /// In en, this message translates to:
  /// **'Download'**
  String get updatesDialogDownload;

  /// No description provided for @updatesDialogDownloadingTitle.
  ///
  /// In en, this message translates to:
  /// **'Downloading update'**
  String get updatesDialogDownloadingTitle;

  /// No description provided for @updatesDialogDownloadFailedTitle.
  ///
  /// In en, this message translates to:
  /// **'Download failed'**
  String get updatesDialogDownloadFailedTitle;

  /// No description provided for @updatesDialogPreparing.
  ///
  /// In en, this message translates to:
  /// **'Preparing download...'**
  String get updatesDialogPreparing;

  /// No description provided for @updatesDialogInstallTitle.
  ///
  /// In en, this message translates to:
  /// **'Install Update'**
  String get updatesDialogInstallTitle;

  /// No description provided for @updatesDialogInstallPrompt.
  ///
  /// In en, this message translates to:
  /// **'Update downloaded ({version}). Install now? The app will close during installation.'**
  String updatesDialogInstallPrompt(String version);

  /// No description provided for @updatesDialogInstall.
  ///
  /// In en, this message translates to:
  /// **'Install'**
  String get updatesDialogInstall;

  /// No description provided for @updatesDialogInstallFailed.
  ///
  /// In en, this message translates to:
  /// **'Installation failed. Please run installer manually.'**
  String get updatesDialogInstallFailed;

  /// No description provided for @updatesDialogDownloadFailed.
  ///
  /// In en, this message translates to:
  /// **'Download failed. Check internet or release assets.'**
  String get updatesDialogDownloadFailed;

  /// No description provided for @updatesDialogClose.
  ///
  /// In en, this message translates to:
  /// **'Close'**
  String get updatesDialogClose;

  /// No description provided for @inverterSettings.
  ///
  /// In en, this message translates to:
  /// **'Inverter settings'**
  String get inverterSettings;

  /// No description provided for @refreshSettings.
  ///
  /// In en, this message translates to:
  /// **'Refresh settings'**
  String get refreshSettings;

  /// No description provided for @settingsLoadingTitle.
  ///
  /// In en, this message translates to:
  /// **'Settings are loading…'**
  String get settingsLoadingTitle;

  /// No description provided for @waitingInverterResponse.
  ///
  /// In en, this message translates to:
  /// **'Waiting for inverter response…'**
  String get waitingInverterResponse;

  /// No description provided for @tapRefreshToLoad.
  ///
  /// In en, this message translates to:
  /// **'Tap refresh to load settings'**
  String get tapRefreshToLoad;

  /// No description provided for @realtimeReadings.
  ///
  /// In en, this message translates to:
  /// **'Realtime readings'**
  String get realtimeReadings;

  /// No description provided for @unitOfMeasure.
  ///
  /// In en, this message translates to:
  /// **'Unit: {unit}'**
  String unitOfMeasure(String unit);

  /// No description provided for @selectValue.
  ///
  /// In en, this message translates to:
  /// **'Select value'**
  String get selectValue;

  /// No description provided for @newValue.
  ///
  /// In en, this message translates to:
  /// **'New value'**
  String get newValue;

  /// No description provided for @apply.
  ///
  /// In en, this message translates to:
  /// **'Apply'**
  String get apply;

  /// No description provided for @debugLogs.
  ///
  /// In en, this message translates to:
  /// **'Debug Logs'**
  String get debugLogs;

  /// No description provided for @viewSystemLogs.
  ///
  /// In en, this message translates to:
  /// **'View system logs'**
  String get viewSystemLogs;

  /// No description provided for @analyzeSystemLogs.
  ///
  /// In en, this message translates to:
  /// **'Analyze app errors and API calls'**
  String get analyzeSystemLogs;

  /// No description provided for @stationParameters.
  ///
  /// In en, this message translates to:
  /// **'Station parameters'**
  String get stationParameters;

  /// No description provided for @stationParametersHint.
  ///
  /// In en, this message translates to:
  /// **'These values help the intelligent algorithm calculate energy balance and weather-based forecasts more accurately.'**
  String get stationParametersHint;

  /// No description provided for @batteryCapacityLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery capacity'**
  String get batteryCapacityLabel;

  /// No description provided for @inputBreakerLabel.
  ///
  /// In en, this message translates to:
  /// **'Input breaker'**
  String get inputBreakerLabel;

  /// No description provided for @gridVoltageLabel.
  ///
  /// In en, this message translates to:
  /// **'Grid voltage'**
  String get gridVoltageLabel;

  /// No description provided for @houseLoadReserveLabel.
  ///
  /// In en, this message translates to:
  /// **'House reserve load'**
  String get houseLoadReserveLabel;

  /// No description provided for @autoReserveLoadTitle.
  ///
  /// In en, this message translates to:
  /// **'Auto reserve load'**
  String get autoReserveLoadTitle;

  /// No description provided for @autoReserveLoadEnabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Reserve is updated automatically from live and historical load'**
  String get autoReserveLoadEnabledSubtitle;

  /// No description provided for @autoReserveLoadDisabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Reserve is fixed manually'**
  String get autoReserveLoadDisabledSubtitle;

  /// No description provided for @reserveModeAuto.
  ///
  /// In en, this message translates to:
  /// **'Mode: AUTO'**
  String get reserveModeAuto;

  /// No description provided for @reserveModeManual.
  ///
  /// In en, this message translates to:
  /// **'Mode: MANUAL'**
  String get reserveModeManual;

  /// No description provided for @reserveModeAutoHint.
  ///
  /// In en, this message translates to:
  /// **'AUTO: reserve updates from live load and hourly profile with smoothing and safety headroom.'**
  String get reserveModeAutoHint;

  /// No description provided for @reserveModeManualHint.
  ///
  /// In en, this message translates to:
  /// **'MANUAL: reserve stays fixed to the value you set.'**
  String get reserveModeManualHint;

  /// No description provided for @houseLoadReserveHint.
  ///
  /// In en, this message translates to:
  /// **'Reserve power for additional home appliances to avoid breaker trips during charging.'**
  String get houseLoadReserveHint;

  /// No description provided for @autoEstimateReserveLoad.
  ///
  /// In en, this message translates to:
  /// **'Auto estimate reserve load'**
  String get autoEstimateReserveLoad;

  /// No description provided for @chargePowerEstimateTitle.
  ///
  /// In en, this message translates to:
  /// **'Safe charge speed estimate'**
  String get chargePowerEstimateTitle;

  /// No description provided for @chargePowerSafeLimitLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery charge power limit'**
  String get chargePowerSafeLimitLabel;

  /// No description provided for @chargeCurrentSafeLimitLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery charge current limit'**
  String get chargeCurrentSafeLimitLabel;

  /// No description provided for @chargeCurrentConservativeLabel.
  ///
  /// In en, this message translates to:
  /// **'Conservative charge current'**
  String get chargeCurrentConservativeLabel;

  /// No description provided for @chargeInputCurrentEstimateLabel.
  ///
  /// In en, this message translates to:
  /// **'Estimated AC input current'**
  String get chargeInputCurrentEstimateLabel;

  /// No description provided for @chargeBreakerRiskSafe.
  ///
  /// In en, this message translates to:
  /// **'Breaker risk: low'**
  String get chargeBreakerRiskSafe;

  /// No description provided for @chargeBreakerRiskElevated.
  ///
  /// In en, this message translates to:
  /// **'Breaker risk: elevated'**
  String get chargeBreakerRiskElevated;

  /// No description provided for @chargeBreakerRiskHigh.
  ///
  /// In en, this message translates to:
  /// **'Breaker risk: high (trip likely)'**
  String get chargeBreakerRiskHigh;

  /// No description provided for @chargeBreakerRiskHint.
  ///
  /// In en, this message translates to:
  /// **'Analysis uses reserve/live/profile house load: {load} W'**
  String chargeBreakerRiskHint(String load);

  /// No description provided for @chargeTimeToFullLabel.
  ///
  /// In en, this message translates to:
  /// **'Time to 100%'**
  String get chargeTimeToFullLabel;

  /// No description provided for @chargeTimeUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Not enough data'**
  String get chargeTimeUnavailable;

  /// No description provided for @chargeEstimateNoRealtimeSoc.
  ///
  /// In en, this message translates to:
  /// **'Realtime SOC is unavailable. Charge time will appear when live data is received.'**
  String get chargeEstimateNoRealtimeSoc;

  /// No description provided for @chargeEstimateBasedOnSoc.
  ///
  /// In en, this message translates to:
  /// **'Estimate based on current SOC: {soc}%'**
  String chargeEstimateBasedOnSoc(String soc);

  /// No description provided for @panelPowerLabel.
  ///
  /// In en, this message translates to:
  /// **'Panel power'**
  String get panelPowerLabel;

  /// No description provided for @inverterPowerLabel.
  ///
  /// In en, this message translates to:
  /// **'Inverter power'**
  String get inverterPowerLabel;

  /// No description provided for @locationPreset.
  ///
  /// In en, this message translates to:
  /// **'Location preset'**
  String get locationPreset;

  /// No description provided for @latitudeLabel.
  ///
  /// In en, this message translates to:
  /// **'Latitude'**
  String get latitudeLabel;

  /// No description provided for @longitudeLabel.
  ///
  /// In en, this message translates to:
  /// **'Longitude'**
  String get longitudeLabel;

  /// No description provided for @timeZoneLabel.
  ///
  /// In en, this message translates to:
  /// **'Time zone'**
  String get timeZoneLabel;

  /// No description provided for @astronomicalWindowsTitle.
  ///
  /// In en, this message translates to:
  /// **'Astronomical windows (sunrise/sunset)'**
  String get astronomicalWindowsTitle;

  /// No description provided for @astronomicalWindowsAutoSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Auto windows from geo coordinates'**
  String get astronomicalWindowsAutoSubtitle;

  /// No description provided for @astronomicalWindowsManualSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Use manual hour boundaries'**
  String get astronomicalWindowsManualSubtitle;

  /// No description provided for @manualDayStartHour.
  ///
  /// In en, this message translates to:
  /// **'Manual day start hour'**
  String get manualDayStartHour;

  /// No description provided for @manualEveningStartHour.
  ///
  /// In en, this message translates to:
  /// **'Manual evening start hour'**
  String get manualEveningStartHour;

  /// No description provided for @manualNightStartHour.
  ///
  /// In en, this message translates to:
  /// **'Manual night start hour'**
  String get manualNightStartHour;

  /// No description provided for @geoPresetKyiv.
  ///
  /// In en, this message translates to:
  /// **'Kyiv, UA'**
  String get geoPresetKyiv;

  /// No description provided for @geoPresetLviv.
  ///
  /// In en, this message translates to:
  /// **'Lviv, UA'**
  String get geoPresetLviv;

  /// No description provided for @geoPresetOdesa.
  ///
  /// In en, this message translates to:
  /// **'Odesa, UA'**
  String get geoPresetOdesa;

  /// No description provided for @geoPresetDnipro.
  ///
  /// In en, this message translates to:
  /// **'Dnipro, UA'**
  String get geoPresetDnipro;

  /// No description provided for @geoPresetKharkiv.
  ///
  /// In en, this message translates to:
  /// **'Kharkiv, UA'**
  String get geoPresetKharkiv;

  /// No description provided for @geoPresetWarsaw.
  ///
  /// In en, this message translates to:
  /// **'Warsaw, PL'**
  String get geoPresetWarsaw;

  /// No description provided for @geoPresetBerlin.
  ///
  /// In en, this message translates to:
  /// **'Berlin, DE'**
  String get geoPresetBerlin;

  /// No description provided for @geoPresetCustom.
  ///
  /// In en, this message translates to:
  /// **'Custom (manual)'**
  String get geoPresetCustom;

  /// No description provided for @geoSummary.
  ///
  /// In en, this message translates to:
  /// **'Geo: {latitude}, {longitude} ({timeZone})'**
  String geoSummary(String latitude, String longitude, String timeZone);

  /// No description provided for @windowsAstronomicalAuto.
  ///
  /// In en, this message translates to:
  /// **'Windows: astronomical (auto)'**
  String get windowsAstronomicalAuto;

  /// No description provided for @windowsManualSummary.
  ///
  /// In en, this message translates to:
  /// **'Windows: manual {day}:00 / {evening}:00 / {night}:00'**
  String windowsManualSummary(String day, String evening, String night);

  /// No description provided for @hardwareSettingsSaved.
  ///
  /// In en, this message translates to:
  /// **'Hardware settings saved!'**
  String get hardwareSettingsSaved;

  /// No description provided for @hardwareSummary.
  ///
  /// In en, this message translates to:
  /// **'Battery: {battery} Ah • PV: {pv} W\nInverter: {inverter} W'**
  String hardwareSummary(String battery, String pv, String inverter);

  /// No description provided for @chargeLimitSummary.
  ///
  /// In en, this message translates to:
  /// **'Input C{breaker} -> safe battery charge {power} W (~{current} A)'**
  String chargeLimitSummary(String breaker, String power, String current);

  /// No description provided for @logsTitle.
  ///
  /// In en, this message translates to:
  /// **'App Logs'**
  String get logsTitle;

  /// No description provided for @logsAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get logsAll;

  /// No description provided for @logsInfo.
  ///
  /// In en, this message translates to:
  /// **'Info'**
  String get logsInfo;

  /// No description provided for @logsWarn.
  ///
  /// In en, this message translates to:
  /// **'Warn'**
  String get logsWarn;

  /// No description provided for @logsError.
  ///
  /// In en, this message translates to:
  /// **'Error'**
  String get logsError;

  /// No description provided for @logsNoEntries.
  ///
  /// In en, this message translates to:
  /// **'No logs yet.'**
  String get logsNoEntries;

  /// No description provided for @logsErrorPrefix.
  ///
  /// In en, this message translates to:
  /// **'Error'**
  String get logsErrorPrefix;

  /// No description provided for @logsCopyFiltered.
  ///
  /// In en, this message translates to:
  /// **'Copy filtered'**
  String get logsCopyFiltered;

  /// No description provided for @clear.
  ///
  /// In en, this message translates to:
  /// **'Clear'**
  String get clear;

  /// No description provided for @logsCopied.
  ///
  /// In en, this message translates to:
  /// **'Logs copied to clipboard'**
  String get logsCopied;

  /// No description provided for @logsSummary.
  ///
  /// In en, this message translates to:
  /// **'Total: {total}  |  Info: {info}  Warn: {warn}  Error: {error}'**
  String logsSummary(String total, String info, String warn, String error);

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

  /// No description provided for @modeFromSolar.
  ///
  /// In en, this message translates to:
  /// **'From solar'**
  String get modeFromSolar;

  /// No description provided for @modeFromGrid.
  ///
  /// In en, this message translates to:
  /// **'From grid'**
  String get modeFromGrid;

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

  /// No description provided for @presetDisabled.
  ///
  /// In en, this message translates to:
  /// **'Disabled'**
  String get presetDisabled;

  /// No description provided for @presetEnabled.
  ///
  /// In en, this message translates to:
  /// **'Enabled'**
  String get presetEnabled;

  /// No description provided for @presetOutputUsb.
  ///
  /// In en, this message translates to:
  /// **'USB - Grid priority'**
  String get presetOutputUsb;

  /// No description provided for @presetOutputSub.
  ///
  /// In en, this message translates to:
  /// **'SUB - Solar first'**
  String get presetOutputSub;

  /// No description provided for @presetOutputSbu.
  ///
  /// In en, this message translates to:
  /// **'SBU - Solar/Battery priority'**
  String get presetOutputSbu;

  /// No description provided for @presetChargerCso.
  ///
  /// In en, this message translates to:
  /// **'CSO - Solar first'**
  String get presetChargerCso;

  /// No description provided for @presetChargerSnu.
  ///
  /// In en, this message translates to:
  /// **'SNU - Solar + Utility'**
  String get presetChargerSnu;

  /// No description provided for @presetChargerOso.
  ///
  /// In en, this message translates to:
  /// **'OSO - Solar only'**
  String get presetChargerOso;

  /// No description provided for @presetChargerUtilityOnly.
  ///
  /// In en, this message translates to:
  /// **'Utility only'**
  String get presetChargerUtilityOnly;

  /// No description provided for @presetFrequency50Hz.
  ///
  /// In en, this message translates to:
  /// **'50 Hz'**
  String get presetFrequency50Hz;

  /// No description provided for @presetFrequency60Hz.
  ///
  /// In en, this message translates to:
  /// **'60 Hz'**
  String get presetFrequency60Hz;

  /// No description provided for @presetAcInputAplWide.
  ///
  /// In en, this message translates to:
  /// **'APL - Wide range'**
  String get presetAcInputAplWide;

  /// No description provided for @presetAcInputUpsNarrow.
  ///
  /// In en, this message translates to:
  /// **'UPS - Narrow range'**
  String get presetAcInputUpsNarrow;

  /// No description provided for @presetBatteryTypeAgm.
  ///
  /// In en, this message translates to:
  /// **'AGM'**
  String get presetBatteryTypeAgm;

  /// No description provided for @presetBatteryTypeFlooded.
  ///
  /// In en, this message translates to:
  /// **'Flooded'**
  String get presetBatteryTypeFlooded;

  /// No description provided for @presetBatteryTypeUser.
  ///
  /// In en, this message translates to:
  /// **'User-defined'**
  String get presetBatteryTypeUser;

  /// No description provided for @presetBatteryTypeLib.
  ///
  /// In en, this message translates to:
  /// **'LIB (Lithium)'**
  String get presetBatteryTypeLib;

  /// No description provided for @presetBatteryTypeLife.
  ///
  /// In en, this message translates to:
  /// **'LiFe'**
  String get presetBatteryTypeLife;

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

  /// No description provided for @forecastNextDays.
  ///
  /// In en, this message translates to:
  /// **'Solar forecast for next days'**
  String get forecastNextDays;

  /// No description provided for @forecastPeak.
  ///
  /// In en, this message translates to:
  /// **'Peak'**
  String get forecastPeak;

  /// No description provided for @forecastUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Forecast data is temporarily unavailable'**
  String get forecastUnavailable;

  /// No description provided for @equipmentStatus.
  ///
  /// In en, this message translates to:
  /// **'Equipment status'**
  String get equipmentStatus;

  /// No description provided for @inverterLoad.
  ///
  /// In en, this message translates to:
  /// **'Inverter load'**
  String get inverterLoad;

  /// No description provided for @pvGeneration.
  ///
  /// In en, this message translates to:
  /// **'PV generation'**
  String get pvGeneration;

  /// No description provided for @refreshChart.
  ///
  /// In en, this message translates to:
  /// **'Refresh chart'**
  String get refreshChart;

  /// No description provided for @batterySignHint.
  ///
  /// In en, this message translates to:
  /// **'Battery: \'+\' means charge, \'-\' means discharge.'**
  String get batterySignHint;

  /// No description provided for @chartNoDataTitle.
  ///
  /// In en, this message translates to:
  /// **'No data yet'**
  String get chartNoDataTitle;

  /// No description provided for @chartNoDataMessage.
  ///
  /// In en, this message translates to:
  /// **'The chart should load in a moment.'**
  String get chartNoDataMessage;

  /// No description provided for @lessThanMinute.
  ///
  /// In en, this message translates to:
  /// **'< 1 min'**
  String get lessThanMinute;

  /// No description provided for @minutesAgo.
  ///
  /// In en, this message translates to:
  /// **'{count} min ago'**
  String minutesAgo(String count);

  /// No description provided for @hoursAgo.
  ///
  /// In en, this message translates to:
  /// **'{count} hr ago'**
  String hoursAgo(String count);

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

  /// No description provided for @connectionOnline.
  ///
  /// In en, this message translates to:
  /// **'Online'**
  String get connectionOnline;

  /// No description provided for @connectionOffline.
  ///
  /// In en, this message translates to:
  /// **'Offline'**
  String get connectionOffline;

  /// No description provided for @gridOutageDetectedShort.
  ///
  /// In en, this message translates to:
  /// **'Grid outage'**
  String get gridOutageDetectedShort;

  /// No description provided for @gridOutageVisualTitle.
  ///
  /// In en, this message translates to:
  /// **'Grid power is unavailable'**
  String get gridOutageVisualTitle;

  /// No description provided for @gridOutageVisualBody.
  ///
  /// In en, this message translates to:
  /// **'Detected by input voltage ({voltage} V), not by current mode switch'**
  String gridOutageVisualBody(String voltage);

  /// No description provided for @backupRuntimeHybrid.
  ///
  /// In en, this message translates to:
  /// **'Estimated backup time (battery + solar): ~{duration}'**
  String backupRuntimeHybrid(String duration);

  /// No description provided for @backupRuntimeBatteryOnly.
  ///
  /// In en, this message translates to:
  /// **'Battery only: ~{duration}'**
  String backupRuntimeBatteryOnly(String duration);

  /// No description provided for @backupRuntimeSolarCoverHint.
  ///
  /// In en, this message translates to:
  /// **'Solar currently covers the load, so actual runtime may be longer.'**
  String get backupRuntimeSolarCoverHint;

  /// No description provided for @runtimeInfinite.
  ///
  /// In en, this message translates to:
  /// **'continuous'**
  String get runtimeInfinite;

  /// No description provided for @runtimeNow.
  ///
  /// In en, this message translates to:
  /// **'now'**
  String get runtimeNow;

  /// No description provided for @runtimeMinutes.
  ///
  /// In en, this message translates to:
  /// **'{minutes} min'**
  String runtimeMinutes(String minutes);

  /// No description provided for @runtimeHoursOnly.
  ///
  /// In en, this message translates to:
  /// **'{hours} h'**
  String runtimeHoursOnly(String hours);

  /// No description provided for @runtimeHoursMinutes.
  ///
  /// In en, this message translates to:
  /// **'{hours} h {minutes} min'**
  String runtimeHoursMinutes(String hours, String minutes);

  /// No description provided for @lastRealtimeUpdate.
  ///
  /// In en, this message translates to:
  /// **'Last update'**
  String get lastRealtimeUpdate;

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

  /// No description provided for @retry.
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get retry;

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

  /// No description provided for @tooltipTodayEnergy.
  ///
  /// In en, this message translates to:
  /// **'Total solar energy generated today'**
  String get tooltipTodayEnergy;

  /// No description provided for @tooltipTotalEnergy.
  ///
  /// In en, this message translates to:
  /// **'Total solar energy generated since device installation'**
  String get tooltipTotalEnergy;

  /// No description provided for @tooltipCo2.
  ///
  /// In en, this message translates to:
  /// **'Estimated CO₂ emission reduction based on solar generation'**
  String get tooltipCo2;

  /// No description provided for @tooltipInverterLoad.
  ///
  /// In en, this message translates to:
  /// **'Current inverter load relative to its rated power capacity'**
  String get tooltipInverterLoad;

  /// No description provided for @tooltipPvGeneration.
  ///
  /// In en, this message translates to:
  /// **'Current PV output relative to total installed panel capacity'**
  String get tooltipPvGeneration;

  /// No description provided for @batteryLevel.
  ///
  /// In en, this message translates to:
  /// **'Battery: {level}%'**
  String batteryLevel(String level);

  /// No description provided for @batteryInstallYearLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery installation year'**
  String get batteryInstallYearLabel;

  /// No description provided for @hemsStrategyLabel.
  ///
  /// In en, this message translates to:
  /// **'HEMS optimization strategy'**
  String get hemsStrategyLabel;

  /// No description provided for @hemsStrategyEconomical.
  ///
  /// In en, this message translates to:
  /// **'Economical (minimize grid cost)'**
  String get hemsStrategyEconomical;

  /// No description provided for @hemsStrategySolarMaxed.
  ///
  /// In en, this message translates to:
  /// **'Solar Maxed (maximize self-consumption)'**
  String get hemsStrategySolarMaxed;

  /// No description provided for @hemsStrategyBatteryLife.
  ///
  /// In en, this message translates to:
  /// **'Battery Life (conservative cycles)'**
  String get hemsStrategyBatteryLife;

  /// No description provided for @hemsStrategyGridReliance.
  ///
  /// In en, this message translates to:
  /// **'Grid Reliance (resilience / off-grid)'**
  String get hemsStrategyGridReliance;

  /// No description provided for @hemsStrategyHybrid.
  ///
  /// In en, this message translates to:
  /// **'Hybrid (balanced — recommended)'**
  String get hemsStrategyHybrid;

  /// No description provided for @moneySavedMonth.
  ///
  /// In en, this message translates to:
  /// **'Saved this month'**
  String get moneySavedMonth;

  /// No description provided for @paymentThisMonth.
  ///
  /// In en, this message translates to:
  /// **'To pay this month'**
  String get paymentThisMonth;

  /// No description provided for @currencyUah.
  ///
  /// In en, this message translates to:
  /// **'UAH'**
  String get currencyUah;

  /// No description provided for @energyTariffLabel.
  ///
  /// In en, this message translates to:
  /// **'Electricity tariff'**
  String get energyTariffLabel;

  /// No description provided for @energyTariffDayLabel.
  ///
  /// In en, this message translates to:
  /// **'Day tariff'**
  String get energyTariffDayLabel;

  /// No description provided for @energyTariffNightLabel.
  ///
  /// In en, this message translates to:
  /// **'Night tariff'**
  String get energyTariffNightLabel;

  /// No description provided for @energyTariffUnit.
  ///
  /// In en, this message translates to:
  /// **'UAH/kWh'**
  String get energyTariffUnit;

  /// No description provided for @nightEnergyShareLabel.
  ///
  /// In en, this message translates to:
  /// **'Night energy share'**
  String get nightEnergyShareLabel;

  /// No description provided for @nightEnergyShareUnit.
  ///
  /// In en, this message translates to:
  /// **'%'**
  String get nightEnergyShareUnit;

  /// No description provided for @autoEstimateNightShare.
  ///
  /// In en, this message translates to:
  /// **'Auto estimate night share'**
  String get autoEstimateNightShare;

  /// No description provided for @batteryRoundTripEfficiencyLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery round-trip efficiency'**
  String get batteryRoundTripEfficiencyLabel;

  /// No description provided for @batteryRoundTripEfficiencyHint.
  ///
  /// In en, this message translates to:
  /// **'Used to reduce savings from battery-shifted energy. Set 100% to disable battery-loss correction.'**
  String get batteryRoundTripEfficiencyHint;

  /// No description provided for @nightShareFallbackHint.
  ///
  /// In en, this message translates to:
  /// **'Used only as a fallback when hourly telemetry economics is unavailable.'**
  String get nightShareFallbackHint;

  /// No description provided for @economicsMethodTelemetry.
  ///
  /// In en, this message translates to:
  /// **'Hourly telemetry • Battery efficiency {efficiency}%'**
  String economicsMethodTelemetry(String efficiency);

  /// No description provided for @economicsMethodEstimated.
  ///
  /// In en, this message translates to:
  /// **'Fallback estimate • Night share {share}%'**
  String economicsMethodEstimated(String share);

  /// No description provided for @calculationSourceLabel.
  ///
  /// In en, this message translates to:
  /// **'Calculation source'**
  String get calculationSourceLabel;

  /// No description provided for @calculationAccuracyLabel.
  ///
  /// In en, this message translates to:
  /// **'Accuracy'**
  String get calculationAccuracyLabel;

  /// No description provided for @calculationSourceTelemetry.
  ///
  /// In en, this message translates to:
  /// **'Hourly telemetry'**
  String get calculationSourceTelemetry;

  /// No description provided for @calculationSourceFallback.
  ///
  /// In en, this message translates to:
  /// **'Fallback estimate'**
  String get calculationSourceFallback;

  /// No description provided for @calculationAccuracyHigh.
  ///
  /// In en, this message translates to:
  /// **'High'**
  String get calculationAccuracyHigh;

  /// No description provided for @calculationAccuracyEstimated.
  ///
  /// In en, this message translates to:
  /// **'Estimated'**
  String get calculationAccuracyEstimated;

  /// No description provided for @effectiveTariffFormula.
  ///
  /// In en, this message translates to:
  /// **'Formula: {day}*(1-{share}%) + {night}*{share}%'**
  String effectiveTariffFormula(String day, String night, String share);

  /// No description provided for @tooltipMoneySavedMonthTelemetry.
  ///
  /// In en, this message translates to:
  /// **'Calculated from telemetry with hourly day/night tariff. Battery-shifted energy is adjusted by {efficiency}% round-trip efficiency.'**
  String tooltipMoneySavedMonthTelemetry(String efficiency);

  /// No description provided for @tooltipMoneySavedMonthEstimated.
  ///
  /// In en, this message translates to:
  /// **'Estimated from self-consumed solar using fallback night share {share}% and battery efficiency {efficiency}%.'**
  String tooltipMoneySavedMonthEstimated(String share, String efficiency);

  /// No description provided for @tooltipPaymentThisMonthTelemetry.
  ///
  /// In en, this message translates to:
  /// **'Calculated from telemetry with hourly tariff windows: day from {dayStart}:00, night from {nightStart}:00.'**
  String tooltipPaymentThisMonthTelemetry(String dayStart, String nightStart);

  /// No description provided for @tooltipPaymentThisMonthEstimated.
  ///
  /// In en, this message translates to:
  /// **'Estimated monthly bill using fallback night share {share}% for day/night tariff split.'**
  String tooltipPaymentThisMonthEstimated(String share);

  /// No description provided for @tooltipEffectiveTariffTelemetry.
  ///
  /// In en, this message translates to:
  /// **'Reference blended tariff only. Actual payment and savings above are calculated hourly from telemetry using day from {dayStart}:00 and night from {nightStart}:00.'**
  String tooltipEffectiveTariffTelemetry(String dayStart, String nightStart);

  /// No description provided for @tooltipMoneySavedMonth.
  ///
  /// In en, this message translates to:
  /// **'Estimated money saved this month from self-consumed solar energy'**
  String get tooltipMoneySavedMonth;

  /// No description provided for @tooltipPaymentThisMonth.
  ///
  /// In en, this message translates to:
  /// **'Estimated monthly bill for imported grid energy'**
  String get tooltipPaymentThisMonth;

  /// No description provided for @projectedSavedMonth.
  ///
  /// In en, this message translates to:
  /// **'Projected saved by month end'**
  String get projectedSavedMonth;

  /// No description provided for @projectedPaymentMonth.
  ///
  /// In en, this message translates to:
  /// **'Projected payment by month end'**
  String get projectedPaymentMonth;

  /// No description provided for @tooltipProjectedSavedMonth.
  ///
  /// In en, this message translates to:
  /// **'Forecast based on current month trend of self-consumed solar energy'**
  String get tooltipProjectedSavedMonth;

  /// No description provided for @tooltipProjectedPaymentMonth.
  ///
  /// In en, this message translates to:
  /// **'Forecast based on current month trend of imported grid energy'**
  String get tooltipProjectedPaymentMonth;

  /// No description provided for @monthlyEnergyBreakdown.
  ///
  /// In en, this message translates to:
  /// **'Monthly energy breakdown'**
  String get monthlyEnergyBreakdown;

  /// No description provided for @monthLoadEnergy.
  ///
  /// In en, this message translates to:
  /// **'Load this month'**
  String get monthLoadEnergy;

  /// No description provided for @monthGridImport.
  ///
  /// In en, this message translates to:
  /// **'Grid import'**
  String get monthGridImport;

  /// No description provided for @monthSelfConsumed.
  ///
  /// In en, this message translates to:
  /// **'Self-consumed solar'**
  String get monthSelfConsumed;

  /// No description provided for @monthGridCost.
  ///
  /// In en, this message translates to:
  /// **'Grid cost this month'**
  String get monthGridCost;

  /// No description provided for @monthSavedCost.
  ///
  /// In en, this message translates to:
  /// **'Saved cost this month'**
  String get monthSavedCost;

  /// No description provided for @monthEffectiveTariff.
  ///
  /// In en, this message translates to:
  /// **'Effective tariff'**
  String get monthEffectiveTariff;

  /// No description provided for @tooltipMonthProgress.
  ///
  /// In en, this message translates to:
  /// **'Current month progress'**
  String get tooltipMonthProgress;

  /// No description provided for @plannedOutageTitle.
  ///
  /// In en, this message translates to:
  /// **'Planned outage alert'**
  String get plannedOutageTitle;

  /// No description provided for @plannedOutageEnabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Auto-precharge before outage'**
  String get plannedOutageEnabledSubtitle;

  /// No description provided for @plannedOutageDisabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'No manual outage schedule'**
  String get plannedOutageDisabledSubtitle;

  /// No description provided for @plannedOutageStartLabel.
  ///
  /// In en, this message translates to:
  /// **'Outage start'**
  String get plannedOutageStartLabel;

  /// No description provided for @plannedOutageEndLabel.
  ///
  /// In en, this message translates to:
  /// **'Outage end'**
  String get plannedOutageEndLabel;

  /// No description provided for @notificationsTitle.
  ///
  /// In en, this message translates to:
  /// **'Notifications'**
  String get notificationsTitle;

  /// No description provided for @notificationsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No notifications yet'**
  String get notificationsEmpty;

  /// No description provided for @notificationsClear.
  ///
  /// In en, this message translates to:
  /// **'Clear all'**
  String get notificationsClear;

  /// No description provided for @notificationMarkAllRead.
  ///
  /// In en, this message translates to:
  /// **'Mark all read'**
  String get notificationMarkAllRead;

  /// No description provided for @notifGridOutageTitle.
  ///
  /// In en, this message translates to:
  /// **'Grid Power Outage'**
  String get notifGridOutageTitle;

  /// No description provided for @notifGridOutageBody.
  ///
  /// In en, this message translates to:
  /// **'Grid voltage dropped — running on solar/battery'**
  String get notifGridOutageBody;

  /// No description provided for @notifGridRestoredTitle.
  ///
  /// In en, this message translates to:
  /// **'Grid Power Restored'**
  String get notifGridRestoredTitle;

  /// No description provided for @notifGridRestoredBody.
  ///
  /// In en, this message translates to:
  /// **'Grid voltage is back to normal ({voltage} V)'**
  String notifGridRestoredBody(String voltage);

  /// No description provided for @notifLowBatteryTitle.
  ///
  /// In en, this message translates to:
  /// **'Low Battery'**
  String get notifLowBatteryTitle;

  /// No description provided for @notifLowBatteryBody.
  ///
  /// In en, this message translates to:
  /// **'Battery SOC is {soc}% — approaching reserve threshold'**
  String notifLowBatteryBody(String soc);

  /// No description provided for @notifBatteryRecoveredTitle.
  ///
  /// In en, this message translates to:
  /// **'Battery Charged'**
  String get notifBatteryRecoveredTitle;

  /// No description provided for @notifBatteryRecoveredBody.
  ///
  /// In en, this message translates to:
  /// **'Battery SOC recovered to {soc}%'**
  String notifBatteryRecoveredBody(String soc);

  /// No description provided for @notifModeChangedTitle.
  ///
  /// In en, this message translates to:
  /// **'HEMS Mode Changed'**
  String get notifModeChangedTitle;

  /// No description provided for @notifModeChangedBody.
  ///
  /// In en, this message translates to:
  /// **'Active mode: {mode}'**
  String notifModeChangedBody(String mode);

  /// No description provided for @notifGridInstabilityTitle.
  ///
  /// In en, this message translates to:
  /// **'Grid Instability'**
  String get notifGridInstabilityTitle;

  /// No description provided for @notifGridInstabilityBody.
  ///
  /// In en, this message translates to:
  /// **'Frequent grid state changes detected'**
  String get notifGridInstabilityBody;

  /// No description provided for @notifAutoStormTitle.
  ///
  /// In en, this message translates to:
  /// **'Auto Storm Mode Activated'**
  String get notifAutoStormTitle;

  /// No description provided for @notifAutoStormBody.
  ///
  /// In en, this message translates to:
  /// **'Grid outage detected — switched to Storm mode to protect battery'**
  String get notifAutoStormBody;

  /// No description provided for @notifForecastStormTitle.
  ///
  /// In en, this message translates to:
  /// **'Storm Mode — Weather Alert'**
  String get notifForecastStormTitle;

  /// No description provided for @notifForecastStormBody.
  ///
  /// In en, this message translates to:
  /// **'Bad weather ahead: {reason} — switched to Storm mode'**
  String notifForecastStormBody(String reason);

  /// No description provided for @notifForecastStormRestoredTitle.
  ///
  /// In en, this message translates to:
  /// **'Adaptive Mode Restored'**
  String get notifForecastStormRestoredTitle;

  /// No description provided for @notifForecastStormRestoredBody.
  ///
  /// In en, this message translates to:
  /// **'Weather risk cleared — restored to normal HEMS mode'**
  String get notifForecastStormRestoredBody;

  /// No description provided for @notifAutoAdaptiveTitle.
  ///
  /// In en, this message translates to:
  /// **'Adaptive Mode Restored'**
  String get notifAutoAdaptiveTitle;

  /// No description provided for @notifAutoAdaptiveBody.
  ///
  /// In en, this message translates to:
  /// **'Grid is back — restored to Adaptive mode automatically'**
  String get notifAutoAdaptiveBody;

  /// No description provided for @notifAnomalyTitle.
  ///
  /// In en, this message translates to:
  /// **'High Consumption Detected'**
  String get notifAnomalyTitle;

  /// No description provided for @notifAnomalyBody.
  ///
  /// In en, this message translates to:
  /// **'Load is {load} W — {times}× above normal for this hour'**
  String notifAnomalyBody(String load, String times);

  /// No description provided for @notifCycleTitle.
  ///
  /// In en, this message translates to:
  /// **'Battery Cycle Completed'**
  String get notifCycleTitle;

  /// No description provided for @notifCycleBody.
  ///
  /// In en, this message translates to:
  /// **'Total cycles: {count}. Estimated battery health: {soh}%'**
  String notifCycleBody(String count, String soh);

  /// No description provided for @eventHistoryTitle.
  ///
  /// In en, this message translates to:
  /// **'Event History'**
  String get eventHistoryTitle;

  /// No description provided for @eventHistoryEmpty.
  ///
  /// In en, this message translates to:
  /// **'No events recorded yet'**
  String get eventHistoryEmpty;

  /// No description provided for @eventHistoryClear.
  ///
  /// In en, this message translates to:
  /// **'Clear history'**
  String get eventHistoryClear;

  /// No description provided for @eventHistoryShowAll.
  ///
  /// In en, this message translates to:
  /// **'Show all'**
  String get eventHistoryShowAll;

  /// No description provided for @exportCsv.
  ///
  /// In en, this message translates to:
  /// **'Export CSV'**
  String get exportCsv;

  /// No description provided for @exportedTo.
  ///
  /// In en, this message translates to:
  /// **'Saved to:\n{path}'**
  String exportedTo(String path);

  /// No description provided for @exportFailed.
  ///
  /// In en, this message translates to:
  /// **'Export failed'**
  String get exportFailed;

  /// No description provided for @currentCostPerHour.
  ///
  /// In en, this message translates to:
  /// **'Cost now'**
  String get currentCostPerHour;

  /// No description provided for @uahPerHour.
  ///
  /// In en, this message translates to:
  /// **'₴/h'**
  String get uahPerHour;

  /// No description provided for @batteryCycles.
  ///
  /// In en, this message translates to:
  /// **'Cycles'**
  String get batteryCycles;

  /// No description provided for @batteryHealth.
  ///
  /// In en, this message translates to:
  /// **'Battery health'**
  String get batteryHealth;

  /// No description provided for @battSohPercent.
  ///
  /// In en, this message translates to:
  /// **'SOH {soh}%'**
  String battSohPercent(String soh);

  /// No description provided for @resetCycleCount.
  ///
  /// In en, this message translates to:
  /// **'Reset counter'**
  String get resetCycleCount;

  /// No description provided for @gridAutoStormNote.
  ///
  /// In en, this message translates to:
  /// **'Auto Storm mode was activated due to grid outage'**
  String get gridAutoStormNote;

  /// No description provided for @autoStormByForecastTitle.
  ///
  /// In en, this message translates to:
  /// **'Auto Storm mode by weather forecast'**
  String get autoStormByForecastTitle;

  /// No description provided for @autoStormByForecastEnabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Switches to Storm mode when bad weather is predicted (next 12 h)'**
  String get autoStormByForecastEnabledSubtitle;

  /// No description provided for @autoStormByForecastDisabledSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Forecast-based Storm mode is off'**
  String get autoStormByForecastDisabledSubtitle;

  /// No description provided for @socHistoryTitle.
  ///
  /// In en, this message translates to:
  /// **'Battery SOC — last 24 h'**
  String get socHistoryTitle;

  /// No description provided for @socHistorySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Charge level trend'**
  String get socHistorySubtitle;

  /// No description provided for @socHistoryNoData.
  ///
  /// In en, this message translates to:
  /// **'Not enough data yet.\nData is collected every 5 minutes.'**
  String get socHistoryNoData;

  /// No description provided for @socHistoryReserveLabel.
  ///
  /// In en, this message translates to:
  /// **'Reserve'**
  String get socHistoryReserveLabel;

  /// No description provided for @socHistoryChargingLabel.
  ///
  /// In en, this message translates to:
  /// **'Charging'**
  String get socHistoryChargingLabel;

  /// No description provided for @socHistoryDischargingLabel.
  ///
  /// In en, this message translates to:
  /// **'Discharging'**
  String get socHistoryDischargingLabel;

  /// No description provided for @socHistoryIdleLabel.
  ///
  /// In en, this message translates to:
  /// **'Idle'**
  String get socHistoryIdleLabel;

  /// No description provided for @scheduleRulesTitle.
  ///
  /// In en, this message translates to:
  /// **'Schedule Rules'**
  String get scheduleRulesTitle;

  /// No description provided for @scheduleRulesSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Force a HEMS mode during a recurring time window'**
  String get scheduleRulesSubtitle;

  /// No description provided for @scheduleRulesEmpty.
  ///
  /// In en, this message translates to:
  /// **'No schedule rules yet.\nTap + to add your first rule.'**
  String get scheduleRulesEmpty;

  /// No description provided for @scheduleRuleAdd.
  ///
  /// In en, this message translates to:
  /// **'Add Rule'**
  String get scheduleRuleAdd;

  /// No description provided for @scheduleRuleEdit.
  ///
  /// In en, this message translates to:
  /// **'Edit Rule'**
  String get scheduleRuleEdit;

  /// No description provided for @scheduleRuleDelete.
  ///
  /// In en, this message translates to:
  /// **'Delete Rule'**
  String get scheduleRuleDelete;

  /// No description provided for @scheduleRuleDeleteConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete rule \"{name}\"?'**
  String scheduleRuleDeleteConfirm(String name);

  /// No description provided for @scheduleRuleName.
  ///
  /// In en, this message translates to:
  /// **'Rule name'**
  String get scheduleRuleName;

  /// No description provided for @scheduleRuleNameHint.
  ///
  /// In en, this message translates to:
  /// **'E.g. Morning solar, Weekend charge…'**
  String get scheduleRuleNameHint;

  /// No description provided for @scheduleRuleDays.
  ///
  /// In en, this message translates to:
  /// **'Active days'**
  String get scheduleRuleDays;

  /// No description provided for @scheduleRuleStartTime.
  ///
  /// In en, this message translates to:
  /// **'Start time'**
  String get scheduleRuleStartTime;

  /// No description provided for @scheduleRuleEndTime.
  ///
  /// In en, this message translates to:
  /// **'End time'**
  String get scheduleRuleEndTime;

  /// No description provided for @scheduleRuleMode.
  ///
  /// In en, this message translates to:
  /// **'Forced HEMS mode'**
  String get scheduleRuleMode;

  /// No description provided for @scheduleRuleModeAdaptive.
  ///
  /// In en, this message translates to:
  /// **'Adaptive (Auto)'**
  String get scheduleRuleModeAdaptive;

  /// No description provided for @scheduleRuleModeArbitrage.
  ///
  /// In en, this message translates to:
  /// **'Night Arbitrage'**
  String get scheduleRuleModeArbitrage;

  /// No description provided for @scheduleRuleModeStorm.
  ///
  /// In en, this message translates to:
  /// **'Storm / Reserve'**
  String get scheduleRuleModeStorm;

  /// No description provided for @scheduleRuleActive.
  ///
  /// In en, this message translates to:
  /// **'Active now'**
  String get scheduleRuleActive;

  /// No description provided for @scheduleRuleNameEmpty.
  ///
  /// In en, this message translates to:
  /// **'Please enter a name'**
  String get scheduleRuleNameEmpty;

  /// No description provided for @scheduleRuleNoDays.
  ///
  /// In en, this message translates to:
  /// **'Select at least one day'**
  String get scheduleRuleNoDays;

  /// No description provided for @scheduleRulePriority.
  ///
  /// In en, this message translates to:
  /// **'Priority'**
  String get scheduleRulePriority;

  /// No description provided for @scheduleRulePriorityHint.
  ///
  /// In en, this message translates to:
  /// **'Higher priority wins when rules overlap'**
  String get scheduleRulePriorityHint;

  /// No description provided for @scheduleRuleConflict.
  ///
  /// In en, this message translates to:
  /// **'{count} rules overlap now — using highest priority'**
  String scheduleRuleConflict(int count);
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

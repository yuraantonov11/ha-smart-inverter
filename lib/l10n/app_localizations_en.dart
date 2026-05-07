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
  String get lightTheme => 'Light theme';

  @override
  String get darkTheme => 'Dark theme';

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
  String get forecast => 'Forecast';

  @override
  String get modeAdaptive => 'Adaptive Intelligence (Auto)';

  @override
  String get modeAdaptiveSubtitle =>
      'Dynamic mode: forecast, tariff, and battery state';

  @override
  String get modeAdaptiveDesc =>
      'Adaptive mode continuously analyzes:\n• real-time PV generation and house consumption\n• solar forecast for the rest of the day\n• battery SOC and safety reserve\n• time-of-use tariff windows (night/day/evening)\n\nWhat it does:\n• At night, it predicts whether tomorrow\'s solar will be enough and decides whether grid charging is needed\n• During daytime, it optimizes source priority to preserve energy for evening peak\n• In evening hours, it uses battery energy down to the configured reserve threshold\n• It automatically falls back to grid if battery safety or supply stability is at risk\n\nResult: fewer manual switches, lower energy cost, and more stable day-to-day operation.';

  @override
  String get modeArbitrage => 'Night Arbitrage';

  @override
  String get modeArbitrageSubtitle =>
      'Strict cost optimization by tariff windows';

  @override
  String get modeArbitrageDesc =>
      'This mode prioritizes minimum electricity cost:\n• During night tariff, it forces grid operation and charges battery\n• During day/evening, it prioritizes battery and solar\n• Grid charging is disabled outside night tariff\n\nBest when cost saving is the primary goal, even with more battery cycling.';

  @override
  String get modeStorm => 'Reserve / Storm';

  @override
  String get modeStormSubtitle => 'Maximum backup readiness for outages';

  @override
  String get modeStormDesc =>
      'This mode prioritizes reliability over savings:\n• Keeps battery as full as possible\n• Powers home from grid to preserve battery for emergency use\n• Ignores tariff optimization when reserve readiness is critical\n\nRecommended before storms, unstable grid periods, or expected long outages.';

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
  String get hemsSubtitle =>
      'Choose your strategy: adaptive automation, savings, or backup reserve';

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
  String get startInTray => 'Start in tray';

  @override
  String get startInTraySubtitle =>
      'Starts minimized when a saved session exists';

  @override
  String get name => 'Name';

  @override
  String get notProvided => 'Not provided';

  @override
  String get unknownValue => 'Unknown';

  @override
  String get accountStatusLocal => 'Local profile only';

  @override
  String get accountStatusSynced => 'Cloud profile synced';

  @override
  String get cloudAccount => 'Cloud account';

  @override
  String get phoneLabel => 'Phone';

  @override
  String get sessionId => 'Session ID';

  @override
  String get accountProfileHint =>
      'Profile data is used for account display and inverter cloud access.';

  @override
  String get logoutConfirmMessage =>
      'Are you sure you want to sign out from this device?';

  @override
  String get nameCannotBeEmpty => 'Name cannot be empty';

  @override
  String get copy => 'Copy';

  @override
  String get copiedToClipboard => 'Copied to clipboard';

  @override
  String get updatesTitle => 'Updates';

  @override
  String get updatesCheckingBackground => 'Checking updates in background...';

  @override
  String get updatesChecking => 'Checking for updates...';

  @override
  String get updatesSubtitleDefault => 'Check and install latest version';

  @override
  String updatesSubtitleAvailable(String version) {
    return 'New version $version is available';
  }

  @override
  String updatesSubtitleSkipped(String version) {
    return 'Version $version is skipped';
  }

  @override
  String updatesSubtitleUpToDate(String version) {
    return 'You are up to date ($version)';
  }

  @override
  String updatesLastChecked(String time) {
    return 'Last checked: $time';
  }

  @override
  String updatesSkippedBanner(String version) {
    return 'Version $version is currently skipped.';
  }

  @override
  String get updatesSkippedRestored => 'Skipped version restored.';

  @override
  String get updatesRestore => 'Restore';

  @override
  String updatesBannerAvailable(String version) {
    return 'New update $version is available';
  }

  @override
  String updatesCurrentVersion(String version) {
    return 'Current version: $version';
  }

  @override
  String get updatesView => 'View';

  @override
  String get updatesSkip => 'Skip';

  @override
  String updatesSkippedNow(String version) {
    return 'Version $version was skipped.';
  }

  @override
  String get updatesNoInstallerFound =>
      'No compatible installer was found in this release.';

  @override
  String get updatesDialogAvailableTitle => 'Update Available';

  @override
  String updatesDialogCurrent(String version) {
    return 'Current: $version';
  }

  @override
  String updatesDialogLatest(String version) {
    return 'Latest: $version';
  }

  @override
  String updatesDialogPublished(String time) {
    return 'Published: $time';
  }

  @override
  String updatesDialogPackage(String name) {
    return 'Package: $name';
  }

  @override
  String get updatesDialogSkipVersion => 'Skip this version';

  @override
  String get updatesDialogLater => 'Later';

  @override
  String get updatesDialogDownload => 'Download';

  @override
  String get updatesDialogDownloadingTitle => 'Downloading update';

  @override
  String get updatesDialogDownloadFailedTitle => 'Download failed';

  @override
  String get updatesDialogPreparing => 'Preparing download...';

  @override
  String get updatesDialogInstallTitle => 'Install Update';

  @override
  String updatesDialogInstallPrompt(String version) {
    return 'Update downloaded ($version). Install now? The app will close during installation.';
  }

  @override
  String get updatesDialogInstall => 'Install';

  @override
  String get updatesDialogInstallFailed =>
      'Installation failed. Please run installer manually.';

  @override
  String get updatesDialogDownloadFailed =>
      'Download failed. Check internet or release assets.';

  @override
  String get updatesDialogClose => 'Close';

  @override
  String get inverterSettings => 'Inverter settings';

  @override
  String get refreshSettings => 'Refresh settings';

  @override
  String get settingsLoadingTitle => 'Settings are loading…';

  @override
  String get waitingInverterResponse => 'Waiting for inverter response…';

  @override
  String get tapRefreshToLoad => 'Tap refresh to load settings';

  @override
  String get realtimeReadings => 'Realtime readings';

  @override
  String unitOfMeasure(String unit) {
    return 'Unit: $unit';
  }

  @override
  String get selectValue => 'Select value';

  @override
  String get newValue => 'New value';

  @override
  String get apply => 'Apply';

  @override
  String get debugLogs => 'Debug Logs';

  @override
  String get viewSystemLogs => 'View system logs';

  @override
  String get analyzeSystemLogs => 'Analyze app errors and API calls';

  @override
  String get stationParameters => 'Station parameters';

  @override
  String get stationParametersHint =>
      'These values help the intelligent algorithm calculate energy balance and weather-based forecasts more accurately.';

  @override
  String get batteryCapacityLabel => 'Battery capacity';

  @override
  String get panelPowerLabel => 'Panel power';

  @override
  String get inverterPowerLabel => 'Inverter power';

  @override
  String get locationPreset => 'Location preset';

  @override
  String get latitudeLabel => 'Latitude';

  @override
  String get longitudeLabel => 'Longitude';

  @override
  String get timeZoneLabel => 'Time zone';

  @override
  String get astronomicalWindowsTitle =>
      'Astronomical windows (sunrise/sunset)';

  @override
  String get astronomicalWindowsAutoSubtitle =>
      'Auto windows from geo coordinates';

  @override
  String get astronomicalWindowsManualSubtitle => 'Use manual hour boundaries';

  @override
  String get manualDayStartHour => 'Manual day start hour';

  @override
  String get manualEveningStartHour => 'Manual evening start hour';

  @override
  String get manualNightStartHour => 'Manual night start hour';

  @override
  String get geoPresetKyiv => 'Kyiv, UA';

  @override
  String get geoPresetLviv => 'Lviv, UA';

  @override
  String get geoPresetOdesa => 'Odesa, UA';

  @override
  String get geoPresetDnipro => 'Dnipro, UA';

  @override
  String get geoPresetKharkiv => 'Kharkiv, UA';

  @override
  String get geoPresetWarsaw => 'Warsaw, PL';

  @override
  String get geoPresetBerlin => 'Berlin, DE';

  @override
  String get geoPresetCustom => 'Custom (manual)';

  @override
  String geoSummary(String latitude, String longitude, String timeZone) {
    return 'Geo: $latitude, $longitude ($timeZone)';
  }

  @override
  String get windowsAstronomicalAuto => 'Windows: astronomical (auto)';

  @override
  String windowsManualSummary(String day, String evening, String night) {
    return 'Windows: manual $day:00 / $evening:00 / $night:00';
  }

  @override
  String get hardwareSettingsSaved => 'Hardware settings saved!';

  @override
  String hardwareSummary(String battery, String pv, String inverter) {
    return 'Battery: $battery Ah • PV: $pv W\nInverter: $inverter W';
  }

  @override
  String get logsTitle => 'App Logs';

  @override
  String get logsAll => 'All';

  @override
  String get logsInfo => 'Info';

  @override
  String get logsWarn => 'Warn';

  @override
  String get logsError => 'Error';

  @override
  String get logsNoEntries => 'No logs yet.';

  @override
  String get logsErrorPrefix => 'Error';

  @override
  String get logsCopyFiltered => 'Copy filtered';

  @override
  String get clear => 'Clear';

  @override
  String get logsCopied => 'Logs copied to clipboard';

  @override
  String logsSummary(String total, String info, String warn, String error) {
    return 'Total: $total  |  Info: $info  Warn: $warn  Error: $error';
  }

  @override
  String get solarSbu => 'SOLAR (SBU)';

  @override
  String get gridUsb => 'GRID (USB)';

  @override
  String get modeFromSolar => 'From solar';

  @override
  String get modeFromGrid => 'From grid';

  @override
  String get utilityFirstUsb => 'Utility First (USB)';

  @override
  String get solarFirstSub => 'Solar First (SUB)';

  @override
  String get solarUtilitySnu => 'Solar + Utility (SNU)';

  @override
  String get presetDisabled => 'Disabled';

  @override
  String get presetEnabled => 'Enabled';

  @override
  String get presetOutputUsb => 'USB - Grid priority';

  @override
  String get presetOutputSub => 'SUB - Solar first';

  @override
  String get presetOutputSbu => 'SBU - Solar/Battery priority';

  @override
  String get presetChargerCso => 'CSO - Solar first';

  @override
  String get presetChargerSnu => 'SNU - Solar + Utility';

  @override
  String get presetChargerOso => 'OSO - Solar only';

  @override
  String get presetChargerUtilityOnly => 'Utility only';

  @override
  String get presetFrequency50Hz => '50 Hz';

  @override
  String get presetFrequency60Hz => '60 Hz';

  @override
  String get presetAcInputAplWide => 'APL - Wide range';

  @override
  String get presetAcInputUpsNarrow => 'UPS - Narrow range';

  @override
  String get presetBatteryTypeAgm => 'AGM';

  @override
  String get presetBatteryTypeFlooded => 'Flooded';

  @override
  String get presetBatteryTypeUser => 'User-defined';

  @override
  String get presetBatteryTypeLib => 'LIB (Lithium)';

  @override
  String get presetBatteryTypeLife => 'LiFe';

  @override
  String get signInCloud => 'Sign in to Siseli Cloud';

  @override
  String get loginFailed => 'Login failed. Check credentials.';

  @override
  String get today => 'Today';

  @override
  String get forecastNextDays => 'Solar forecast for next days';

  @override
  String get forecastPeak => 'Peak';

  @override
  String get forecastUnavailable => 'Forecast data is temporarily unavailable';

  @override
  String get equipmentStatus => 'Equipment status';

  @override
  String get inverterLoad => 'Inverter load';

  @override
  String get pvGeneration => 'PV generation';

  @override
  String get refreshChart => 'Refresh chart';

  @override
  String get batterySignHint =>
      'Battery: \'+\' means charge, \'-\' means discharge.';

  @override
  String get chartNoDataTitle => 'No data yet';

  @override
  String get chartNoDataMessage => 'The chart should load in a moment.';

  @override
  String get lessThanMinute => '< 1 min';

  @override
  String minutesAgo(String count) {
    return '$count min ago';
  }

  @override
  String hoursAgo(String count) {
    return '$count hr ago';
  }

  @override
  String get total => 'Total';

  @override
  String get solar => 'Solar';

  @override
  String get load => 'Load';

  @override
  String get connectionOnline => 'Online';

  @override
  String get connectionOffline => 'Offline';

  @override
  String get lastRealtimeUpdate => 'Last update';

  @override
  String updatedAt(String time) {
    return 'Updated at $time';
  }

  @override
  String get updateFailed => 'Update failed';

  @override
  String get retry => 'Retry';

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
  String get tooltipTodayEnergy => 'Total solar energy generated today';

  @override
  String get tooltipTotalEnergy =>
      'Total solar energy generated since device installation';

  @override
  String get tooltipCo2 =>
      'Estimated CO₂ emission reduction based on solar generation';

  @override
  String get tooltipInverterLoad =>
      'Current inverter load relative to its rated power capacity';

  @override
  String get tooltipPvGeneration =>
      'Current PV output relative to total installed panel capacity';

  @override
  String batteryLevel(String level) {
    return 'Battery: $level%';
  }

  @override
  String get batteryInstallYearLabel => 'Battery installation year';

  @override
  String get hemsStrategyLabel => 'HEMS optimization strategy';

  @override
  String get hemsStrategyEconomical => 'Economical (minimize grid cost)';

  @override
  String get hemsStrategySolarMaxed =>
      'Solar Maxed (maximize self-consumption)';

  @override
  String get hemsStrategyBatteryLife => 'Battery Life (conservative cycles)';

  @override
  String get hemsStrategyGridReliance =>
      'Grid Reliance (resilience / off-grid)';

  @override
  String get hemsStrategyHybrid => 'Hybrid (balanced — recommended)';

  @override
  String get moneySavedMonth => 'Saved this month';

  @override
  String get paymentThisMonth => 'To pay this month';

  @override
  String get currencyUah => 'UAH';

  @override
  String get energyTariffLabel => 'Electricity tariff';

  @override
  String get energyTariffDayLabel => 'Day tariff';

  @override
  String get energyTariffNightLabel => 'Night tariff';

  @override
  String get energyTariffUnit => 'UAH/kWh';

  @override
  String get nightEnergyShareLabel => 'Night energy share';

  @override
  String get nightEnergyShareUnit => '%';

  @override
  String get autoEstimateNightShare => 'Auto estimate night share';

  @override
  String get batteryRoundTripEfficiencyLabel => 'Battery round-trip efficiency';

  @override
  String get batteryRoundTripEfficiencyHint =>
      'Used to reduce savings from battery-shifted energy. Set 100% to disable battery-loss correction.';

  @override
  String get nightShareFallbackHint =>
      'Used only as a fallback when hourly telemetry economics is unavailable.';

  @override
  String economicsMethodTelemetry(String efficiency) {
    return 'Hourly telemetry • Battery efficiency $efficiency%';
  }

  @override
  String economicsMethodEstimated(String share) {
    return 'Fallback estimate • Night share $share%';
  }

  @override
  String get calculationSourceLabel => 'Calculation source';

  @override
  String get calculationAccuracyLabel => 'Accuracy';

  @override
  String get calculationSourceTelemetry => 'Hourly telemetry';

  @override
  String get calculationSourceFallback => 'Fallback estimate';

  @override
  String get calculationAccuracyHigh => 'High';

  @override
  String get calculationAccuracyEstimated => 'Estimated';

  @override
  String effectiveTariffFormula(String day, String night, String share) {
    return 'Formula: $day*(1-$share%) + $night*$share%';
  }

  @override
  String tooltipMoneySavedMonthTelemetry(String efficiency) {
    return 'Calculated from telemetry with hourly day/night tariff. Battery-shifted energy is adjusted by $efficiency% round-trip efficiency.';
  }

  @override
  String tooltipMoneySavedMonthEstimated(String share, String efficiency) {
    return 'Estimated from self-consumed solar using fallback night share $share% and battery efficiency $efficiency%.';
  }

  @override
  String tooltipPaymentThisMonthTelemetry(String dayStart, String nightStart) {
    return 'Calculated from telemetry with hourly tariff windows: day from $dayStart:00, night from $nightStart:00.';
  }

  @override
  String tooltipPaymentThisMonthEstimated(String share) {
    return 'Estimated monthly bill using fallback night share $share% for day/night tariff split.';
  }

  @override
  String tooltipEffectiveTariffTelemetry(String dayStart, String nightStart) {
    return 'Reference blended tariff only. Actual payment and savings above are calculated hourly from telemetry using day from $dayStart:00 and night from $nightStart:00.';
  }

  @override
  String get tooltipMoneySavedMonth =>
      'Estimated money saved this month from self-consumed solar energy';

  @override
  String get tooltipPaymentThisMonth =>
      'Estimated monthly bill for imported grid energy';

  @override
  String get projectedSavedMonth => 'Projected saved by month end';

  @override
  String get projectedPaymentMonth => 'Projected payment by month end';

  @override
  String get tooltipProjectedSavedMonth =>
      'Forecast based on current month trend of self-consumed solar energy';

  @override
  String get tooltipProjectedPaymentMonth =>
      'Forecast based on current month trend of imported grid energy';

  @override
  String get monthlyEnergyBreakdown => 'Monthly energy breakdown';

  @override
  String get monthLoadEnergy => 'Load this month';

  @override
  String get monthGridImport => 'Grid import';

  @override
  String get monthSelfConsumed => 'Self-consumed solar';

  @override
  String get monthGridCost => 'Grid cost this month';

  @override
  String get monthSavedCost => 'Saved cost this month';

  @override
  String get monthEffectiveTariff => 'Effective tariff';

  @override
  String get tooltipMonthProgress => 'Current month progress';

  @override
  String get plannedOutageTitle => 'Planned outage alert';

  @override
  String get plannedOutageEnabledSubtitle => 'Auto-precharge before outage';

  @override
  String get plannedOutageDisabledSubtitle => 'No manual outage schedule';

  @override
  String get plannedOutageStartLabel => 'Outage start';

  @override
  String get plannedOutageEndLabel => 'Outage end';
}

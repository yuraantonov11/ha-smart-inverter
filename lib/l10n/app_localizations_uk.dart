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
  String get lightTheme => 'Світла тема';

  @override
  String get darkTheme => 'Темна тема';

  @override
  String get language => 'Мова';

  @override
  String get dashboard => 'Дашборд';

  @override
  String get details => 'Деталі';

  @override
  String get automation => 'Автоматика';

  @override
  String get data => 'Дані';

  @override
  String get smartModes => 'Смарт Режими';

  @override
  String get modeOff => 'Вимкнення';

  @override
  String get modeWinter => 'Зимовий Режим';

  @override
  String get modeWinterDesc => 'Опис для Зимового Режиму';

  @override
  String get modeSummer => 'Літній Режим';

  @override
  String get modeSummerDesc => 'Опис для Літнього Режиму';

  @override
  String get energyOverview => 'Огляд енергії';

  @override
  String get production => 'Виробництво';

  @override
  String get consumption => 'Споживання';

  @override
  String get day => 'День';

  @override
  String get week => 'Тиждень';

  @override
  String get month => 'Місяць';

  @override
  String get mon => 'Пн';

  @override
  String get tue => 'Вт';

  @override
  String get wed => 'Ср';

  @override
  String get thu => 'Чт';

  @override
  String get fri => 'Пт';

  @override
  String get sat => 'Сб';

  @override
  String get sun => 'Нд';

  @override
  String get battery => 'Батарея';

  @override
  String get grid => 'Мережа';

  @override
  String get forecast => 'Прогноз';

  @override
  String get modeAdaptive => 'Адаптивний інтелект (Авто)';

  @override
  String get modeAdaptiveSubtitle =>
      'Динамічний режим: прогноз, тариф і стан АКБ';

  @override
  String get modeAdaptiveDesc =>
      'Адаптивний режим щохвилини аналізує:\n• поточну генерацію PV і споживання будинку\n• прогноз сонячної генерації на день\n• заряд АКБ та резерв безпеки\n• часові зони тарифу (ніч/день/вечір)\n\nЩо робить режим:\n• Вночі оцінює, чи вистачить сонця наступного дня, і вирішує: заряджати АКБ від мережі чи ні\n• Вдень перемикає пріоритет так, щоб зберегти енергію на вечірній пік\n• Увечері максимально використовує АКБ до резервного порогу\n• Автоматично повертає живлення від мережі, якщо є ризик просадки АКБ\n\nРезультат: мінімум ручних перемикань, нижча вартість електроенергії та стабільніша робота системи.';

  @override
  String get modeArbitrage => 'Нічний арбітраж';

  @override
  String get modeArbitrageSubtitle => 'Жорстка економія за часовими тарифами';

  @override
  String get modeArbitrageDesc =>
      'Режим орієнтований на мінімальну ціну електроенергії:\n• У нічний тариф примусово працює від мережі та заряджає АКБ\n• У денний/вечірній період віддає пріоритет АКБ і сонцю\n• Зарядка від мережі вдень вимикається\n\nПідійде, якщо головна ціль — економія, навіть ціною більш частих циклів АКБ.';

  @override
  String get modeStorm => 'Резерв / Шторм';

  @override
  String get modeStormSubtitle => 'Максимальний резерв на випадок відключень';

  @override
  String get modeStormDesc =>
      'Режим пріоритету надійності:\n• Система тримає АКБ максимально зарядженою\n• Живлення будинку від мережі для збереження ресурсу АКБ перед аваріями\n• Економія тарифу не є пріоритетом\n\nРекомендовано перед штормами, нестабільною мережею або при ризику тривалих відключень.';

  @override
  String get account => 'Акаунт';

  @override
  String get appSettings => 'Налаштування застосунку';

  @override
  String get editProfile => 'Редагувати профіль';

  @override
  String get cancel => 'Скасувати';

  @override
  String get save => 'Зберегти';

  @override
  String get userNameDefault => 'Користувач';

  @override
  String userId(String id) {
    return 'ID: $id';
  }

  @override
  String get inverterMode => 'Режим інвертора';

  @override
  String get advancedSettings => 'Розширені налаштування';

  @override
  String get outputSourcePriority => 'Пріоритет виходу (Output)';

  @override
  String get chargerSourcePriority => 'Пріоритет зарядки (Charger)';

  @override
  String get sbuPriority => 'SBU Priority';

  @override
  String get onlySolar => 'Тільки Сонце (OSO)';

  @override
  String get solarFirst => 'Сонце в першу чергу (CSO)';

  @override
  String get hemsTitle => 'Інтелектуальні HEMS режими';

  @override
  String get hemsSubtitle =>
      'Оберіть стратегію: адаптивна автоматизація, економія або резерв';

  @override
  String get gotIt => 'Зрозуміло';

  @override
  String get welcomeBack => 'З поверненням';

  @override
  String get email => 'Електронна пошта';

  @override
  String get password => 'Пароль';

  @override
  String get loginError => 'Неправильний логін або пароль';

  @override
  String get startWithWindows => 'Автозапуск з Windows';

  @override
  String get startInTray => 'Запускати в треї';

  @override
  String get startInTraySubtitle => 'Стартує згорнутим, якщо є збережена сесія';

  @override
  String get name => 'Ім\'я';

  @override
  String get notProvided => 'Не вказано';

  @override
  String get unknownValue => 'Невідомо';

  @override
  String get accountStatusLocal => 'Локальний профіль';

  @override
  String get accountStatusSynced => 'Профіль синхронізовано';

  @override
  String get cloudAccount => 'Cloud акаунт';

  @override
  String get phoneLabel => 'Телефон';

  @override
  String get sessionId => 'ID сесії';

  @override
  String get accountProfileHint =>
      'Дані профілю використовуються для відображення акаунта та доступу до хмари інвертора.';

  @override
  String get logoutConfirmMessage =>
      'Ви впевнені, що хочете вийти на цьому пристрої?';

  @override
  String get nameCannotBeEmpty => 'Ім\'я не може бути порожнім';

  @override
  String get copy => 'Копіювати';

  @override
  String get copiedToClipboard => 'Скопійовано в буфер обміну';

  @override
  String get updatesTitle => 'Оновлення';

  @override
  String get updatesCheckingBackground => 'Перевірка оновлень у фоні...';

  @override
  String get updatesChecking => 'Перевірка оновлень...';

  @override
  String get updatesSubtitleDefault =>
      'Перевірити та встановити останню версію';

  @override
  String updatesSubtitleAvailable(String version) {
    return 'Доступна нова версія $version';
  }

  @override
  String updatesSubtitleSkipped(String version) {
    return 'Версію $version пропущено';
  }

  @override
  String updatesSubtitleUpToDate(String version) {
    return 'У вас остання версія ($version)';
  }

  @override
  String updatesLastChecked(String time) {
    return 'Остання перевірка: $time';
  }

  @override
  String updatesSkippedBanner(String version) {
    return 'Версію $version наразі пропущено.';
  }

  @override
  String get updatesSkippedRestored => 'Пропущену версію відновлено.';

  @override
  String get updatesRestore => 'Відновити';

  @override
  String updatesBannerAvailable(String version) {
    return 'Доступне оновлення $version';
  }

  @override
  String updatesCurrentVersion(String version) {
    return 'Поточна версія: $version';
  }

  @override
  String get updatesView => 'Переглянути';

  @override
  String get updatesSkip => 'Пропустити';

  @override
  String updatesSkippedNow(String version) {
    return 'Версію $version пропущено.';
  }

  @override
  String get updatesNoInstallerFound =>
      'У цьому релізі не знайдено сумісний інсталятор.';

  @override
  String get updatesDialogAvailableTitle => 'Доступне оновлення';

  @override
  String updatesDialogCurrent(String version) {
    return 'Поточна: $version';
  }

  @override
  String updatesDialogLatest(String version) {
    return 'Остання: $version';
  }

  @override
  String updatesDialogPublished(String time) {
    return 'Опубліковано: $time';
  }

  @override
  String updatesDialogPackage(String name) {
    return 'Пакет: $name';
  }

  @override
  String get updatesDialogSkipVersion => 'Пропустити цю версію';

  @override
  String get updatesDialogLater => 'Пізніше';

  @override
  String get updatesDialogDownload => 'Завантажити';

  @override
  String get updatesDialogDownloadingTitle => 'Завантаження оновлення';

  @override
  String get updatesDialogDownloadFailedTitle => 'Помилка завантаження';

  @override
  String get updatesDialogPreparing => 'Підготовка завантаження...';

  @override
  String get updatesDialogInstallTitle => 'Встановити оновлення';

  @override
  String updatesDialogInstallPrompt(String version) {
    return 'Оновлення завантажено ($version). Встановити зараз? Під час встановлення застосунок буде закрито.';
  }

  @override
  String get updatesDialogInstall => 'Встановити';

  @override
  String get updatesDialogInstallFailed =>
      'Не вдалося встановити. Запустіть інсталятор вручну.';

  @override
  String get updatesDialogDownloadFailed =>
      'Не вдалося завантажити. Перевірте інтернет або файли релізу.';

  @override
  String get updatesDialogClose => 'Закрити';

  @override
  String get inverterSettings => 'Налаштування інвертора';

  @override
  String get refreshSettings => 'Оновити налаштування';

  @override
  String get settingsLoadingTitle => 'Налаштування завантажуються…';

  @override
  String get waitingInverterResponse => 'Очікуємо відповідь інвертора…';

  @override
  String get tapRefreshToLoad => 'Натисніть 🔄 для завантаження';

  @override
  String get realtimeReadings => 'Поточні показники';

  @override
  String unitOfMeasure(String unit) {
    return 'Одиниця виміру: $unit';
  }

  @override
  String get selectValue => 'Оберіть значення';

  @override
  String get newValue => 'Нове значення';

  @override
  String get apply => 'Застосувати';

  @override
  String get debugLogs => 'Debug Logs';

  @override
  String get viewSystemLogs => 'Переглянути системні логи';

  @override
  String get analyzeSystemLogs => 'Аналіз помилок застосунку та API-викликів';

  @override
  String get stationParameters => 'Параметри станції';

  @override
  String get stationParametersHint =>
      'Ці дані потрібні інтелектуальному алгоритму для точного розрахунку енергії та прогнозу погоди.';

  @override
  String get batteryCapacityLabel => 'Ємність АКБ';

  @override
  String get panelPowerLabel => 'Потужність панелей';

  @override
  String get inverterPowerLabel => 'Потужність інвертора';

  @override
  String get locationPreset => 'Пресет локації';

  @override
  String get latitudeLabel => 'Широта';

  @override
  String get longitudeLabel => 'Довгота';

  @override
  String get timeZoneLabel => 'Часовий пояс';

  @override
  String get astronomicalWindowsTitle =>
      'Астрономічні вікна (схід/захід сонця)';

  @override
  String get astronomicalWindowsAutoSubtitle =>
      'Автовікна за географічними координатами';

  @override
  String get astronomicalWindowsManualSubtitle =>
      'Використовувати ручні часові межі';

  @override
  String get manualDayStartHour => 'Ручний старт дня';

  @override
  String get manualEveningStartHour => 'Ручний старт вечора';

  @override
  String get manualNightStartHour => 'Ручний старт ночі';

  @override
  String get geoPresetKyiv => 'Київ, UA';

  @override
  String get geoPresetLviv => 'Львів, UA';

  @override
  String get geoPresetOdesa => 'Одеса, UA';

  @override
  String get geoPresetDnipro => 'Дніпро, UA';

  @override
  String get geoPresetKharkiv => 'Харків, UA';

  @override
  String get geoPresetWarsaw => 'Варшава, PL';

  @override
  String get geoPresetBerlin => 'Берлін, DE';

  @override
  String get geoPresetCustom => 'Кастомно (вручну)';

  @override
  String geoSummary(String latitude, String longitude, String timeZone) {
    return 'Geo: $latitude, $longitude ($timeZone)';
  }

  @override
  String get windowsAstronomicalAuto => 'Вікна: астрономічні (авто)';

  @override
  String windowsManualSummary(String day, String evening, String night) {
    return 'Вікна: вручну $day:00 / $evening:00 / $night:00';
  }

  @override
  String get hardwareSettingsSaved => 'Параметри обладнання збережено!';

  @override
  String hardwareSummary(String battery, String pv, String inverter) {
    return 'АКБ: $battery Ah • PV: $pv W\nІнвертор: $inverter W';
  }

  @override
  String get logsTitle => 'Логи застосунку';

  @override
  String get logsAll => 'Усі';

  @override
  String get logsInfo => 'Інфо';

  @override
  String get logsWarn => 'Попередж.';

  @override
  String get logsError => 'Помилки';

  @override
  String get logsNoEntries => 'Логи відсутні.';

  @override
  String get logsErrorPrefix => 'Помилка';

  @override
  String get logsCopyFiltered => 'Копіювати фільтр';

  @override
  String get clear => 'Очистити';

  @override
  String get logsCopied => 'Логи скопійовано в буфер обміну';

  @override
  String logsSummary(String total, String info, String warn, String error) {
    return 'Всього: $total  |  Інфо: $info  Попередж.: $warn  Помилки: $error';
  }

  @override
  String get solarSbu => 'СОНЦЕ (SBU)';

  @override
  String get gridUsb => 'МЕРЕЖА (USB)';

  @override
  String get modeFromSolar => 'Від сонця';

  @override
  String get modeFromGrid => 'Від мережі';

  @override
  String get utilityFirstUsb => 'Мережа (Utility First / USB)';

  @override
  String get solarFirstSub => 'Сонце (Solar First / SUB)';

  @override
  String get solarUtilitySnu => 'Сонце + Мережа (SNU)';

  @override
  String get presetDisabled => 'Вимкнено';

  @override
  String get presetEnabled => 'Увімкнено';

  @override
  String get presetOutputUsb => 'USB - Пріоритет мережі';

  @override
  String get presetOutputSub => 'SUB - Пріоритет сонця';

  @override
  String get presetOutputSbu => 'SBU - Пріоритет сонця/АКБ';

  @override
  String get presetChargerCso => 'CSO - Спочатку сонце';

  @override
  String get presetChargerSnu => 'SNU - Сонце + Мережа';

  @override
  String get presetChargerOso => 'OSO - Тільки сонце';

  @override
  String get presetChargerUtilityOnly => 'Тільки мережа';

  @override
  String get presetFrequency50Hz => '50 Гц';

  @override
  String get presetFrequency60Hz => '60 Гц';

  @override
  String get presetAcInputAplWide => 'APL - Широкий діапазон';

  @override
  String get presetAcInputUpsNarrow => 'UPS - Вузький діапазон';

  @override
  String get presetBatteryTypeAgm => 'AGM';

  @override
  String get presetBatteryTypeFlooded => 'Flooded (залитий)';

  @override
  String get presetBatteryTypeUser => 'User (власні параметри)';

  @override
  String get presetBatteryTypeLib => 'LIB (літієвий)';

  @override
  String get presetBatteryTypeLife => 'LiFe';

  @override
  String get signInCloud => 'Увійдіть до Siseli Cloud';

  @override
  String get loginFailed => 'Помилка входу. Перевірте дані.';

  @override
  String get today => 'Сьогодні';

  @override
  String get forecastNextDays => 'Прогноз сонячної генерації на наступні дні';

  @override
  String get forecastPeak => 'Пік';

  @override
  String get forecastUnavailable => 'Дані прогнозу тимчасово недоступні';

  @override
  String get equipmentStatus => 'Статус обладнання';

  @override
  String get inverterLoad => 'Навантаження інвертора';

  @override
  String get pvGeneration => 'Генерація PV';

  @override
  String get refreshChart => 'Оновити графік';

  @override
  String get batterySignHint =>
      'АКБ: \'+\' означає заряд, \'-\' означає розряд.';

  @override
  String get chartNoDataTitle => 'Нема даних';

  @override
  String get chartNoDataMessage =>
      'Графік повинен завантажитися через деякий час';

  @override
  String get lessThanMinute => '< 1 хв';

  @override
  String minutesAgo(String count) {
    return '$count хв тому';
  }

  @override
  String hoursAgo(String count) {
    return '$count год тому';
  }

  @override
  String get total => 'Всього';

  @override
  String get solar => 'Сонце';

  @override
  String get load => 'Будинок';

  @override
  String get connectionOnline => 'Онлайн';

  @override
  String get connectionOffline => 'Офлайн';

  @override
  String get lastRealtimeUpdate => 'Останнє оновлення';

  @override
  String updatedAt(String time) {
    return 'Оновлено о $time';
  }

  @override
  String get updateFailed => 'Помилка оновлення';

  @override
  String get retry => 'Повторити';

  @override
  String get updated => 'Оновлено!';

  @override
  String get enableSolar => 'Увімкнути СОНЦЕ (SBU)';

  @override
  String get enableGrid => 'Увімкнути МЕРЕЖУ (USB)';

  @override
  String get showApp => 'Показати вікно';

  @override
  String get exit => 'Вийти';

  @override
  String get tooltipTodayEnergy =>
      'Загальна сонячна енергія, згенерована сьогодні';

  @override
  String get tooltipTotalEnergy =>
      'Загальна сонячна енергія з моменту встановлення пристрою';

  @override
  String get tooltipCo2 =>
      'Оцінка скорочення викидів CO₂ на основі сонячної генерації';

  @override
  String get tooltipInverterLoad =>
      'Поточне навантаження інвертора відносно номінальної потужності';

  @override
  String get tooltipPvGeneration =>
      'Поточна потужність PV-панелей відносно встановленої ємності';

  @override
  String batteryLevel(String level) {
    return 'АКБ: $level%';
  }

  @override
  String get batteryInstallYearLabel => 'Рік встановлення АКБ';

  @override
  String get hemsStrategyLabel => 'Стратегія оптимізації HEMS';

  @override
  String get hemsStrategyEconomical =>
      'Економна (мінімізація витрат на мережу)';

  @override
  String get hemsStrategySolarMaxed =>
      'Максимум сонця (максимум самоспоживання)';

  @override
  String get hemsStrategyBatteryLife =>
      'Довговічність АКБ (консервативні цикли)';

  @override
  String get hemsStrategyGridReliance =>
      'Автономність (резервний / автономний режим)';

  @override
  String get hemsStrategyHybrid => 'Гібридна (збалансована — рекомендовано)';

  @override
  String get moneySavedMonth => 'Зекономлено за місяць';

  @override
  String get paymentThisMonth => 'До оплати за місяць';

  @override
  String get currencyUah => 'грн';

  @override
  String get energyTariffLabel => 'Тариф на електроенергію';

  @override
  String get energyTariffDayLabel => 'Денний тариф';

  @override
  String get energyTariffNightLabel => 'Нічний тариф';

  @override
  String get energyTariffUnit => 'грн/кВт·год';

  @override
  String get nightEnergyShareLabel => 'Частка нічного споживання';

  @override
  String get nightEnergyShareUnit => '%';

  @override
  String get autoEstimateNightShare => 'Автооцінка нічної частки';

  @override
  String get batteryRoundTripEfficiencyLabel => 'ККД циклу акумулятора';

  @override
  String get batteryRoundTripEfficiencyHint =>
      'Використовується для зменшення економії від енергії, що пройшла через акумулятор. 100% вимикає поправку на втрати АКБ.';

  @override
  String get nightShareFallbackHint =>
      'Використовується лише як fallback, коли немає погодинної економіки з телеметрії.';

  @override
  String economicsMethodTelemetry(String efficiency) {
    return 'Погодинна телеметрія • ККД АКБ $efficiency%';
  }

  @override
  String economicsMethodEstimated(String share) {
    return 'Fallback-оцінка • Нічна частка $share%';
  }

  @override
  String get calculationSourceLabel => 'Джерело розрахунку';

  @override
  String get calculationAccuracyLabel => 'Точність';

  @override
  String get calculationSourceTelemetry => 'Погодинна телеметрія';

  @override
  String get calculationSourceFallback => 'Fallback-оцінка';

  @override
  String get calculationAccuracyHigh => 'Висока';

  @override
  String get calculationAccuracyEstimated => 'Оціночна';

  @override
  String effectiveTariffFormula(String day, String night, String share) {
    return 'Формула: $day*(1-$share%) + $night*$share%';
  }

  @override
  String tooltipMoneySavedMonthTelemetry(String efficiency) {
    return 'Розраховано з телеметрії по годинах з урахуванням денного/нічного тарифу. Енергія через акумулятор коригується ККД $efficiency%.';
  }

  @override
  String tooltipMoneySavedMonthEstimated(String share, String efficiency) {
    return 'Оціночна економія на основі власного сонячного споживання, fallback-нічної частки $share% та ККД АКБ $efficiency%.';
  }

  @override
  String tooltipPaymentThisMonthTelemetry(String dayStart, String nightStart) {
    return 'Розраховано з телеметрії по годинах: денний тариф з $dayStart:00, нічний з $nightStart:00.';
  }

  @override
  String tooltipPaymentThisMonthEstimated(String share) {
    return 'Оціночна сума до сплати з fallback-розподілом день/ніч за нічною часткою $share%.';
  }

  @override
  String tooltipEffectiveTariffTelemetry(String dayStart, String nightStart) {
    return 'Це лише довідковий усереднений тариф. Фактичні суми вище рахуються по годинах із телеметрії: день з $dayStart:00, ніч з $nightStart:00.';
  }

  @override
  String get tooltipMoneySavedMonth =>
      'Оціночна економія за місяць за рахунок власного сонячного споживання';

  @override
  String get tooltipPaymentThisMonth =>
      'Оціночна сума до сплати за мережеву електроенергію цього місяця';

  @override
  String get projectedSavedMonth => 'Прогноз економії до кінця місяця';

  @override
  String get projectedPaymentMonth => 'Прогноз оплати до кінця місяця';

  @override
  String get tooltipProjectedSavedMonth =>
      'Прогноз на основі поточного тренду власного сонячного споживання за місяць';

  @override
  String get tooltipProjectedPaymentMonth =>
      'Прогноз на основі поточного тренду споживання мережевої електроенергії за місяць';

  @override
  String get monthlyEnergyBreakdown => 'Структура енергії за місяць';

  @override
  String get monthLoadEnergy => 'Навантаження за місяць';

  @override
  String get monthGridImport => 'Імпорт з мережі';

  @override
  String get monthSelfConsumed => 'Власне сонячне споживання';

  @override
  String get monthGridCost => 'Вартість мережі за місяць';

  @override
  String get monthSavedCost => 'Зекономлена сума за місяць';

  @override
  String get monthEffectiveTariff => 'Ефективний тариф';

  @override
  String get tooltipMonthProgress => 'Поточний прогрес місяця';

  @override
  String get plannedOutageTitle => 'Попередження про планове відключення';

  @override
  String get plannedOutageEnabledSubtitle => 'Автопідзаряд перед відключенням';

  @override
  String get plannedOutageDisabledSubtitle =>
      'Немає ручного графіка відключень';

  @override
  String get plannedOutageStartLabel => 'Початок відключення';

  @override
  String get plannedOutageEndLabel => 'Кінець відключення';
}

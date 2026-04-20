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
  String get solarSbu => 'СОНЦЕ (SBU)';

  @override
  String get gridUsb => 'МЕРЕЖА (USB)';

  @override
  String get utilityFirstUsb => 'Мережа (Utility First / USB)';

  @override
  String get solarFirstSub => 'Сонце (Solar First / SUB)';

  @override
  String get solarUtilitySnu => 'Сонце + Мережа (SNU)';

  @override
  String get signInCloud => 'Увійдіть до Siseli Cloud';

  @override
  String get loginFailed => 'Помилка входу. Перевірте дані.';

  @override
  String get today => 'Сьогодні';

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
  String batteryLevel(String level) {
    return 'АКБ: $level%';
  }
}

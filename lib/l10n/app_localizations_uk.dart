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
  String get modeAdaptiveSubtitle => 'Баланс автономності та економії (SBU)';

  @override
  String get modeAdaptiveDesc =>
      'Інвертор самостійно маневрує між сонячною енергією та батареєю. Мережа використовується лише при критичному розряді.';

  @override
  String get modeArbitrage => 'Нічний арбітраж';

  @override
  String get modeArbitrageSubtitle => 'Максимальна фінансова економія';

  @override
  String get modeArbitrageDesc =>
      'О 23:00 інвертор плавно заряджає батарею за нічним тарифом. Вдень пріоритет віддається сонцю. Налаштовано динамічне відключення бойлера при низькому заряді.';

  @override
  String get modeStorm => 'Резерв / Шторм';

  @override
  String get modeStormSubtitle => 'Підготовка до віялових відключень';

  @override
  String get modeStormDesc =>
      'Фінансова економія ігнорується. Батарея примусово підтримується на 100% від мережі (режим USB). Готовність до непередбачуваних відключень.';

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
  String get hemsSubtitle => 'Оберіть стратегію керування енергією';

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
  String updatedAt(String time) {
    return 'Оновлено о $time';
  }

  @override
  String get updateFailed => 'Помилка оновлення';

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

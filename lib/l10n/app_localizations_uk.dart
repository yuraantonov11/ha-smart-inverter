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
  String get dashboard => 'Панель';

  @override
  String get details => 'Деталі';

  @override
  String get automation => 'Автоматизація';

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
}

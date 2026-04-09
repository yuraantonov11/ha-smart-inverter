import '../models/inverter_data.dart';
import '../providers/app_provider.dart';

class HemsAlgorithmService {
  final AppStateProvider provider;

  HemsAlgorithmService(this.provider);

  /// 1. Акустичний комфорт (Нічна тиша)
  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    // Дістаємо налаштування за НОВИМ ключем
    final buzzerConfig = data.rawFields['fullConfigs']?['buzzerAlarmSetting'];

    // Важливо: Siseli повертає значення як int (1 або 0)
    final currentBuzzer = buzzerConfig?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1';

    if (currentBuzzer != null && currentBuzzer != desiredBuzzer) {
      await provider.changeSetting('buzzerAlarmSetting', desiredBuzzer);
    }
  }

  /// 2. Адаптивний режим (зрізання піків + прогноз погоди)
  /// 2. Предиктивний адаптивний режим (Звільнення місця для сонця + Резерв на вечір)
  Future<void> executeAdaptiveMode(InverterData data, double batteryCapacityAh,
      double expectedSolarTodayWh // Прогноз Solcast на ПОТОЧНИЙ день
      ) async {
    final now = DateTime.now();
    final currentHour = now.hour;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    // 1. Фізичні параметри
    const systemVoltage = 51.2;
    final batteryCapacityWh = batteryCapacityAh * systemVoltage;
    const reserveSoc = 20.0; // Залишаємо 20% для здоров'я АКБ

    final availableSoc = data.batterySoc - reserveSoc;
    final availableEnergyWh =
        (availableSoc > 0) ? (batteryCapacityWh * (availableSoc / 100.0)) : 0.0;

    // Середнє споживання за годину (беремо поточне, але не менше 400 Вт для страховки)
    final avgHourlyLoad = data.loadPower > 400 ? data.loadPower : 400.0;

    if (currentHour >= 23 || currentHour < 7) {
      // --- НІЧНИЙ ТАРИФ (23:00 - 07:00) ---
      // Живимо будинок від мережі (дешево)
      if (currentOutput != '0') await provider.setMode(0); // USB

      // Скільки нам знадобиться на завтра? (з 07:00 до 23:00 = 16 годин)
      final dailyNeedWh = avgHourlyLoad * 16.0;
      // Дефіцит = Потреба - Прогноз сонця
      final deficitWh = dailyNeedWh - expectedSolarTodayWh;

      if (deficitWh > 0 && availableEnergyWh < deficitWh) {
        // [ЗАРЯДЖАЄМО] Енергії не вистачить. Заряджаємо з мережі до рівня дефіциту.
        if (currentCharger != '1') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '1'); // SNU
        }
      } else {
        // [СТОП ЗАРЯД] В акумуляторі ВЖЕ достатньо місця/енергії. Вимикаємо мережу.
        if (currentCharger != '2') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '2'); // OSO
        }
      }
    } else if (currentHour >= 19 && currentHour < 23) {
      // --- ВЕЧІРНЯ ПІДГОТОВКА (19:00 - 23:00) ---
      // Очікується скоро нічний тариф. АГРЕСИВНО РОЗРЯДЖАЄМО до 20%.
      if (currentOutput != '2') await provider.setMode(2); // SBU
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    } else {
      // --- ДЕНЬ (07:00 - 19:00) ---

      // 1. Скільки годин залишилося жити до 23:00?
      final hoursTillNight = 23.0 - currentHour;
      final energyNeededTillNightWh = avgHourlyLoad * hoursTillNight;

      // 2. Скільки сонячної енергії з прогнозу ще має надійти сьогодні?
      // (Проста пропорція: чим ближче до 19:00, тим менша частка прогнозу залишилась)
      final percentOfDayRemaining = (19.0 - currentHour) / 12.0;
      final remainingSolarWh = expectedSolarTodayWh *
          (percentOfDayRemaining > 0 ? percentOfDayRemaining : 0);

      // 3. Загальний баланс виживання
      final totalAvailableEnergyWh = availableEnergyWh + remainingSolarWh;

      if (totalAvailableEnergyWh >= energyNeededTillNightWh) {
        // [ЗВІЛЬНЮЄМО МІСЦЕ]
        // Сума заряду та очікуваного сонця ПЕРЕВИЩУЄ наші потреби!
        // Працюємо від батареї (SBU), щоб звільнити місце для сонця, інакше воно пропаде дарма.
        if (currentOutput != '2') await provider.setMode(2); // SBU
      } else {
        // [ТРИМАЄМО АКУМУЛЯТОР ЗАРЯДЖЕНИМ]
        // Прогноз сонця впав або акумулятор пустий. Якщо розрядимо зараз, до 23:00 не дотягнемо.
        // Перемикаємо будинок на мережу (USB), щоб "заморозити" залишок в АКБ для вечірнього піку.
        if (currentOutput != '0') await provider.setMode(0); // USB
      }

      // Вдень категорично забороняємо заряджати акумулятор від мережі, навіть якщо похмуро!
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    }
  }

  /// 3. Нічний арбітраж (Примусова економія)
  Future<void> executeNightArbitrage(InverterData data) async {
    final hour = DateTime.now().hour;
    if (hour >= 23 || hour < 7) {
      if (data.rawFields['outputSourcePriority']?['value']?.toString() != '0') {
        await provider.setMode(0); // USB
      }
      if (data.rawFields['chargerSourcePriority']?['value']?.toString() !=
          '1') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '1'); // SNU
      }
    } else {
      if (data.rawFields['outputSourcePriority']?['value']?.toString() != '2') {
        await provider.setMode(2); // SBU
      }
      if (data.rawFields['chargerSourcePriority']?['value']?.toString() !=
          '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    }
  }

  /// 4. Шторм / Резерв (100% готовність до відключень)
  Future<void> executeStormMode(InverterData data) async {
    if (data.rawFields['outputSourcePriority']?['value']?.toString() != '0') {
      await provider.setMode(0); // USB
    }
    if (data.rawFields['chargerSourcePriority']?['value']?.toString() != '1') {
      await provider.changeSetting('chargerSourcePrioritySetting', '1'); // SNU
    }
  }
}

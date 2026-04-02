import '../models/inverter_data.dart';
import '../providers/app_provider.dart';

class HemsAlgorithmService {
  final AppStateProvider provider;

  // Географічна прив'язка (Пустомити)
  final double latitude = 49.7132;
  final double longitude = 23.9056;

  HemsAlgorithmService(this.provider);

  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    final currentBuzzer = data.rawFields['buzzerSwitch']?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1'; // 0 = Disable, 1 = Enable

    if (currentBuzzer != desiredBuzzer) {
      await provider.changeSetting('buzzerSwitchSetting', desiredBuzzer);
    }
  }

  /// Предиктивний адаптивний режим з максимізацією розряду до 23:00
  Future<void> executeAdaptiveMode(
      InverterData data, double batteryCapacityAh) async {
    final now = DateTime.now();
    final currentHour = now.hour;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    // 1. Фізичні параметри системи
    const systemVoltage = 51.2; // Напруга системи
    final batteryCapacityWh = batteryCapacityAh * systemVoltage;
    const reserveSoc =
        20.0; // Нижній поріг розряду (залишаємо 20% для здоров'я батареї)

    // 2. Розрахунок доступної енергії
    final availableSoc = data.batterySoc - reserveSoc;
    final availableEnergyWh =
        (availableSoc > 0) ? (batteryCapacityWh * (availableSoc / 100.0)) : 0.0;

    // Беремо поточне навантаження (мінімум 400 Вт для перестраховки)
    final predictedAvgLoad = data.loadPower > 400 ? data.loadPower : 400.0;

    // 3. Зони доби
    final isNightTariff = currentHour >= 23 || currentHour < 7;
    final isEveningPeak = currentHour >= 19 && currentHour < 23;
    final isDay = currentHour >= 7 && currentHour < 19;

    if (isNightTariff) {
      // --- НІЧНИЙ ТАРИФ (23:00 - 07:00) ---
      // Батарея зараз розряджена. Максимально заряджаємо її дешевою енергією.
      if (currentOutput != '0') {
        await provider.setMode(0); // USB (Мережа живить будинок)
      }
      if (currentCharger != '1') {
        await provider.changeSetting('chargerSourcePrioritySetting',
            '1'); // SNU (Мережа + Сонце заряджають)
      }
    } else if (isEveningPeak) {
      // --- ВЕЧІРНІЙ ПІК (19:00 - 23:00) ---
      // Найдорожчий час. Безжально розряджаємо батарею до мінімуму.
      if (currentOutput != '2') {
        await provider.setMode(2); // SBU (Сонце -> Батарея -> Мережа)
      }
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO (Тільки сонце)
      }
    } else if (isDay) {
      // --- ДЕНЬ (07:00 - 19:00) ---
      // Мета: підійти до 19:00 із зарядом, якого вистачить РІВНО на 4 години піку (до 23:00).

      // Скільки енергії нам знадобиться на вечірній пік (з 19 до 23 = 4 години)?
      final peakEnergyRequiredWh = predictedAvgLoad * 4.0;

      if (availableEnergyWh <= peakEnergyRequiredWh) {
        // [БРОНЮВАННЯ] Енергії лишилося тільки на вечірній пік!
        // Зупиняємо розрядку зараз. Переходимо на мережу (денний тариф дешевший за піковий).
        // Це дозволить "донести" цей заряд до 19:00.
        if (currentOutput != '0') await provider.setMode(0); // USB (Мережа)
        if (currentCharger != '2') {
          await provider.changeSetting('chargerSourcePrioritySetting',
              '2'); // OSO (Мережею не заряджаємо, чекаємо сонця або ночі)
        }
      } else {
        // [РОЗРЯД] Енергії більше, ніж треба на вечір.
        // Працюємо від батареї, щоб цілеспрямовано її розряджати і економити денний тариф.
        if (currentOutput != '2') await provider.setMode(2); // SBU (Батарея)
        if (currentCharger != '0') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '0'); // CSO (Сонце першочергово)
        }
      }
    }
  }

  Future<void> executeNightArbitrage(
      InverterData data, double batteryCapacityAh) async {
    final now = DateTime.now();
    final isNightTariff = now.hour >= 23 || now.hour < 7;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    if (isNightTariff) {
      // Вночі живимось від мережі і заряджаємо батарею (SNU)
      if (currentOutput != '0') await provider.setMode(0); // USB
      if (currentCharger != '1') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '1'); // SNU
      }

      // Логіка розрахунку струму (можна розширити налаштуванням max_utility_charge_current)
    } else {
      // Вдень повертаємось на автономію (SBU) та зарядку від сонця (OSO/CSO)
      if (currentOutput != '2') await provider.setMode(2); // SBU
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    }
  }

  Future<void> executeSmartLoadShedding(InverterData data) async {
    var compensatedSoc = data.batterySoc;

    // Компенсація Voltage Sag (якщо споживання > 2kW, напруга тимчасово просідає)
    if (data.loadPower > 2000) {
      compensatedSoc += 3.0;
    }

    // Ключ залежить від специфікації інвертора PowMr (напр. pointOfReturnToUtility)
    // Тут залишаю приклад логіки відключення додаткового навантаження.
    // Якщо заряд падає нижче 40% - відключаємо неприоритетне навантаження (бойлер тощо).
  }

  Future<void> executeStormMode(InverterData data) async {
    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    // Пріоритет мережі для живлення будинку
    if (currentOutput != '0') await provider.setMode(0); // USB
    // Максимальна зарядка батареї (Мережа + Сонце)
    if (currentCharger != '1') {
      await provider.changeSetting('chargerSourcePrioritySetting', '1'); // SNU
    }
  }
}

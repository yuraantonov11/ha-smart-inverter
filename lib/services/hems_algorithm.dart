import '../models/inverter_data.dart';
import '../providers/app_provider.dart';

class HemsAlgorithmService {
  final AppStateProvider provider;

  HemsAlgorithmService(this.provider);

  /// 1. Акустичний комфорт (Нічна тиша)
  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    final currentBuzzer = data.rawFields['buzzerSwitch']?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1';

    if (currentBuzzer != desiredBuzzer) {
      await provider.changeSetting('buzzerSwitchSetting', desiredBuzzer);
    }
  }

  /// 2. Адаптивний режим (зрізання піків + прогноз погоди)
  Future<void> executeAdaptiveMode(InverterData data, double batteryCapacityAh,
      double expectedSolarTomorrowWh) async {
    final now = DateTime.now();
    final currentHour = now.hour;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    const systemVoltage = 51.2;
    final batteryCapacityWh = batteryCapacityAh * systemVoltage;
    const reserveSoc = 20.0;

    final availableSoc = data.batterySoc - reserveSoc;
    final availableEnergyWh =
        (availableSoc > 0) ? (batteryCapacityWh * (availableSoc / 100.0)) : 0.0;

    final predictedAvgLoad = data.loadPower > 400 ? data.loadPower : 400.0;

    final isNightTariff = currentHour >= 23 || currentHour < 7;
    final isEveningPeak = currentHour >= 19 && currentHour < 23;
    final isDay = currentHour >= 7 && currentHour < 19;

    if (isNightTariff) {
      // --- НІЧНИЙ ТАРИФ (23:00 - 07:00) ---
      final expectedDailyConsumptionWh =
          predictedAvgLoad * 16.0; // Потреба на завтрашній день

      if (currentOutput != '0') {
        await provider.setMode(0); // USB (Живлення від мережі)
      }

      // ПРИЙНЯТТЯ РІШЕННЯ НА ОСНОВІ ПРОГНОЗУ:
      if (expectedSolarTomorrowWh >= expectedDailyConsumptionWh) {
        // Завтра багато сонця. Заряджаємо батарею тільки сонцем (OSO), економимо гроші.
        if (currentCharger != '2') {
          await provider.changeSetting('chargerSourcePrioritySetting', '2');
        }
      } else {
        // Завтра хмарно. Заряджаємо батарею від мережі (SNU), бо сонця не вистачить.
        if (currentCharger != '1') {
          await provider.changeSetting('chargerSourcePrioritySetting', '1');
        }
      }
    } else if (isEveningPeak) {
      // --- ВЕЧІРНІЙ ПІК (19:00 - 23:00) ---
      if (currentOutput != '2') {
        await provider.setMode(2); // SBU (Живлення від батареї)
      }
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    } else if (isDay) {
      // --- ДЕНЬ (07:00 - 19:00) ---
      final peakEnergyRequiredWh =
          predictedAvgLoad * 4.0; // Енергія для вечірнього піку

      if (availableEnergyWh <= peakEnergyRequiredWh) {
        // Резервуємо енергію! Зупиняємо розряд і переходимо на денний тариф мережі.
        if (currentOutput != '0') await provider.setMode(0); // USB
        if (currentCharger != '2') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '2'); // OSO
        }
      } else {
        // Енергії достатньо, працюємо автономно.
        if (currentOutput != '2') await provider.setMode(2); // SBU
        if (currentCharger != '0') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '0'); // CSO (Сонце першочергово)
        }
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
      if (data.rawFields['chargerSourcePriority']?['value']?.toString() != '1') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '1'); // SNU
      }
    } else {
      if (data.rawFields['outputSourcePriority']?['value']?.toString() != '2') {
        await provider.setMode(2); // SBU
      }
      if (data.rawFields['chargerSourcePriority']?['value']?.toString() != '2') {
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

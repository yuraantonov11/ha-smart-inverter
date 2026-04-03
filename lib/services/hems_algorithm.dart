import 'package:flutter/foundation.dart'; // Для debugPrint
import '../models/inverter_data.dart';
import '../providers/app_provider.dart';

class HemsAlgorithmService {
  final AppStateProvider provider;

  // Географічна прив'язка (Пустомити)
  final double latitude = 49.7132;
  final double longitude = 23.9056;

  HemsAlgorithmService(this.provider);

  // ===========================================================================
  // 1. БАЗОВІ ЗАХИСНІ АЛГОРИТМИ (ПРІОРИТЕТ 1)
  // ===========================================================================

  /// РЕЖИМ ВИЖИВАННЯ: Запобігає блекауту при критичному розряді
  /// Повертає [true], якщо режим активовано (щоб зупинити інші алгоритми).
  Future<bool> enforceSurvivalMode(InverterData data) async {
    final soc = data.batterySoc;
    // Якщо API не повертає напругу прямо, можна використовувати 0.0 або дістати з rawFields
    final voltage = data.rawFields['batteryVoltage']?['value'] ?? 50.0;

    // Критичні показники (20% заряду або напруга нижче 47.5V)
    final isCriticallyLow =
        soc < 20.0 || (voltage is num && voltage < 47.5);

    if (isCriticallyLow) {
      debugPrint('🚨 КРИТИЧНИЙ СТАН: SOC=$soc%. Активація режиму виживання!');

      final currentOutput =
          data.rawFields['outputSourcePriority']?['value']?.toString();
      final currentCharger =
          data.rawFields['chargerSourcePriority']?['value']?.toString();

      // 1. Примусово живимо будинок від мережі (Utility First)
      if (currentOutput != '0') await provider.setMode(0);

      // 2. Вмикаємо зарядку від мережі (Solar and Utility)
      if (currentCharger != '1') {
        await provider.changeSetting('chargerSourcePrioritySetting', '1');
      }

      return true; // Блокуємо виконання інших алгоритмів
    }
    return false;
  }

  /// Акустичний комфорт (вимикаємо пищалку на ніч)
  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    final currentBuzzer = data.rawFields['buzzerSwitch']?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1'; // 0 = Disable, 1 = Enable

    if (currentBuzzer != desiredBuzzer) {
      debugPrint('🌙 Зміна режиму пищалки на: $desiredBuzzer');
      await provider.changeSetting('buzzerSwitchSetting', desiredBuzzer);
    }
  }

  /// Grid Assist: Допомога мережею при пікових навантаженнях
  /// Захищає інвертор та батарею від перевантаження (напр., увімкнули бойлер + чайник)
  Future<void> executeSmartLoadShedding(InverterData data) async {
    final load = data.loadPower;
    var compensatedSoc = data.batterySoc;

    // Компенсація просадки напруги (Voltage Sag) під навантаженням
    if (load > 2000) compensatedSoc += 3.0;
    if (load > 4000) compensatedSoc += 5.0;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();

    // Якщо навантаження > 4.5 кВт АБО (навантаження > 3 кВт і реальний заряд < 30%)
    if (load > 4500 || (load > 3000 && compensatedSoc < 30.0)) {
      if (currentOutput != '0') {
        debugPrint(
            '⚠️ Перевантаження/Просадка ($load W). Тимчасовий перехід на мережу (Bypass).');
        await provider.setMode(0); // USB (Мережа напряму)
      }
    }
  }

  // ===========================================================================
  // 2. РЕЖИМИ РОЗУМНОГО УПРАВЛІННЯ (ПРІОРИТЕТ 2)
  // ===========================================================================

  /// Предиктивний адаптивний режим з урахуванням тарифів
  Future<void> executeAdaptiveMode(
      InverterData data, double batteryCapacityAh) async {
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

    // Прогнозоване навантаження (не менше 400 Вт)
    final predictedAvgLoad = data.loadPower > 400 ? data.loadPower : 400.0;

    final isNightTariff = currentHour >= 23 || currentHour < 7;
    final isEveningPeak = currentHour >= 19 && currentHour < 23;
    final isDay = currentHour >= 7 && currentHour < 19;

    if (isNightTariff) {
      // --- НІЧНИЙ ТАРИФ (23:00 - 07:00) ---
      if (currentOutput != '0') await provider.setMode(0); // USB
      if (currentCharger != '1') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '1'); // SNU
      }
    } else if (isEveningPeak) {
      // --- ВЕЧІРНІЙ ПІК (19:00 - 23:00) ---
      if (currentOutput != '2') {
        await provider.setMode(2); // SBU (Батарея пріоритет)
      }
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    } else if (isDay) {
      // --- ДЕНЬ (07:00 - 19:00) ---
      final peakEnergyRequiredWh =
          predictedAvgLoad * (23.0 - 19.0); // Енергія на вечірні години

      if (availableEnergyWh <= peakEnergyRequiredWh && data.batterySoc < 95.0) {
        // Заряду мало: економимо його на вечір, живимось від мережі
        if (currentOutput != '0') await provider.setMode(0); // USB
        if (currentCharger != '2') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '2'); // OSO
        }
      } else {
        // Заряду багато: працюємо від батареї (автономія)
        if (currentOutput != '2') await provider.setMode(2); // SBU
        if (currentCharger != '0') {
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '0'); // CSO
        }
      }
    }
  }

  /// Простий Нічний арбітраж (без зайвої логіки)
  Future<void> executeNightArbitrage(InverterData data) async {
    final now = DateTime.now();
    final isNightTariff = now.hour >= 23 || now.hour < 7;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    if (isNightTariff) {
      if (currentOutput != '0') await provider.setMode(0); // USB
      if (currentCharger != '1') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '1'); // SNU
      }
    } else {
      if (currentOutput != '2') await provider.setMode(2); // SBU
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }
    }
  }

  /// Режим "Шторм" (Готуємось до відключень, тримаємо заряд 100%)
  Future<void> executeStormMode(InverterData data) async {
    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    if (currentOutput != '0') await provider.setMode(0); // USB
    if (currentCharger != '1') {
      await provider.changeSetting('chargerSourcePrioritySetting', '1'); // SNU
    }
  }
}

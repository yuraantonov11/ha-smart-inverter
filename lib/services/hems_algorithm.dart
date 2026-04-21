import 'package:intl/intl.dart';
import 'dart:math';
import '../models/inverter_data.dart';
import '../providers/app_provider.dart';
import 'log_service.dart';

class HemsAlgorithmService {
  final AppStateProvider provider;

  // --- Battery Keepalive ---
  DateTime? _lastBatteryActivityAt;
  bool _keepaliveInProgress = false;
  static const _keepaliveInterval = Duration(hours: 2);
  static const _keepaliveDuration = Duration(seconds: 90);
  static const _keepaliveMinSoc = 22.0;

  // --- Acoustic comfort ---
  String? _lastAppliedBuzzer;

  HemsAlgorithmService(this.provider);

  /// Оновлює час останньої активності батареї (розряд або заряд)
  void _trackBatteryActivity(InverterData data) {
    if (data.batteryPower.abs() > 50) {
      // батарея активна (заряд або розряд > 50W)
      _lastBatteryActivityAt = DateTime.now();
    }
  }

  /// Keepalive: якщо батарея не була активна більше 2 годин,
  /// коротко перемикаємо на SBU щоб "розбудити" її.
  Future<bool> _batteryKeepalive(InverterData data) async {
    if (_keepaliveInProgress) return true; // вже виконується

    _trackBatteryActivity(data);

    // Не робимо keepalive якщо SOC близький до резерву
    if (data.batterySoc <= _keepaliveMinSoc) return false;

    final lastActivity = _lastBatteryActivityAt;
    if (lastActivity == null) {
      // Перший запуск — ініціалізуємо
      _lastBatteryActivityAt = DateTime.now();
      return false;
    }

    final inactiveDuration = DateTime.now().difference(lastActivity);
    if (inactiveDuration < _keepaliveInterval) return false;

    // Батарея неактивна занадто довго — запускаємо keepalive
    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    if (currentOutput == '2') return false; // вже на батареї

    _keepaliveInProgress = true;
    LogService.log(
        '🔋 Keepalive: батарея неактивна ${inactiveDuration.inMinutes} хв. Короткий перехід на SBU для запобігання сну АКБ.');

    await provider.setMode(2); // SBU — розбудити батарею

    // Повертаємо назад через 90 секунд
    Future.delayed(_keepaliveDuration, () async {
      await provider.setMode(0); // Повертаємо USB
      _lastBatteryActivityAt = DateTime.now();
      _keepaliveInProgress = false;
      LogService.log('🔋 Keepalive завершено. Повернено режим мережі (USB).');
    });

    return true; // keepalive запущено, пропускаємо основну логіку
  }

  /// 1. Акустичний комфорт (Нічна тиша)
  Future<void> enforceAcousticComfort(InverterData data) async {
    final hour = DateTime.now().hour;
    final isNight = hour >= 22 || hour < 7;

    final buzzerConfig = data.rawFields['fullConfigs']?['buzzerAlarmSetting'];
    final currentBuzzer = buzzerConfig?['value']?.toString();
    final desiredBuzzer = isNight ? '0' : '1';

    // Skip if config not loaded yet (avoid repeated attempts on null config)
    if (currentBuzzer == null) return;

    // Skip if already applied this desired state (avoids repeating when
    // changeSetting's optimistic update doesn't reach fullConfigs cache)
    if (currentBuzzer == desiredBuzzer || _lastAppliedBuzzer == desiredBuzzer) {
      return;
    }

    await provider.changeSetting('buzzerAlarmSetting', desiredBuzzer);
    // Also update fullConfigs cache directly so next check sees the new value
    final fullConfigs = data.rawFields['fullConfigs'];
    if (fullConfigs is Map<String, dynamic> &&
        fullConfigs['buzzerAlarmSetting'] is Map<String, dynamic>) {
      (fullConfigs['buzzerAlarmSetting'] as Map<String, dynamic>)['value'] =
          num.tryParse(desiredBuzzer) ?? desiredBuzzer;
    }
    _lastAppliedBuzzer = desiredBuzzer;
    LogService.log(isNight
        ? '🤫 Увімкнено нічну тишу інвертора'
        : '🔊 Повернено звук інвертора');
  }

  /// 2. ВИСОКОТОЧНИЙ АДАПТИВНИЙ РЕЖИМ (Статистичне моделювання)
  Future<void> executeAdaptiveMode({
    required InverterData data,
    required double batteryCapacityAh,
    required Map<String, double> hourlyForecast,
    required Map<int, double>
        avgHourlyConsumptionStats, // Статистика споживання (година: Ват-години)
    required double
        productionCoefficient, // Коефіцієнт реальної генерації (напр. 0.75)
    DateTime? nowOverride,
  }) async {
    // Keepalive перевірка — якщо батарея засинає, пробуджуємо
    if (await _batteryKeepalive(data)) return;

    final now = nowOverride ?? DateTime.now();
    final currentHour = now.hour;

    final currentOutput =
        data.rawFields['outputSourcePriority']?['value']?.toString();
    final currentCharger =
        data.rawFields['chargerSourcePriority']?['value']?.toString();

    // 1. Фізичні параметри АКБ
    const systemVoltage = 51.2;
    final maxBatteryCapacityWh = batteryCapacityAh * systemVoltage;

    // Динамічний резерв (нижче якого ми не хочемо опускати батарею, напр. 20%)
    const reserveSoc = 20.0;
    final reserveEnergyWh = maxBatteryCapacityWh * (reserveSoc / 100.0);

    final currentEnergyWh = maxBatteryCapacityWh * (data.batterySoc / 100.0);
    final availableEnergyWh = max(0.0, currentEnergyWh - reserveEnergyWh);
    final eveningSafetyWh = maxBatteryCapacityWh * 0.01; // 1% буфер від флотів

    final formatter = DateFormat('yyyy-MM-dd HH:mm');

    // 2. ФУНКЦІЯ СИМУЛЯЦІЇ (DIGITAL TWIN)
    // Повертає дефіцит енергії (в Ват-годинах) для заданого періоду.
    // Якщо повертає 0 - енергії вистачить і батарея не впаде нижче резерву.
    double simulateEnergyDeficit(int startHour, int endHour,
        DateTime targetDate, double startBatteryWh) {
      var simulatedBatteryWh = startBatteryWh;
      var totalDeficitWh = 0.0;

      for (var h = startHour; h < endHour; h++) {
        // Отримуємо статистичне споживання для цієї години
        final loadWh = avgHourlyConsumptionStats[h] ?? 500.0; // 500Вт як фолбек

        // Отримуємо скоригований прогноз сонця
        final timeKey = formatter.format(
            DateTime(targetDate.year, targetDate.month, targetDate.day, h, 0));
        final rawSolarWh = hourlyForecast[timeKey] ?? 0.0;
        final realSolarWh = rawSolarWh *
            productionCoefficient; // Враховуємо малу площу панелей/тіні

        // Баланс години
        simulatedBatteryWh += realSolarWh - loadWh;

        // Якщо батарея зарядилась повністю, надлишок сонця "згорає" (якщо немає бойлера)
        if (simulatedBatteryWh > maxBatteryCapacityWh) {
          simulatedBatteryWh = maxBatteryCapacityWh;
        }

        // Якщо батарея впала нижче резерву - фіксуємо дефіцит
        if (simulatedBatteryWh < reserveEnergyWh) {
          totalDeficitWh += (reserveEnergyWh - simulatedBatteryWh);
          simulatedBatteryWh =
              reserveEnergyWh; // Батарея залишається на дні (будинок перейде на мережу)
        }
      }
      return totalDeficitWh;
    }

    // --- ЛОГІКА ПРИЙНЯТТЯ РІШЕНЬ ---

    if (currentHour >= 23 || currentHour < 7) {
      // ==========================================
      // НІЧНИЙ ТАРИФ (23:00 - 07:00)
      // ==========================================
      if (currentOutput != '0') {
        await provider.setMode(0); // USB (Utility First)
      }

      // Симулюємо ЗАВТРІШНІЙ день (з 07:00 до 23:00)
      // Припускаємо, що до 07:00 ранку батарея зарядиться від мережі до 100% (якщо ми увімкнемо зарядку)
      // або залишиться як є (якщо вимкнемо).

      // Перевіряємо, що буде, якщо ми НЕ будемо заряджати вночі взагалі (стартуємо з поточного заряду)
      final tomorrow = now.hour >= 23 ? now.add(const Duration(days: 1)) : now;
      final deficitIfNoCharge =
          simulateEnergyDeficit(7, 23, tomorrow, currentEnergyWh);

      if (deficitIfNoCharge > 0) {
        // Навіть із сонцем нам не вистачить енергії на день/вечір.
        // Причина: похмуро АБО панелей занадто мало для покриття потреб. Заряджаємось по нічному тарифу!
        if (currentCharger != '1') {
          LogService.log(
              '🌙 Ніч: Симуляція показує дефіцит ${deficitIfNoCharge.toInt()} Вт*год на завтра. Вмикаємо зарядку (SNU).');
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '1'); // SNU
        }
      } else {
        // Енергії повністю вистачить завдяки сонцю і залишку в батареї
        if (currentCharger != '2') {
          LogService.log(
              '🌙 Ніч: Завтра сонця вистачить повністю. Економимо ресурс АКБ, зарядка від мережі ВИМК (OSO).');
          await provider.changeSetting(
              'chargerSourcePrioritySetting', '2'); // OSO
        }
      }
    } else if (currentHour >= 17 && currentHour < 23) {
      // ==========================================
      // ВЕЧІРНІЙ ПІК (17:00 - 23:00)
      // ==========================================
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }

      final deficitTillNight =
          simulateEnergyDeficit(currentHour, 23, now, currentEnergyWh);
      final reserveProtectionActive = data.batterySoc <= (reserveSoc + 2.0) ||
          availableEnergyWh <= eveningSafetyWh ||
          deficitTillNight > 0;
      final batteryCanBeUsed = data.batterySoc >= (reserveSoc + 5.0) &&
          availableEnergyWh > eveningSafetyWh &&
          deficitTillNight == 0;

      if (reserveProtectionActive) {
        if (currentOutput != '0') {
          if (deficitTillNight > 0) {
            LogService.log(
                '⚠️ Вечір: прогнозований дефіцит до 23:00 = ${deficitTillNight.toInt()} Вт*год. Перехід на мережу (USB).');
          } else {
            LogService.log(
                '⚠️ Вечір: SOC ${data.batterySoc.toStringAsFixed(1)}% біля резерву ${reserveSoc.toInt()}%. Перехід на мережу (USB).');
          }
          await provider.setMode(0); // USB
        }
      } else if (batteryCanBeUsed) {
        if (currentOutput != '2') {
          LogService.log(
              '🌆 Вечір: Працюємо від АКБ (SBU). Залишок: ${availableEnergyWh.toInt()} Вт*год.');
          await provider.setMode(2); // SBU
        }
      }
    } else {
      // ==========================================
      // ДЕНЬ (07:00 - 17:00)
      // ==========================================
      if (currentCharger != '2') {
        await provider.changeSetting(
            'chargerSourcePrioritySetting', '2'); // OSO
      }

      // Симулюємо залишок поточного дня (від поточної години до 23:00)
      // Стартуємо з поточного реального заряду батареї
      final deficitTillNight =
          simulateEnergyDeficit(currentHour, 23, now, currentEnergyWh);

      if (deficitTillNight == 0) {
        // Сонця і батареї ГАРАНТОВАНО вистачить до 23:00 (з урахуванням коефіцієнта і статистики)
        if (currentOutput != '2') {
          LogService.log(
              '☀️ День: Симуляція успішна (дефіцит 0). Працюємо від Сонця/АКБ (SBU).');
          await provider.setMode(2); // SBU
        }
      } else {
        // УВАГА: Симуляція показує, що батарея сяде ДО 23:00 (наприклад, о 20:00).
        // Це означає, що панелі зараз генерують сонце, але його не вистачає для покриття поточного
        // споживання + накопичення резерву на вечір.
        // РІШЕННЯ: Зараз живимо будинок від мережі (вона зараз дешева), а ВСЕ сонце направляємо на зарядку АКБ!
        if (currentOutput != '0') {
          LogService.log(
              '⛅ День: Симуляція показує дефіцит ввечері. Живимось від мережі (USB), зберігаємо сонце в АКБ!');
          await provider.setMode(0); // USB
        }
      }
    }
  }

  /// 3. Нічний арбітраж (Примусова економія)
  Future<void> executeNightArbitrage(InverterData data) async {
    if (await _batteryKeepalive(data)) return;

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
    // У штормовому режимі keepalive не потрібен — батарея заряджається постійно
    _trackBatteryActivity(data);

    if (data.rawFields['outputSourcePriority']?['value']?.toString() != '0') {
      await provider.setMode(0); // USB
    }
    if (data.rawFields['chargerSourcePriority']?['value']?.toString() != '1') {
      await provider.changeSetting('chargerSourcePrioritySetting', '1'); // SNU
    }
  }
}

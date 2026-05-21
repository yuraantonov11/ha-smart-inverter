class InverterData {
  final double pvPower;
  final double gridPower;
  final double
      batteryPower; // позитивне = заряджається, негативне = розряджається
  final double loadPower;
  final double batterySoc;

  final double pvVoltage;
  final double gridVoltage;
  final double batteryVoltage;
  final double loadPercentage;
  final String workingMode;

  final String deviceSn;
  final String currentModeStr;
  Map<String, dynamic> rawFields;

  InverterData({
    required this.pvPower,
    required this.gridPower,
    required this.batteryPower,
    required this.loadPower,
    required this.batterySoc,
    required this.pvVoltage,
    required this.gridVoltage,
    required this.batteryVoltage,
    required this.loadPercentage,
    required this.workingMode,
    required this.deviceSn,
    required this.currentModeStr,
    required this.rawFields,
  });

  /// Розраховує реальний SOC для 16S LiFePO4 (48V nominal) батареї за напругою.
  /// Без BMS-кабелю інвертор часто рапортує статичний 100%, тому корекція
  /// за напругою — головне джерело правди. Параметр [batteryCurrent] —
  /// струм у А (>0 заряд, <0 розряд) використовується для компенсації
  /// просадки/підняття напруги під навантаженням (внутрішній опір ≈ 8 мОм/cell).
  static double getRealSoc(
    double reportedSoc,
    double voltage, {
    double batteryCurrent = 0.0,
  }) {
    // Якщо напруга невалідна — лишаємо як є.
    if (voltage <= 10.0) return reportedSoc.clamp(0.0, 100.0);

    // Компенсація IR-drop: ~0.128 В на кожні 10 А (16 cells * 8 mΩ).
    // При розряді (current<0) додаємо назад просадку, при заряді — віднімаємо.
    final compensatedVoltage = voltage - batteryCurrent * 0.0128;

    // Open-circuit voltage таблиця для 16S LiFePO4 (3.0..3.45 В/cell).
    // Точки калібровані під типовий профіль розряду LFP.
    double socFromVoltage;
    if (compensatedVoltage >= 54.4) {
      socFromVoltage = 100.0; // 3.40 V/cell — повний
    } else if (compensatedVoltage >= 53.6) {
      socFromVoltage = 95.0; // 3.35
    } else if (compensatedVoltage >= 53.2) {
      socFromVoltage = 90.0; // 3.325
    } else if (compensatedVoltage >= 52.8) {
      socFromVoltage = 80.0; // 3.30
    } else if (compensatedVoltage >= 52.5) {
      socFromVoltage = 70.0;
    } else if (compensatedVoltage >= 52.2) {
      socFromVoltage = 60.0;
    } else if (compensatedVoltage >= 52.0) {
      socFromVoltage = 50.0; // плато LFP
    } else if (compensatedVoltage >= 51.7) {
      socFromVoltage = 40.0;
    } else if (compensatedVoltage >= 51.4) {
      socFromVoltage = 30.0;
    } else if (compensatedVoltage >= 51.0) {
      socFromVoltage = 20.0;
    } else if (compensatedVoltage >= 50.4) {
      socFromVoltage = 15.0;
    } else if (compensatedVoltage >= 49.6) {
      socFromVoltage = 10.0;
    } else if (compensatedVoltage >= 48.8) {
      socFromVoltage = 5.0;
    } else if (compensatedVoltage >= 48.0) {
      socFromVoltage = 2.0;
    } else {
      socFromVoltage = 0.0; // <48V (3.0 V/cell) — глибокий розряд
    }

    // Якщо інвертор репортує валідне значення (не очевидно "застрягле" на 100/0)
    // і воно близьке до напруги — довіряємо BMS. Інакше — беремо мінімум,
    // щоб уникнути небезпечного завищення.
    final reported = reportedSoc.clamp(0.0, 100.0);
    final delta = (reported - socFromVoltage).abs();

    // Якщо BMS показує 100% але напруга < 53.2В — це баг, беремо за напругою.
    // Якщо узгоджено в межах ±10% — довіряємо репорту (BMS точніший на плато).
    if (delta <= 10.0) return reported;
    return socFromVoltage;
  }

  static double _parseDouble(dynamic fieldObject, {bool isKw = false}) {
    if (fieldObject == null) return 0.0;
    var val = 0.0;
    if (fieldObject is num) {
      val = fieldObject.toDouble();
    } else if (fieldObject is String) {
      val = double.tryParse(fieldObject) ?? 0.0;
    } else if (fieldObject is Map) {
      final rawValue =
          fieldObject['value'] ?? fieldObject['valueDisplay'] ?? 0.0;
      if (rawValue is num) val = rawValue.toDouble();
      if (rawValue is String) val = double.tryParse(rawValue) ?? 0.0;
    }
    return isKw ? val * 1000 : val;
  }

  static String _parseString(dynamic fieldObject) {
    if (fieldObject == null) return 'N/A';
    if (fieldObject is String) return fieldObject;
    if (fieldObject is Map) {
      return fieldObject['valueDisplay']?.toString() ??
          fieldObject['value']?.toString() ??
          'N/A';
    }
    return fieldObject.toString();
  }

  static String _normalizeModeLabel(String raw) {
    final value = raw.trim();
    if (value.isEmpty || value.toLowerCase() == 'null') return '';
    if (value == '0') return 'USB';
    if (value == '1') return 'SUB';
    if (value == '2') return 'SBU';
    return value;
  }

  static String _resolveCurrentMode(
    String currentModeStr,
    Map<String, dynamic> fields,
  ) {
    final explicit = _normalizeModeLabel(currentModeStr);
    if (explicit.isNotEmpty) return explicit;

    final outputPriority = fields['outputSourcePriority'];
    if (outputPriority is Map) {
      final normalized = _normalizeModeLabel(
        outputPriority['valueDisplay']?.toString() ??
            outputPriority['value']?.toString() ??
            '',
      );
      if (normalized.isNotEmpty) return normalized;
    }

    final outputPrioritySetting = fields['outputSourcePrioritySetting'];
    if (outputPrioritySetting is Map) {
      final normalized = _normalizeModeLabel(
        outputPrioritySetting['valueDisplay']?.toString() ??
            outputPrioritySetting['value']?.toString() ??
            '',
      );
      if (normalized.isNotEmpty) return normalized;
    }

    return 'N/A';
  }

  factory InverterData.fromJson(
      Map<String, dynamic> json, String deviceSn, String currentModeStr) {
    final fields = json['deviceAttributeState']?['fields'] ?? {};

    // Усі потужності зводимо до Ват (W)
    var pv = _parseDouble(fields['pvInputPower'] ?? fields['generationPower']);
    var load = _parseDouble(fields['acOutputActivePower'], isKw: true);
    var reportedSoc = _parseDouble(fields['batteryCapacity']);
    var gridVolt = _parseDouble(fields['acInputVoltage']);
    var loadPct = _parseDouble(fields['loadPercentage']);
    var batVolt = _parseDouble(fields['batteryVoltage']);
    var pvVolt = _parseDouble(fields['pvInputVoltage']);

    // Розрахунок реальної потужності батареї
    var batCharge = _parseDouble(fields['batteryChargingCurrent']);
    var batDischarge = _parseDouble(fields['batteryDischargeCurrent']);
    var batPower = (batCharge > 0)
        ? batCharge * batVolt
        : (batDischarge > 0 ? -batDischarge * batVolt : 0.0);

    // КРИТИЧНО: API часто рапортує batteryCapacity=100% коли немає BMS-кабелю,
    // навіть якщо акумулятор фактично розряджений. Завжди валідуємо за напругою.
    final batteryCurrent =
        batCharge > 0 ? batCharge : (batDischarge > 0 ? -batDischarge : 0.0);
    var soc = getRealSoc(
      reportedSoc,
      batVolt,
      batteryCurrent: batteryCurrent,
    );

    // Логіка визначення потоку з мережі (Grid Power)
    var workingState = _parseString(fields['workingStates']);
    var workingStateVal = fields['workingStates']?['value']?.toString() ?? '';

    // Якщо інвертор в режимі "Line Mode" (Мережа) - зазвичай це код 4
    var isLineMode =
        workingStateVal == '4' || workingState.toLowerCase().contains('line');

    var grid = 0.0;
    if (isLineMode && gridVolt > 0) {
      // Мережа покриває різницю між споживанням/зарядом та тим, що дає сонце
      grid = load + (batPower > 0 ? batPower : 0) - pv;
      if (grid < 0) grid = 0; // Якщо сонце перекриває все, з мережі не беремо
    }

    return InverterData(
      pvPower: pv,
      gridPower: grid,
      batteryPower: batPower,
      loadPower: load,
      batterySoc: soc,
      pvVoltage: pvVolt,
      gridVoltage: gridVolt,
      batteryVoltage: batVolt,
      loadPercentage: loadPct,
      workingMode: workingState,
      deviceSn: deviceSn,
      currentModeStr: _resolveCurrentMode(currentModeStr, fields),
      rawFields: fields,
    );
  }

  Map<String, dynamic> toCacheMap() {
    return {
      'pvPower': pvPower,
      'gridPower': gridPower,
      'batteryPower': batteryPower,
      'loadPower': loadPower,
      'batterySoc': batterySoc,
      'pvVoltage': pvVoltage,
      'gridVoltage': gridVoltage,
      'batteryVoltage': batteryVoltage,
      'loadPercentage': loadPercentage,
      'workingMode': workingMode,
      'deviceSn': deviceSn,
      'currentModeStr': currentModeStr,
      'rawFields': rawFields,
    };
  }

  factory InverterData.fromCacheMap(Map<String, dynamic> map) {
    final raw = map['rawFields'];
    final rawFields = raw is Map<String, dynamic> ? raw : <String, dynamic>{};

    return InverterData(
      pvPower: (map['pvPower'] as num?)?.toDouble() ?? 0.0,
      gridPower: (map['gridPower'] as num?)?.toDouble() ?? 0.0,
      batteryPower: (map['batteryPower'] as num?)?.toDouble() ?? 0.0,
      loadPower: (map['loadPower'] as num?)?.toDouble() ?? 0.0,
      batterySoc: (map['batterySoc'] as num?)?.toDouble() ?? 0.0,
      pvVoltage: (map['pvVoltage'] as num?)?.toDouble() ?? 0.0,
      gridVoltage: (map['gridVoltage'] as num?)?.toDouble() ?? 0.0,
      batteryVoltage: (map['batteryVoltage'] as num?)?.toDouble() ?? 0.0,
      loadPercentage: (map['loadPercentage'] as num?)?.toDouble() ?? 0.0,
      workingMode: map['workingMode']?.toString() ?? 'N/A',
      deviceSn: map['deviceSn']?.toString() ?? '',
      currentModeStr: map['currentModeStr']?.toString() ?? '',
      rawFields: rawFields,
    );
  }
}

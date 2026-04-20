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

  static double getRealSoc(
      double reportedSoc, double voltage, double loadPower) {
    // Якщо інвертор має BMS-кабель, reportedSoc точний, повертаємо його.
    // Якщо ні, обчислюємо приблизно за напругою 16S:
    if (voltage <= 0) return reportedSoc;

    // Компенсуємо просадку напруги під навантаженням (приблизно 0.5V на кожні 2kW)
    var compensatedVoltage = voltage + (loadPower / 2000.0) * 0.5;

    if (compensatedVoltage >= 53.5) return 100.0;
    if (compensatedVoltage >= 53.0) return 90.0;
    if (compensatedVoltage >= 52.8) return 80.0;
    if (compensatedVoltage >= 52.5) return 60.0;
    if (compensatedVoltage >= 52.0) return 40.0;
    if (compensatedVoltage >= 51.2) return 20.0;
    if (compensatedVoltage >= 49.0) return 10.0;
    if (compensatedVoltage < 48.0) return 0.0;

    return reportedSoc;
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

  factory InverterData.fromJson(
      Map<String, dynamic> json, String deviceSn, String currentModeStr) {
    final fields = json['deviceAttributeState']?['fields'] ?? {};

    // Усі потужності зводимо до Ват (W)
    var pv = _parseDouble(fields['pvInputPower'] ?? fields['generationPower']);
    var load = _parseDouble(fields['acOutputActivePower'], isKw: true);
    var soc = _parseDouble(fields['batteryCapacity']);
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
      currentModeStr: currentModeStr,
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

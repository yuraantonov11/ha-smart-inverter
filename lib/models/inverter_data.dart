class InverterData {
  final double pvPower;
  final double gridPower;
  final double batteryPower;
  final double loadPower;
  final double batterySoc;

  // Додаткові детальні дані
  final double pvVoltage;
  final double gridVoltage;
  final double batteryVoltage;
  final double loadPercentage;
  final String workingMode;

  final String deviceSn;
  final String currentModeStr;

  // Зберігаємо абсолютно всі поля для вкладки "Деталі"
  final Map<String, dynamic> rawFields;

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

    var pv = _parseDouble(fields['pvInputPower'] ?? fields['generationPower']);
    var load = _parseDouble(fields['acOutputActivePower'], isKw: true);
    var soc = _parseDouble(fields['batteryCapacity']);

    var gridVolt = _parseDouble(fields['acInputVoltage']);
    var loadPct = _parseDouble(fields['loadPercentage']);
    var batVolt = _parseDouble(fields['batteryVoltage']);
    var pvVolt = _parseDouble(fields['pvInputVoltage']);

    // Розрахунок мережі (споживання або віддача)
    var grid = gridVolt > 0 ? (load - pv > 0 ? load - pv : 0.0) : 0.0;

    // Струм батареї (заряд або розряд)
    var batCharge = _parseDouble(fields['batteryChargingCurrent']);
    var batDischarge = _parseDouble(fields['batteryDischargeCurrent']);
    var batPower = (batCharge > 0)
        ? batCharge * batVolt
        : (batDischarge > 0 ? -batDischarge * batVolt : 0.0);

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
      workingMode: _parseString(fields['workingStates']),
      deviceSn: deviceSn,
      currentModeStr: currentModeStr,
      rawFields: fields, // Зберігаємо всі дані
    );
  }
}

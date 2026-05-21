import 'dart:io';

import 'package:intl/intl.dart';
import 'package:path_provider/path_provider.dart';

import '../models/inverter_data.dart';
import '../providers/app_provider.dart';
import 'formatters.dart';

String buildDashboardDiagnosticsSnapshot(
  AppStateProvider provider,
  InverterData data,
) {
  final hems = provider.hemsService.buildDiagnosticsSnapshot();
  final weatherPerf = provider.weatherService.getPerformanceSnapshot();
  final buffer = StringBuffer()
    ..writeln('Smart Inverter diagnostics snapshot')
    ..writeln('Generated: ${_formatDateTime(hems.capturedAt)}')
    ..writeln('Status: ${_oneLine(provider.statusMessage)}')
    ..writeln(
      'Last realtime update: ${_formatNullableDateTime(provider.lastSuccessfulRealtimeAt)}',
    )
    ..writeln('App version: ${provider.appVersionLabel}')
    ..writeln('Device SN: ${data.deviceSn}')
    ..writeln('Working mode: ${data.workingMode}')
    ..writeln('Current mode: ${data.currentModeStr}')
    ..writeln('Offline: ${provider.isInverterOffline ? 'yes' : 'no'}')
    ..writeln(
      'Control raw: output=${_rawField(data.rawFields, 'outputSourcePriority')} '
      'charger=${_rawField(data.rawFields, 'chargerSourcePriority')}',
    )
    ..writeln(
      'Power: PV ${Formatters.formatPower(data.pvPower)} | '
      'Grid ${Formatters.formatPower(data.gridPower)} | '
      'Battery ${Formatters.formatPower(data.batteryPower)} | '
      'Load ${Formatters.formatPower(data.loadPower)}',
    )
    ..writeln(
      'Battery: SOC ${data.batterySoc.toStringAsFixed(1)}% | '
      'Voltage ${data.batteryVoltage.toStringAsFixed(1)} V | '
      'Load ${data.loadPercentage.toStringAsFixed(1)}%',
    )
    ..writeln(
      'Economics: daily ${provider.service.dailyEnergy.toStringAsFixed(1)} kWh | '
      'total ${provider.service.totalEnergy.toStringAsFixed(1)} kWh | '
      'CO2 ${provider.service.co2Reduction.toStringAsFixed(1)} kg',
    )
    ..writeln(
      'Month economics: load ${_formatKwh(provider.monthLoadKwh)} | '
      'grid ${_formatKwh(provider.monthGridKwh)} | '
      'self-consumed ${_formatKwh(provider.monthSelfConsumedKwh)} | '
      'saved ${_formatMoney(provider.monthSavedUah)} | '
      'payable ${_formatMoney(provider.monthToPayUah)}',
    )
    ..writeln(
      'HEMS: mode ${_smartModeLabel(provider.smartMode)} | '
      'strategy ${provider.hemsStrategy.name} | '
      'windows ${provider.useAstronomicalWindows ? 'astronomical' : 'manual'}',
    )
    ..writeln(
      'HEMS windows: day ${hems.dayStartHour}:00 | '
      'evening ${hems.eveningStartHour}:00 | '
      'night ${hems.nightStartHour}:00',
    )
    ..writeln(
      'HEMS tuning: reserve ${hems.adaptiveReserveSoc.toStringAsFixed(1)}% | '
      'pv enter ${hems.adaptivePvSurplusEnterW.toStringAsFixed(0)} W | '
      'dwell ${hems.adaptiveDwell.inMinutes} min',
    )
    ..writeln(
      'Tariff forecast: ${hems.tariffForecastActive ? 'active' : 'inactive'} | '
      'cheap now ${hems.chargingCheapNow ? 'yes' : 'no'}'
      '${hems.nextCheapChargingWindow == null ? '' : ' | next ${_formatDateTime(hems.nextCheapChargingWindow!)}'}',
    )
    ..writeln(
      'Weather perf: local hit/miss ${weatherPerf.localCacheHits}/${weatherPerf.localCacheMisses} '
      '(join ${weatherPerf.localInFlightJoins}) avg ${weatherPerf.localAvgMs}ms | '
      'daily hit/miss ${weatherPerf.dailyCacheHits}/${weatherPerf.dailyCacheMisses} '
      '(join ${weatherPerf.dailyInFlightJoins}) avg ${weatherPerf.dailyAvgMs}ms',
    );

  return buffer.toString().trimRight();
}

String _oneLine(String value) {
  final trimmed = value.trim();
  if (trimmed.isEmpty) return 'n/a';
  return trimmed.replaceAll(RegExp(r'\s+'), ' ');
}

String _formatNullableDateTime(DateTime? value) {
  if (value == null) return 'n/a';
  return _formatDateTime(value);
}

String _formatDateTime(DateTime value) =>
    DateFormat('yyyy-MM-dd HH:mm:ss').format(value);

String _formatKwh(double? value) =>
    value == null ? 'n/a' : '${value.toStringAsFixed(1)} kWh';

String _formatMoney(double? value) =>
    value == null ? 'n/a' : '${value.toStringAsFixed(1)} UAH';

String _smartModeLabel(int mode) {
  switch (mode) {
    case 1:
      return 'Night arbitrage';
    case 2:
      return 'Storm / Reserve';
    default:
      return 'Adaptive';
  }
}

String _rawField(Map<String, dynamic> fields, String key) {
  final field = fields[key];
  if (field is Map) {
    final value = field['value'] ?? field['valueDisplay'];
    if (value != null) return value.toString();
  }
  return 'n/a';
}

Future<String?> appendDashboardDiagnosticsSnapshotToFile(
    String snapshot) async {
  try {
    final docs = await getApplicationDocumentsDirectory();
    final dir = Directory('${docs.path}/siseli_debug_logs');
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }

    final now = DateTime.now();
    final stamp = DateFormat('yyyy-MM').format(now);
    final file = File('${dir.path}/dashboard_diagnostics_$stamp.log');
    final divider = '-' * 72;
    await file.writeAsString(
      '$divider\n$snapshot\n$divider\n',
      mode: FileMode.append,
    );
    return file.path;
  } catch (_) {
    return null;
  }
}

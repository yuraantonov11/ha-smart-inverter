import 'package:flutter/material.dart';

import '../models/inverter_data.dart';
import '../providers/app_provider.dart';

class DetailsTab extends StatelessWidget {
  final InverterData data;
  final AppStateProvider provider;

  const DetailsTab({super.key, required this.data, required this.provider});

  // ---------------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------------
  @override
  Widget build(BuildContext context) {
    final fullConfigs = data.rawFields['fullConfigs'];
    final configList = _extractConfigList(fullConfigs);
    configList.sort((a, b) {
      final orderA = (a['orderNumber'] as num?)?.toInt() ?? 999;
      final orderB = (b['orderNumber'] as num?)?.toInt() ?? 999;
      // Descending by orderNumber (higher = more important)
      return orderB.compareTo(orderA);
    });

    // Realtime fields — everything except the 'fullConfigs' sub-map
    final realtimeFields = Map<String, dynamic>.from(data.rawFields)
      ..remove('fullConfigs');
    final stateKeys = realtimeFields.keys.toList()..sort();

    return Stack(
      children: [
        ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // ── Settings header ───────────────────────────────────────────
            _SectionHeader(
              icon: Icons.tune_rounded,
              title: 'Налаштування інвертора',
              trailing: (provider.isSettingChanging || provider.isConfigLoading)
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : IconButton(
                      icon: const Icon(Icons.refresh_rounded, size: 18),
                      tooltip: 'Оновити налаштування',
                      onPressed: () => provider.refreshDeviceConfigs(),
                    ),
            ),
            const SizedBox(height: 8),
            if (configList.isEmpty)
              Card(
                child: ListTile(
                  leading: provider.isConfigLoading
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.hourglass_empty_rounded),
                  title: const Text('Налаштування завантажуються…'),
                  subtitle: Text(provider.isConfigLoading
                      ? 'Очікуємо відповідь інвертора…'
                      : 'Натисніть 🔄 для завантаження'),
                ),
              )
            else
              ...configList.map((cfg) => _buildConfigTile(context, cfg)),

            const SizedBox(height: 20),

            // ── Realtime readings header ───────────────────────────────────
            _SectionHeader(
              icon: Icons.monitor_heart_rounded,
              title: 'Поточні показники',
            ),
            const SizedBox(height: 8),
            ...stateKeys.map(
                (key) => _buildStateTile(context, key, realtimeFields[key])),
            const SizedBox(height: 16),
          ],
        ),

        // Translucent overlay while a setting change is in progress
        if (provider.isSettingChanging)
          const Positioned.fill(
            child: ColoredBox(
              color: Color(0x44000000),
              child: Center(child: CircularProgressIndicator()),
            ),
          ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // Data helpers
  // ---------------------------------------------------------------------------

  /// Converts the raw fullConfigs map (configAttributeStates or compact {t,v})
  /// into a list of config entries with at least {key, value, valueDisplay, name}.
  List<Map<String, dynamic>> _extractConfigList(dynamic fullConfigs) {
    if (fullConfigs == null || fullConfigs is! Map) return [];
    final list = <Map<String, dynamic>>[];
    for (final entry in (fullConfigs as Map<String, dynamic>).entries) {
      if (entry.value == null) continue;
      if (entry.value is Map<String, dynamic>) {
        final cfg =
            Map<String, dynamic>.from(entry.value as Map<String, dynamic>);
        cfg['key'] ??= entry.key;
        list.add(cfg);
      } else {
        // Compact format: entry.value is a primitive
        list.add({
          'key': entry.key,
          'value': entry.value,
          'valueDisplay': entry.value?.toString() ?? '',
          'nameDisplay': entry.key,
        });
      }
    }
    return list;
  }

  // ---------------------------------------------------------------------------
  // Widget builders
  // ---------------------------------------------------------------------------

  Widget _buildConfigTile(BuildContext context, Map<String, dynamic> cfg) {
    final key = cfg['key']?.toString() ?? '';
    final name =
        cfg['nameDisplay']?.toString() ?? cfg['name']?.toString() ?? key;
    final valueDisplay =
        cfg['valueDisplay']?.toString() ?? cfg['value']?.toString() ?? '—';
    final unit = cfg['unit']?.toString() ?? '';
    final displayText = (unit.isNotEmpty && unit != 'null')
        ? '$valueDisplay $unit'
        : valueDisplay;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: Icon(
          _iconForSetting(key),
          color: Theme.of(context).colorScheme.primary,
          size: 22,
        ),
        title: Text(name, style: const TextStyle(fontSize: 13)),
        subtitle: Text(
          displayText,
          style: TextStyle(
            fontWeight: FontWeight.w700,
            fontSize: 15,
            color: Theme.of(context).colorScheme.secondary,
          ),
        ),
        trailing: provider.isSettingChanging
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.edit_rounded, size: 18),
        onTap: provider.isSettingChanging
            ? null
            : () => _showEditDialog(context, key, name, cfg),
      ),
    );
  }

  Widget _buildStateTile(BuildContext context, String key, dynamic fieldData) {
    var name = key;
    var value = '—';
    var unit = '';
    if (fieldData is Map) {
      name = fieldData['nameDisplay']?.toString() ??
          fieldData['name']?.toString() ??
          key;
      value = fieldData['valueDisplay']?.toString() ??
          fieldData['value']?.toString() ??
          '—';
      unit = fieldData['unit']?.toString() ?? '';
    } else if (fieldData != null) {
      value = fieldData.toString();
    }

    return Card(
      margin: const EdgeInsets.only(bottom: 6),
      child: ListTile(
        dense: true,
        title: Text(name, style: const TextStyle(fontSize: 13)),
        trailing: Text(
          (unit.isNotEmpty && unit != 'null') ? '$value $unit' : value,
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w700,
            color: Theme.of(context).colorScheme.primary,
          ),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Edit dialog
  // ---------------------------------------------------------------------------

  void _showEditDialog(
    BuildContext context,
    String key,
    String name,
    Map<String, dynamic> cfg,
  ) {
    final valueType = (cfg['valueType'] as num?)?.toInt() ?? 1;
    final currentValue = cfg['value']?.toString() ?? '';
    final unit = cfg['unit']?.toString() ?? '';

    final presets = _getPresets(key);
    var selected = currentValue;
    // Ensure selected is a valid preset option
    if (presets != null && !presets.any((e) => e.value == selected)) {
      selected = presets.first.value ?? '';
    }
    final controller = TextEditingController(text: currentValue);

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setState) => AlertDialog(
          title: Text(name, style: const TextStyle(fontSize: 16)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (unit.isNotEmpty && unit != 'null')
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Одиниця виміру: $unit',
                    style: TextStyle(
                      fontSize: 12,
                      color: Theme.of(context).colorScheme.secondary,
                    ),
                  ),
                ),
              if (presets != null)
                DropdownButtonFormField<String>(
                  initialValue: selected,
                  items: presets,
                  onChanged: (val) {
                    if (val != null) setState(() => selected = val);
                  },
                  decoration: const InputDecoration(
                    border: OutlineInputBorder(),
                    labelText: 'Оберіть значення',
                  ),
                )
              else
                TextField(
                  controller: controller,
                  keyboardType: valueType == 1
                      ? const TextInputType.numberWithOptions(decimal: true)
                      : TextInputType.text,
                  decoration: InputDecoration(
                    border: const OutlineInputBorder(),
                    labelText: 'Нове значення',
                    suffixText:
                        (unit.isNotEmpty && unit != 'null') ? unit : null,
                  ),
                ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Скасувати'),
            ),
            FilledButton.icon(
              icon: const Icon(Icons.check_rounded, size: 16),
              label: const Text('Застосувати'),
              onPressed: () async {
                final newValue =
                    presets != null ? selected : controller.text.trim();
                if (newValue.isEmpty) return;
                Navigator.pop(ctx);
                await provider.changeInverterSetting(key, newValue);
              },
            ),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Presets for known settings
  // ---------------------------------------------------------------------------

  List<DropdownMenuItem<String>>? _getPresets(String key) {
    const enableDisable = [
      DropdownMenuItem(value: '0', child: Text('Вимкнено (Disable)')),
      DropdownMenuItem(value: '1', child: Text('Увімкнено (Enable)')),
    ];
    const onOff = [
      DropdownMenuItem(value: '0', child: Text('OFF')),
      DropdownMenuItem(value: '1', child: Text('ON')),
    ];

    switch (key) {
      case 'outputSourcePrioritySetting':
        return const [
          DropdownMenuItem(value: '0', child: Text('USB — Пріоритет мережі')),
          DropdownMenuItem(value: '1', child: Text('SUB')),
          DropdownMenuItem(value: '2', child: Text('SBU — Пріоритет сонця')),
        ];
      case 'chargerSourcePrioritySetting':
        return const [
          DropdownMenuItem(value: '0', child: Text('CSO — Спочатку сонце')),
          DropdownMenuItem(value: '1', child: Text('SNU — Сонце + Мережа')),
          DropdownMenuItem(value: '2', child: Text('OSO — Тільки сонце')),
          DropdownMenuItem(value: '3', child: Text('Тільки мережа')),
        ];
      case 'outputFrequencySetting':
        return const [
          DropdownMenuItem(value: '0', child: Text('50 Гц')),
          DropdownMenuItem(value: '1', child: Text('60 Гц')),
        ];
      case 'acInputRangeSetting':
        return const [
          DropdownMenuItem(value: '0', child: Text('APL — Широкий діапазон')),
          DropdownMenuItem(value: '1', child: Text('UPS — Вузький діапазон')),
        ];
      case 'outputVoltageSettings':
        return const [
          DropdownMenuItem(value: '208', child: Text('208 V')),
          DropdownMenuItem(value: '220', child: Text('220 V')),
          DropdownMenuItem(value: '230', child: Text('230 V')),
          DropdownMenuItem(value: '240', child: Text('240 V')),
        ];
      case 'settingBatteryType':
        return const [
          DropdownMenuItem(value: '0', child: Text('AGM')),
          DropdownMenuItem(value: '1', child: Text('Flooded (залитий)')),
          DropdownMenuItem(value: '2', child: Text('User (власні параметри)')),
          DropdownMenuItem(value: '3', child: Text('LIB (Літієвий)')),
          DropdownMenuItem(value: '4', child: Text('Life')),
        ];
      case 'batteryPowerLimitingSetting':
      case 'cutOffVoltBatteryForSmartMainLoad':
      case 'rgbOnAndOffControlSetting':
        return onOff;
      case 'batteryEqualizationSetting':
      case 'batteryEqualizationActivateImmediate':
      case 'overLoadRestartSetting':
      case 'overTemperatureAutoRestartSetting':
      case 'recordFaultCodeSetting':
      case 'transferToBypassFromOverload':
      case 'beepsWhilePrimarySourceInterupt':
      case 'autoReturnToDefaultDisplayScreen':
      case 'lcdBacklightSetting':
      case 'buzzerAlarmSetting':
        return enableDisable;
      default:
        return null; // Numeric text field
    }
  }

  // ---------------------------------------------------------------------------
  // Icon helper
  // ---------------------------------------------------------------------------

  IconData _iconForSetting(String key) {
    final k = key.toLowerCase();
    if (k.contains('output') || k.contains('voltage')) {
      return Icons.electric_bolt_rounded;
    }
    if (k.contains('battery') || k.contains('lowdc') || k.contains('soc')) {
      return Icons.battery_charging_full_rounded;
    }
    if (k.contains('charger') ||
        k.contains('charging') ||
        k.contains('charge')) {
      return Icons.charging_station_rounded;
    }
    if (k.contains('buzzer') || k.contains('beep') || k.contains('alarm')) {
      return Icons.volume_up_rounded;
    }
    if (k.contains('lcd') ||
        k.contains('display') ||
        k.contains('rgb') ||
        k.contains('backlight')) {
      return Icons.display_settings_rounded;
    }
    if (k.contains('temperature')) return Icons.thermostat_rounded;
    if (k.contains('grid') || k.contains('utility') || k.contains('input')) {
      return Icons.power_input_rounded;
    }
    if (k.contains('frequency')) return Icons.graphic_eq_rounded;
    if (k.contains('overload') || k.contains('overtemperature')) {
      return Icons.warning_amber_rounded;
    }
    if (k.contains('equalization')) return Icons.balance_rounded;
    return Icons.settings_rounded;
  }
}

// ---------------------------------------------------------------------------
// Helper widget
// ---------------------------------------------------------------------------

class _SectionHeader extends StatelessWidget {
  final IconData icon;
  final String title;
  final Widget? trailing;

  const _SectionHeader({
    required this.icon,
    required this.title,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, color: Theme.of(context).colorScheme.primary, size: 20),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            title,
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.bold),
          ),
        ),
        if (trailing != null) trailing!,
      ],
    );
  }
}

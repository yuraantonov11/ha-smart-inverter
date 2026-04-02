import 'package:flutter/material.dart';
import '../providers/app_provider.dart';

class ControlPanel extends StatelessWidget {
  final AppStateProvider provider;

  const ControlPanel({super.key, required this.provider});

  void _showSettingsModal(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).cardColor,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: EdgeInsets.only(
                bottom: MediaQuery.of(context).viewInsets.bottom),
            child: _SettingsModal(provider: provider),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final isEn = provider.isEn;
    final currentOutputPriority = provider
            .data?.rawFields['outputSourcePriority']?['value']
            ?.toString() ??
        '2';

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: isDark
            ? []
            : [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.05), blurRadius: 10)
              ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(isEn ? 'Inverter Mode' : 'Режим інвертора',
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.bold)),
              IconButton(
                icon: const Icon(Icons.settings_outlined, color: Colors.grey),
                tooltip: isEn ? 'Advanced Settings' : 'Розширені налаштування',
                onPressed: () => _showSettingsModal(context),
              )
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: _SwitchCard(
                  title: isEn ? 'SOLAR (SBU)' : 'СОНЦЕ (SBU)',
                  icon: Icons.wb_sunny_rounded,
                  isActive: currentOutputPriority == '2',
                  activeColor: Colors.amber,
                  onTap: () => provider.setMode(2),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: _SwitchCard(
                  title: isEn ? 'GRID (USB)' : 'МЕРЕЖА (USB)',
                  icon: Icons.power_rounded,
                  isActive: currentOutputPriority == '0',
                  activeColor: Colors.blueAccent,
                  onTap: () => provider.setMode(0),
                ),
              ),
            ],
          )
        ],
      ),
    );
  }
}

class _SwitchCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final bool isActive;
  final Color activeColor;
  final VoidCallback onTap;

  const _SwitchCard(
      {required this.title,
      required this.icon,
      required this.isActive,
      required this.activeColor,
      required this.onTap});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final baseColor = isDark ? Colors.white : Colors.black;

    return GestureDetector(
      onTap: isActive ? null : onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 300),
        padding: const EdgeInsets.symmetric(vertical: 20),
        decoration: BoxDecoration(
          color: isActive
              ? activeColor.withValues(alpha: 0.15)
              : (isDark ? Colors.black26 : Colors.grey.shade100),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
              color: isActive
                  ? activeColor
                  : (isDark ? Colors.white12 : Colors.grey.shade300),
              width: isActive ? 2 : 1),
        ),
        child: Column(
          children: [
            Icon(icon,
                color:
                    isActive ? activeColor : baseColor.withValues(alpha: 0.4),
                size: 36),
            const SizedBox(height: 12),
            Text(title,
                style: TextStyle(
                    color: isActive
                        ? activeColor
                        : baseColor.withValues(alpha: 0.6),
                    fontWeight: isActive ? FontWeight.bold : FontWeight.w500)),
          ],
        ),
      ),
    );
  }
}

class _SettingsModal extends StatelessWidget {
  final AppStateProvider provider;

  const _SettingsModal({required this.provider});

  @override
  Widget build(BuildContext context) {
    final isEn = provider.isEn;
    final fields = provider.data?.rawFields ?? {};
    final outputPriority =
        fields['outputSourcePriority']?['value']?.toString() ?? '2';
    final chargerPriority =
        fields['chargerSourcePriority']?['value']?.toString() ?? '0';

    return Padding(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(isEn ? 'Advanced Settings' : 'Розширені налаштування',
              style:
                  const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          const SizedBox(height: 24),
          Text(isEn ? 'Output Source Priority' : 'Пріоритет виходу (Output)',
              style: const TextStyle(fontSize: 14, color: Colors.grey)),
          const SizedBox(height: 8),
          _buildDropdown(
            context,
            value: outputPriority,
            items: [
              DropdownMenuItem(
                  value: '0',
                  child: Text(isEn
                      ? 'Utility First (USB)'
                      : 'Мережа (Utility First / USB)')),
              DropdownMenuItem(
                  value: '1',
                  child: Text(isEn
                      ? 'Solar First (SUB)'
                      : 'Сонце (Solar First / SUB)')),
              DropdownMenuItem(value: '2', child: Text('SBU Priority')),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.changeSetting('outputSourcePrioritySetting', val);
                Navigator.pop(context);
              }
            },
          ),
          const SizedBox(height: 24),
          Text(isEn ? 'Charger Source Priority' : 'Пріоритет зарядки (Charger)',
              style: const TextStyle(fontSize: 14, color: Colors.grey)),
          const SizedBox(height: 8),
          _buildDropdown(
            context,
            value: chargerPriority,
            items: [
              DropdownMenuItem(
                  value: '0',
                  child: Text(
                      isEn ? 'Solar First (CSO)' : 'Сонце пріоритет (CSO)')),
              DropdownMenuItem(
                  value: '1',
                  child: Text(
                      isEn ? 'Solar + Utility (SNU)' : 'Сонце + Мережа (SNU)')),
              DropdownMenuItem(
                  value: '2',
                  child:
                      Text(isEn ? 'Only Solar (OSO)' : 'Тільки Сонце (OSO)')),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.changeSetting('chargerSourcePrioritySetting', val);
                Navigator.pop(context);
              }
            },
          ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }

  Widget _buildDropdown(BuildContext context,
      {required String value,
      required List<DropdownMenuItem<String>> items,
      required void Function(String?) onChanged}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: Theme.of(context).scaffoldBackgroundColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.grey.withValues(alpha: 0.2)),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: items.any((item) => item.value == value)
              ? value
              : items.first.value,
          isExpanded: true,
          icon: const Icon(Icons.arrow_drop_down_rounded, color: Colors.amber),
          items: items,
          onChanged: onChanged,
        ),
      ),
    );
  }
}

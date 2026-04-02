import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';

class AutomationTab extends StatelessWidget {
  final AppStateProvider provider;

  const AutomationTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    // Звертаємося до файлу локалізації
    final l10n = AppLocalizations.of(context)!;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 12, left: 4),
          child: Text(l10n.smartModes,
              style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                  color: Colors.grey)),
        ),
        _buildRadioCard(
          context,
          title: l10n.modeOff,
          subtitle: '',
          icon: Icons.pan_tool_rounded,
          color: Colors.grey,
          value: 0,
        ),
        const SizedBox(height: 16),
        _buildRadioCard(
          context,
          title: l10n.modeWinter,
          subtitle: l10n.modeWinterDesc,
          icon: Icons.ac_unit_rounded,
          color: Colors.lightBlueAccent,
          value: 1,
        ),
        const SizedBox(height: 16),
        _buildRadioCard(
          context,
          title: l10n.modeSummer,
          subtitle: l10n.modeSummerDesc,
          icon: Icons.wb_sunny_rounded,
          color: Colors.amber,
          value: 2,
        ),
      ],
    );
  }

  Widget _buildRadioCard(BuildContext context,
      {required String title,
      required String subtitle,
      required IconData icon,
      required Color color,
      required int value}) {
    final isSelected = provider.smartMode == value;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return GestureDetector(
      onTap: () => provider.setSmartMode(value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          color: isSelected
              ? color.withValues(alpha: 0.1)
              : Theme.of(context).cardColor,
          borderRadius: BorderRadius.circular(24),
          border: Border.all(
              color: isSelected ? color : Colors.transparent, width: 2),
          boxShadow: isDark
              ? []
              : [
                  BoxShadow(
                      color: Colors.black.withValues(alpha: 0.05),
                      blurRadius: 10)
                ],
        ),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.15),
                    shape: BoxShape.circle),
                child: Icon(icon, color: color),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 8),
                    Text(title,
                        style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                            color: isSelected ? color : null)),
                    if (subtitle.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Text(subtitle,
                          style: const TextStyle(
                              fontSize: 13, height: 1.4, color: Colors.grey)),
                    ]
                  ],
                ),
              ),
              // Кастомна іконка замість deprecated Radio
              Padding(
                padding: const EdgeInsets.only(top: 8.0),
                child: Icon(
                  isSelected
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked,
                  color: isSelected ? color : Colors.grey,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

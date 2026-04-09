// ignore_for_file: deprecated_member_use

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';

class AutomationTab extends StatelessWidget {
  final AppStateProvider provider;

  const AutomationTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 20, left: 4),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                l10n.hemsTitle,
                style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                  color: isDark ? Colors.white : Colors.black87,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                l10n.hemsSubtitle,
                style: TextStyle(
                  fontSize: 14,
                  color: isDark ? Colors.white60 : Colors.black54,
                ),
              ),
            ],
          ),
        ),

        // 1. Адаптивний режим
        _SmartModeCard(
          title: l10n.modeAdaptive,
          subtitle: l10n.modeAdaptiveSubtitle,
          icon: Icons.auto_awesome_rounded,
          color: Colors.blueAccent,
          value: 0,
          groupValue: provider.smartMode,
          onChanged: (val) => provider.setSmartMode(val!),
          tooltipText: l10n.modeAdaptiveDesc,
        ),
        const SizedBox(height: 16),

        // 2. Нічний арбітраж
        _SmartModeCard(
          title: l10n.modeArbitrage,
          subtitle: l10n.modeArbitrageSubtitle,
          icon: Icons.nightlight_round,
          color: Colors.deepPurpleAccent,
          value: 1,
          groupValue: provider.smartMode,
          onChanged: (val) => provider.setSmartMode(val!),
          tooltipText: l10n.modeArbitrageDesc,
        ),
        const SizedBox(height: 16),

        // 3. Шторм / Резерв
        _SmartModeCard(
          title: l10n.modeStorm,
          subtitle: l10n.modeStormSubtitle,
          icon: Icons.thunderstorm_rounded,
          color: Colors.orangeAccent,
          value: 2,
          groupValue: provider.smartMode,
          onChanged: (val) => provider.setSmartMode(val!),
          tooltipText: l10n.modeStormDesc,
        ),
      ],
    );
  }
}

class _SmartModeCard extends StatelessWidget {
  final String title;
  final String subtitle;
  final IconData icon;
  final Color color;
  final int value;
  final int groupValue;
  final ValueChanged<int?> onChanged;
  final String tooltipText;

  const _SmartModeCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.color,
    required this.value,
    required this.groupValue,
    required this.onChanged,
    required this.tooltipText,
  });

  void _showInfo(BuildContext context) {
    // ДОДАНО: Отримуємо локалізацію для цього контексту
    final l10n = AppLocalizations.of(context)!;

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Row(
          children: [
            Icon(icon, color: color),
            const SizedBox(width: 10),
            Flexible(child: Text(title)),
          ],
        ),
        content: Text(tooltipText),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            // ВИПРАВЛЕНО: Прибрали const, бо l10n генерується динамічно
            child: Text(l10n.gotIt),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final isSelected = value == groupValue;

    return InkWell(
      onTap: () => onChanged(value),
      borderRadius: BorderRadius.circular(20),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 250),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: isDark
              ? (isSelected
                  ? color.withValues(alpha: 0.15)
                  : Colors.white.withValues(alpha: 0.05))
              : (isSelected ? color.withValues(alpha: 0.1) : Colors.grey[100]),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color:
                isSelected ? color.withValues(alpha: 0.5) : Colors.transparent,
            width: 2,
          ),
          boxShadow: [
            if (isSelected)
              BoxShadow(
                color: color.withValues(alpha: 0.2),
                blurRadius: 10,
                offset: const Offset(0, 4),
              ),
          ],
        ),
        child: Row(
          children: [
            // Іконка в колі
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: isSelected ? color : color.withValues(alpha: 0.1),
                shape: BoxShape.circle,
              ),
              child: Icon(icon,
                  color: isSelected ? Colors.white : color, size: 28),
            ),
            const SizedBox(width: 16),

            // Текстовий блок
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 17,
                      color: isDark ? Colors.white : Colors.black87,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    subtitle,
                    style: TextStyle(
                      color: isDark ? Colors.white60 : Colors.black54,
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
            ),

            // Кнопка інфо
            IconButton(
              icon: Icon(
                Icons.help_outline_rounded,
                color: isSelected ? color : Colors.grey,
                size: 22,
              ),
              onPressed: () => _showInfo(context),
            ),

            // Радіо-кнопка
            // (Попередження про deprecation тепер ігнорується на рівні файлу)
            Radio<int>(
              value: value,
              groupValue: groupValue,
              onChanged: onChanged,
              activeColor: color,
            ),
          ],
        ),
      ),
    );
  }
}

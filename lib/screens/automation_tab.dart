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
          child: Text(l10n.hemsTitle,
              style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: isDark ? Colors.white70 : Colors.grey[800])),
        ),
        _SmartModeCard(
          title: l10n.modeAdaptive,
          subtitle: l10n.modeAdaptiveSubtitle,
          icon: Icons.auto_awesome,
          color: Colors.blueAccent,
          value: 0,
          groupValue: provider.smartMode,
          onChanged: (val) => provider.setSmartMode(val!),
          tooltipText: l10n.modeAdaptiveDesc,
        ),
        const SizedBox(height: 16),
        _SmartModeCard(
          title: l10n.modeArbitrage,
          subtitle: l10n.modeArbitrageSubtitle,
          icon: Icons.nights_stay_rounded,
          color: Colors.purpleAccent,
          value: 1,
          groupValue: provider.smartMode,
          onChanged: (val) => provider.setSmartMode(val!),
          tooltipText: l10n.modeArbitrageDesc,
        ),
        const SizedBox(height: 16),
        _SmartModeCard(
          title: l10n.modeStorm,
          subtitle: l10n.modeStormSubtitle,
          icon: Icons.thunderstorm_rounded,
          color: Colors.amber,
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
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (context) => Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: color, size: 28),
                const SizedBox(width: 12),
                Expanded(
                    child: Text(title,
                        style: const TextStyle(
                            fontSize: 20, fontWeight: FontWeight.bold))),
              ],
            ),
            const SizedBox(height: 16),
            Text(tooltipText,
                style: const TextStyle(fontSize: 15, height: 1.5)),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: () => Navigator.pop(context),
                style: ElevatedButton.styleFrom(
                  backgroundColor: color,
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12)),
                ),
                child: Text(l10n.gotIt,
                    style: const TextStyle(color: Colors.white)),
              ),
            )
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isSelected = value == groupValue;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return GestureDetector(
      onTap: () => onChanged(value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 300),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: isSelected
              ? color.withValues(alpha: 0.1)
              : Theme.of(context).cardColor,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: isSelected ? color : Colors.transparent,
            width: 2,
          ),
          boxShadow: isDark
              ? []
              : [
                  BoxShadow(
                      color: Colors.black.withValues(alpha: 0.05),
                      blurRadius: 10)
                ],
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                // Використовуємо ваш стиль з alpha
                color: color.withValues(alpha: 0.15),
                shape: BoxShape.circle,
              ),
              child: Icon(icon, color: color, size: 24),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                  const SizedBox(height: 4),
                  Text(subtitle,
                      style: const TextStyle(color: Colors.grey, fontSize: 13)),
                ],
              ),
            ),
            IconButton(
              icon: const Icon(Icons.info_outline_rounded, color: Colors.grey),
              onPressed: () => _showInfo(context),
            ),
            // ВИПРАВЛЕНИЙ RADIO:
            Radio<int>(
              value: value, // Унікальне значення цього елемента
              activeColor: color,
              // groupValue та onChanged тут БІЛЬШЕ НЕ ПОТРІБНІ
            ),
          ],
        ),
      ),
    );
  }
}

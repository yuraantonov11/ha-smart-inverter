// ignore_for_file: deprecated_member_use

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';

class AutomationTab extends StatelessWidget {
  final AppStateProvider provider;

  const AutomationTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    return ListView(
      padding: const EdgeInsets.all(AppTheme.spacingXL),
      children: [
        AppSectionTitle(
          title: l10n.hemsTitle,
          subtitle: l10n.hemsSubtitle,
          icon: Icons.tune_rounded,
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
        const SizedBox(height: AppTheme.spacingL),

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
        const SizedBox(height: AppTheme.spacingL),

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
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTheme.radiusXL),
        ),
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(AppTheme.spacingS),
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.14),
                borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              ),
              child: Icon(icon, color: color, size: 20),
            ),
            const SizedBox(width: AppTheme.spacingM),
            Flexible(child: Text(title)),
          ],
        ),
        content: SingleChildScrollView(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: _buildTooltipContent(context),
            ),
          ),
        ),
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

  List<Widget> _buildTooltipContent(BuildContext context) {
    final theme = Theme.of(context);
    final sections = tooltipText
        .split('\n\n')
        .map((part) => part.trim())
        .where((part) => part.isNotEmpty)
        .toList();

    final widgets = <Widget>[];

    for (var sectionIndex = 0; sectionIndex < sections.length; sectionIndex++) {
      final lines = sections[sectionIndex]
          .split('\n')
          .map((line) => line.trim())
          .where((line) => line.isNotEmpty)
          .toList();

      if (lines.isEmpty) continue;

      final firstLine = lines.first;
      final hasHeading = firstLine.endsWith(':') && !firstLine.startsWith('•');

      if (hasHeading) {
        widgets.add(
          Padding(
            padding: EdgeInsets.only(bottom: lines.length > 1 ? 8 : 0),
            child: Text(
              firstLine.substring(0, firstLine.length - 1),
              style: theme.textTheme.titleSmall?.copyWith(
                fontWeight: FontWeight.w700,
                color: color,
              ),
            ),
          ),
        );
      }

      final contentLines = hasHeading ? lines.skip(1).toList() : lines;
      for (final line in contentLines) {
        if (line.startsWith('•')) {
          widgets.add(_buildBulletLine(context, line.substring(1).trim()));
        } else {
          widgets.add(
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                line,
                style: theme.textTheme.bodyMedium,
              ),
            ),
          );
        }
      }

      if (sectionIndex != sections.length - 1) {
        widgets.add(const SizedBox(height: 10));
      }
    }

    return widgets;
  }

  Widget _buildBulletLine(BuildContext context, String text) {
    final theme = Theme.of(context);

    return Padding(
      padding: const EdgeInsets.only(bottom: AppTheme.spacingS),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(
                color: color,
                shape: BoxShape.circle,
              ),
            ),
          ),
          const SizedBox(width: AppTheme.spacingM),
          Expanded(
            child: Text(
              text,
              style: theme.textTheme.bodyMedium,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isSelected = value == groupValue;

    return AppCard(
      onTap: () => onChanged(value),
      backgroundColor: isSelected
          ? color.withValues(
              alpha: theme.brightness == Brightness.dark ? 0.16 : 0.10)
          : theme.cardColor,
      borderRadius: AppTheme.radiusXL,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 220),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(AppTheme.spacingM),
              decoration: BoxDecoration(
                color: isSelected ? color : color.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(AppTheme.radiusLarge),
              ),
              child: Icon(
                icon,
                color: isSelected ? Colors.white : color,
                size: 24,
              ),
            ),
            const SizedBox(width: AppTheme.spacingL),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: theme.textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: isSelected
                          ? color
                          : theme.textTheme.titleLarge?.color,
                    ),
                  ),
                  const SizedBox(height: AppTheme.spacingXS),
                  Text(
                    subtitle,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: isSelected
                          ? theme.textTheme.bodyMedium?.color
                          : theme.textTheme.bodyMedium?.color,
                    ),
                  ),
                ],
              ),
            ),
            Container(
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHighest
                    .withValues(alpha: isSelected ? 0.55 : 0.35),
                borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              ),
              child: IconButton(
                icon: Icon(
                  Icons.help_outline_rounded,
                  color: isSelected ? color : theme.textTheme.bodySmall?.color,
                  size: 20,
                ),
                onPressed: () => _showInfo(context),
                tooltip: title,
              ),
            ),
            const SizedBox(width: AppTheme.spacingS),
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

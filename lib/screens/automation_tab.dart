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
    final expressive = context.expressive;
    final modes = [
      (
        title: l10n.modeAdaptive,
        subtitle: l10n.modeAdaptiveSubtitle,
        icon: Icons.auto_awesome_rounded,
        color: AppTheme.pvColor,
        value: 0,
        tooltip: l10n.modeAdaptiveDesc,
      ),
      (
        title: l10n.modeArbitrage,
        subtitle: l10n.modeArbitrageSubtitle,
        icon: Icons.nightlight_round,
        color: AppTheme.gridColor,
        value: 1,
        tooltip: l10n.modeArbitrageDesc,
      ),
      (
        title: l10n.modeStorm,
        subtitle: l10n.modeStormSubtitle,
        icon: Icons.thunderstorm_rounded,
        color: AppTheme.batteryColor,
        value: 2,
        tooltip: l10n.modeStormDesc,
      ),
    ];

    return ListView(
      padding: const EdgeInsets.all(AppTheme.spacingXL),
      children: [
        AppSectionTitle(
          title: l10n.hemsTitle,
          subtitle: l10n.hemsSubtitle,
          icon: Icons.tune_rounded,
        ),
        AppCard(
          borderRadius: expressive.cornerXL,
          child: Row(
            children: [
              Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: Theme.of(context)
                      .colorScheme
                      .primary
                      .withValues(alpha: 0.12),
                  border: Border.all(
                    color: Theme.of(context)
                        .colorScheme
                        .primary
                        .withValues(alpha: 0.25),
                  ),
                ),
                child: Icon(
                  Icons.auto_graph_rounded,
                  color: Theme.of(context).colorScheme.primary,
                ),
              ),
              const SizedBox(width: AppTheme.spacingL),
              Expanded(
                child: Text(
                  l10n.hemsSubtitle,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppTheme.spacingM),
        LayoutBuilder(
          builder: (context, constraints) {
            final width = constraints.maxWidth;
            final columns = width >= 1220
                ? 3
                : width >= 760
                    ? 2
                    : 1;
            final cardWidth =
                (width - (columns - 1) * AppTheme.spacingL) / columns;

            return Wrap(
              spacing: AppTheme.spacingL,
              runSpacing: AppTheme.spacingL,
              children: modes
                  .map(
                    (mode) => SizedBox(
                      width: cardWidth,
                      child: _SmartModeCard(
                        title: mode.title,
                        subtitle: mode.subtitle,
                        icon: mode.icon,
                        color: mode.color,
                        value: mode.value,
                        groupValue: provider.smartMode,
                        onChanged: (val) => provider.setSmartMode(val!),
                        tooltipText: mode.tooltip,
                      ),
                    ),
                  )
                  .toList(),
            );
          },
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
    final motion = context.motion;
    final expressive = context.expressive;
    final isSelected = value == groupValue;

    return AppCard(
      onTap: () => onChanged(value),
      backgroundColor: isSelected
          ? color.withValues(
              alpha: theme.brightness == Brightness.dark ? 0.16 : 0.10)
          : theme.cardColor,
      borderRadius: expressive.cornerXL,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(AppTheme.spacingM),
            decoration: BoxDecoration(
              color: isSelected ? color : color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(expressive.cornerLarge),
              border: Border.all(
                color: isSelected
                    ? color.withValues(alpha: 0.5)
                    : theme.colorScheme.outlineVariant.withValues(alpha: 0.45),
              ),
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
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        title,
                        style: theme.textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: isSelected
                              ? color
                              : theme.textTheme.titleLarge?.color,
                        ),
                      ),
                    ),
                    AnimatedContainer(
                      duration: motion.quick,
                      curve: motion.standardCurve,
                      padding: const EdgeInsets.all(6),
                      decoration: BoxDecoration(
                        color: isSelected
                            ? color.withValues(alpha: 0.16)
                            : theme.colorScheme.surfaceContainerHighest
                                .withValues(alpha: 0.35),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Icon(
                        isSelected
                            ? Icons.bolt_rounded
                            : Icons.schedule_rounded,
                        size: 14,
                        color: isSelected
                            ? color
                            : theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  subtitle,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: theme.textTheme.bodyMedium?.color,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
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
          AnimatedContainer(
            duration: motion.quick,
            curve: motion.standardCurve,
            width: 26,
            height: 26,
            decoration: BoxDecoration(
              color: isSelected
                  ? color.withValues(alpha: 0.16)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(999),
              border: Border.all(
                color: isSelected
                    ? color.withValues(alpha: 0.75)
                    : theme.dividerColor.withValues(alpha: 0.7),
              ),
            ),
            child: Icon(
              isSelected ? Icons.check_rounded : Icons.circle_outlined,
              size: 16,
              color: isSelected
                  ? color
                  : theme.colorScheme.onSurfaceVariant.withValues(alpha: 0.8),
            ),
          ),
        ],
      ),
    );
  }
}

// ignore_for_file: deprecated_member_use

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:open_file/open_file.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../services/event_history_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';
import 'schedule_rules_section.dart';

class AutomationTab extends StatelessWidget {
  final AppStateProvider provider;

  const AutomationTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
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
    final selectedMode = modes.firstWhere(
      (mode) => mode.value == provider.smartMode,
      orElse: () => modes.first,
    );

    final isCompact = MediaQuery.sizeOf(context).width < 600;
    return ListView(
      padding: EdgeInsets.fromLTRB(
        isCompact ? AppTheme.spacingM : AppTheme.spacingXL,
        isCompact ? AppTheme.spacingS : AppTheme.spacingXL,
        isCompact ? AppTheme.spacingM : AppTheme.spacingXL,
        AppTheme.spacingXL,
      ),
      children: [
        AppSectionTitle(
          title: l10n.hemsTitle,
          subtitle: l10n.hemsSubtitle,
          icon: Icons.tune_rounded,
        ),
        AppSectionCard(
          title: l10n.energyOverview,
          subtitle: l10n.hemsSubtitle,
          icon: Icons.auto_graph_rounded,
          borderRadius: context.expressive.cornerXL,
          child: _AutomationOverviewCard(
            provider: provider,
            modeTitle: selectedMode.title,
            modeSubtitle: selectedMode.subtitle,
            modeColor: selectedMode.color,
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
        const SizedBox(height: AppTheme.spacingXL),
        const ScheduleRulesSection(),
        const SizedBox(height: AppTheme.spacingXL),
        _EventHistorySection(provider: provider),
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

class _AutomationOverviewCard extends StatelessWidget {
  final AppStateProvider provider;
  final String modeTitle;
  final String modeSubtitle;
  final Color modeColor;

  const _AutomationOverviewCard({
    required this.provider,
    required this.modeTitle,
    required this.modeSubtitle,
    required this.modeColor,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final diagnostics = provider.hemsService.buildDiagnosticsSnapshot();
    final windowsLabel =
        '${diagnostics.dayStartHour.toString().padLeft(2, '0')}:00 / '
        '${diagnostics.eveningStartHour.toString().padLeft(2, '0')}:00 / '
        '${diagnostics.nightStartHour.toString().padLeft(2, '0')}:00';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 46,
              height: 46,
              decoration: BoxDecoration(
                color: modeColor.withValues(alpha: 0.12),
                borderRadius:
                    BorderRadius.circular(context.expressive.cornerMedium),
              ),
              child: Icon(Icons.bolt_rounded, color: modeColor),
            ),
            const SizedBox(width: AppTheme.spacingM),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    modeTitle,
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: AppTheme.spacingXS),
                  Text(
                    modeSubtitle,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: AppTheme.spacingM),
        Wrap(
          spacing: AppTheme.spacingS,
          runSpacing: AppTheme.spacingS,
          children: [
            AppStatusChip(
              icon: provider.isInverterOffline
                  ? Icons.cloud_off_rounded
                  : Icons.cloud_done_rounded,
              label: provider.isInverterOffline
                  ? l10n.connectionOffline
                  : l10n.connectionOnline,
              color: provider.isInverterOffline
                  ? theme.colorScheme.error
                  : AppTheme.batteryColor,
            ),
            AppStatusChip(
              icon: Icons.power_outlined,
              label: provider.plannedOutageEnabled
                  ? l10n.plannedOutageEnabledSubtitle
                  : l10n.plannedOutageDisabledSubtitle,
              color: provider.plannedOutageEnabled
                  ? AppTheme.pvColor
                  : theme.colorScheme.onSurfaceVariant,
            ),
            AppStatusChip(
              icon: Icons.schedule_rounded,
              label: windowsLabel,
              color: theme.colorScheme.primary,
            ),
          ],
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Event History Section
// ---------------------------------------------------------------------------

class _EventHistorySection extends StatefulWidget {
  final AppStateProvider provider;
  const _EventHistorySection({required this.provider});

  @override
  State<_EventHistorySection> createState() => _EventHistorySectionState();
}

class _EventHistorySectionState extends State<_EventHistorySection> {
  static const _previewCount = 8;
  bool _showAll = false;

  Future<void> _exportCsv(BuildContext context, AppLocalizations l10n) async {
    final path = await widget.provider.eventHistory.exportToCsvFile();
    if (!context.mounted) return;
    if (path != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(l10n.exportedTo(path)),
          action: Platform.isWindows
              ? SnackBarAction(
                  label: 'Open',
                  onPressed: () => OpenFile.open(
                      path.substring(0, path.lastIndexOf('\\')).isEmpty
                          ? path
                          : path.substring(0, path.lastIndexOf('\\'))),
                )
              : null,
          duration: const Duration(seconds: 6),
        ),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.exportFailed)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final history = widget.provider.eventHistory;

    return ListenableBuilder(
      listenable: history,
      builder: (context, _) {
        final events = history.events;
        final displayEvents =
            _showAll ? events : events.take(_previewCount).toList();

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSectionTitle(
              title: l10n.eventHistoryTitle,
              subtitle: events.isEmpty
                  ? l10n.eventHistoryEmpty
                  : '${events.length} events',
              icon: Icons.history_rounded,
              trailing: events.isEmpty
                  ? null
                  : Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Tooltip(
                          message: l10n.exportCsv,
                          child: IconButton(
                            icon: const Icon(Icons.download_rounded, size: 18),
                            onPressed: () => _exportCsv(context, l10n),
                          ),
                        ),
                        Tooltip(
                          message: l10n.eventHistoryClear,
                          child: IconButton(
                            icon: const Icon(Icons.delete_outline_rounded,
                                size: 18),
                            onPressed: () {
                              showDialog(
                                context: context,
                                builder: (ctx) => AlertDialog(
                                  title: Text(l10n.eventHistoryClear),
                                  actions: [
                                    TextButton(
                                      onPressed: () => Navigator.pop(ctx),
                                      child: Text(l10n.cancel),
                                    ),
                                    TextButton(
                                      onPressed: () {
                                        history.clearAll();
                                        Navigator.pop(ctx);
                                      },
                                      child: Text(l10n.confirm,
                                          style: TextStyle(
                                              color: theme.colorScheme.error)),
                                    ),
                                  ],
                                ),
                              );
                            },
                          ),
                        ),
                      ],
                    ),
            ),
            if (events.isEmpty)
              AppCard(
                borderRadius: expressive.cornerXL,
                child: Padding(
                  padding: const EdgeInsets.all(AppTheme.spacingXL),
                  child: Center(
                    child: Text(
                      l10n.eventHistoryEmpty,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                ),
              )
            else
              AppCard(
                borderRadius: expressive.cornerXL,
                padding: EdgeInsets.zero,
                child: Column(
                  children: [
                    ...displayEvents.asMap().entries.map((entry) {
                      final i = entry.key;
                      final e = entry.value;
                      final isLast = i == displayEvents.length - 1 &&
                          (events.length <= _previewCount || _showAll);
                      return _EventTile(event: e, isLast: isLast);
                    }),
                    if (!_showAll && events.length > _previewCount)
                      InkWell(
                        borderRadius: BorderRadius.vertical(
                          bottom: Radius.circular(expressive.cornerXL),
                        ),
                        onTap: () => setState(() => _showAll = true),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(
                              vertical: AppTheme.spacingM),
                          child: Center(
                            child: Text(
                              '${l10n.eventHistoryShowAll} (${events.length})',
                              style: theme.textTheme.labelMedium?.copyWith(
                                color: theme.colorScheme.primary,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ),
                      ),
                    if (_showAll && events.length > _previewCount)
                      InkWell(
                        borderRadius: BorderRadius.vertical(
                          bottom: Radius.circular(expressive.cornerXL),
                        ),
                        onTap: () => setState(() => _showAll = false),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(
                              vertical: AppTheme.spacingM),
                          child: Center(
                            child: Text(
                              '▲ Collapse',
                              style: theme.textTheme.labelMedium?.copyWith(
                                color: theme.colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}

class _EventTile extends StatelessWidget {
  final HemsEvent event;
  final bool isLast;

  const _EventTile({required this.event, required this.isLast});

  Color _typeColor(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    switch (event.type) {
      case HemsEventType.gridOutage:
      case HemsEventType.lowBattery:
      case HemsEventType.emergencyCharge:
      case HemsEventType.anomaly:
        return scheme.error;
      case HemsEventType.gridRestored:
      case HemsEventType.batteryRecovered:
      case HemsEventType.stormAutoDeactivated:
        return AppTheme.batteryColor;
      case HemsEventType.stormAutoActivated:
      case HemsEventType.gridInstability:
        return AppTheme.pvColor;
      default:
        return scheme.onSurfaceVariant;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = _typeColor(context);
    final t = event.time.toLocal();
    final timeStr =
        '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')} '
        '${t.day.toString().padLeft(2, '0')}.${t.month.toString().padLeft(2, '0')}';

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppTheme.spacingL,
            vertical: AppTheme.spacingM,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 32,
                height: 32,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  shape: BoxShape.circle,
                  border: Border.all(color: color.withValues(alpha: 0.3)),
                ),
                child: Center(
                  child: Text(
                    event.type.icon,
                    style: const TextStyle(fontSize: 14),
                  ),
                ),
              ),
              const SizedBox(width: AppTheme.spacingM),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      event.message,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: event.type.isCritical
                            ? color
                            : theme.colorScheme.onSurface,
                        fontWeight: event.type.isCritical
                            ? FontWeight.w600
                            : FontWeight.normal,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      timeStr,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        if (!isLast)
          Divider(
            height: 1,
            indent: AppTheme.spacingL + 32 + AppTheme.spacingM,
            endIndent: AppTheme.spacingL,
            color: theme.dividerColor.withValues(alpha: 0.5),
          ),
      ],
    );
  }
}

// ignore_for_file: deprecated_member_use

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../models/schedule_rule.dart';
import '../services/schedule_rules_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';

/// Section widget embedded in AutomationTab that shows and manages schedule rules.
class ScheduleRulesSection extends StatelessWidget {
  const ScheduleRulesSection({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final expressive = context.expressive;

    return ListenableBuilder(
      listenable: ScheduleRulesService.instance,
      builder: (context, _) {
        final rules = ScheduleRulesService.instance.rules;
        final now = DateTime.now();
        final activeRules = rules.where((r) => r.isActiveAt(now)).toList();
        final conflictCount = activeRules.length;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSectionTitle(
              title: l10n.scheduleRulesTitle,
              subtitle: conflictCount > 1
                  ? l10n.scheduleRuleConflict(conflictCount)
                  : l10n.scheduleRulesSubtitle,
              icon: Icons.event_repeat_rounded,
              trailing: Tooltip(
                message: l10n.scheduleRuleAdd,
                child: IconButton(
                  icon: const Icon(Icons.add_rounded, size: 20),
                  onPressed: () => _showEditDialog(context, null),
                ),
              ),
            ),
            if (rules.isEmpty)
              AppCard(
                borderRadius: expressive.cornerXL,
                child: Padding(
                  padding: const EdgeInsets.all(AppTheme.spacingXL),
                  child: Center(
                    child: Text(
                      l10n.scheduleRulesEmpty,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color:
                                Theme.of(context).colorScheme.onSurfaceVariant,
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
                  children: rules.asMap().entries.map((entry) {
                    final i = entry.key;
                    final rule = entry.value;
                    return _RuleTile(
                      rule: rule,
                      isLast: i == rules.length - 1,
                      onEdit: () => _showEditDialog(context, rule),
                      onDelete: () => _confirmDelete(context, rule, l10n),
                      onToggle: () =>
                          ScheduleRulesService.instance.toggleRule(rule.id),
                    );
                  }).toList(),
                ),
              ),
          ],
        );
      },
    );
  }

  Future<void> _showEditDialog(
      BuildContext context, ScheduleRule? existing) async {
    await showDialog<void>(
      context: context,
      builder: (ctx) => _RuleEditDialog(existing: existing),
    );
  }

  Future<void> _confirmDelete(
    BuildContext context,
    ScheduleRule rule,
    AppLocalizations l10n,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.scheduleRuleDelete),
        content: Text(l10n.scheduleRuleDeleteConfirm(rule.name)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(l10n.cancel),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.confirm,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await ScheduleRulesService.instance.deleteRule(rule.id);
    }
  }
}

// ---------------------------------------------------------------------------
// Rule tile
// ---------------------------------------------------------------------------

class _RuleTile extends StatelessWidget {
  final ScheduleRule rule;
  final bool isLast;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final VoidCallback onToggle;

  const _RuleTile({
    required this.rule,
    required this.isLast,
    required this.onEdit,
    required this.onDelete,
    required this.onToggle,
  });

  Color _modeColor(BuildContext context) {
    switch (rule.mode) {
      case ScheduleRuleMode.adaptive:
        return AppTheme.pvColor;
      case ScheduleRuleMode.arbitrage:
        return AppTheme.gridColor;
      case ScheduleRuleMode.storm:
        return AppTheme.batteryColor;
    }
  }

  IconData _modeIcon() {
    switch (rule.mode) {
      case ScheduleRuleMode.adaptive:
        return Icons.auto_awesome_rounded;
      case ScheduleRuleMode.arbitrage:
        return Icons.nightlight_round;
      case ScheduleRuleMode.storm:
        return Icons.thunderstorm_rounded;
    }
  }

  String _modeLabel(AppLocalizations l10n) {
    switch (rule.mode) {
      case ScheduleRuleMode.adaptive:
        return l10n.scheduleRuleModeAdaptive;
      case ScheduleRuleMode.arbitrage:
        return l10n.scheduleRuleModeArbitrage;
      case ScheduleRuleMode.storm:
        return l10n.scheduleRuleModeStorm;
    }
  }

  String _dayLabel(AppLocalizations l10n, int day) {
    switch (day) {
      case 1:
        return l10n.mon;
      case 2:
        return l10n.tue;
      case 3:
        return l10n.wed;
      case 4:
        return l10n.thu;
      case 5:
        return l10n.fri;
      case 6:
        return l10n.sat;
      case 7:
        return l10n.sun;
      default:
        return '';
    }
  }

  String _daysLabel(AppLocalizations l10n) {
    final sorted = List<int>.from(rule.daysOfWeek)..sort();
    return sorted
        .map((d) => _dayLabel(l10n, d))
        .where((v) => v.isNotEmpty)
        .join(', ');
  }

  /// Small circular badge showing the numeric priority of the rule.
  Widget _priorityBadge(BuildContext context) {
    final theme = Theme.of(context);
    final color = _modeColor(context);
    final l10n = AppLocalizations.of(context)!;
    return Tooltip(
      message: '${l10n.scheduleRulePriority}: ${rule.priority}',
      child: Container(
        width: 22,
        height: 22,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: rule.enabled
              ? color.withValues(alpha: 0.16)
              : theme.colorScheme.surfaceContainerHighest
                  .withValues(alpha: 0.35),
          border: Border.all(
            color: rule.enabled
                ? color.withValues(alpha: 0.4)
                : theme.colorScheme.outlineVariant.withValues(alpha: 0.3),
          ),
        ),
        child: Center(
          child: Text(
            '${rule.priority}',
            style: theme.textTheme.labelSmall?.copyWith(
              fontSize: 10,
              fontWeight: FontWeight.w700,
              color: rule.enabled
                  ? color
                  : theme.colorScheme.onSurfaceVariant.withValues(alpha: 0.5),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final color = _modeColor(context);
    final isActive = rule.isActiveNow;

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(
              horizontal: AppTheme.spacingM, vertical: AppTheme.spacingS),
          child: Row(
            children: [
              // Mode icon badge
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  color: rule.enabled
                      ? color.withValues(alpha: 0.14)
                      : theme.colorScheme.surfaceContainerHighest
                          .withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
                  border: Border.all(
                    color: rule.enabled
                        ? color.withValues(alpha: 0.35)
                        : theme.colorScheme.outlineVariant
                            .withValues(alpha: 0.3),
                  ),
                ),
                child: Icon(
                  _modeIcon(),
                  color: rule.enabled
                      ? color
                      : theme.colorScheme.onSurfaceVariant
                          .withValues(alpha: 0.5),
                  size: 18,
                ),
              ),
              const SizedBox(width: AppTheme.spacingM),
              // Priority badge
              _priorityBadge(context),
              const SizedBox(width: AppTheme.spacingS),
              // Name + info
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            rule.name.isEmpty ? _modeLabel(l10n) : rule.name,
                            style: theme.textTheme.bodyMedium?.copyWith(
                              fontWeight: FontWeight.w600,
                              color: rule.enabled
                                  ? theme.colorScheme.onSurface
                                  : theme.colorScheme.onSurfaceVariant
                                      .withValues(alpha: 0.6),
                            ),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (isActive && rule.enabled)
                          Container(
                            margin: const EdgeInsets.only(left: 6),
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: color.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(99),
                              border: Border.all(
                                  color: color.withValues(alpha: 0.5)),
                            ),
                            child: Text(
                              l10n.scheduleRuleActive,
                              style: theme.textTheme.labelSmall?.copyWith(
                                color: color,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '${rule.timeRangeLabel}  •  ${_daysLabel(l10n)}',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              // Toggle
              Switch(
                value: rule.enabled,
                onChanged: (_) => onToggle(),
                activeColor: color,
              ),
              // Edit
              IconButton(
                icon: const Icon(Icons.edit_outlined, size: 18),
                onPressed: onEdit,
                tooltip: l10n.scheduleRuleEdit,
                color: theme.colorScheme.onSurfaceVariant,
              ),
              // Delete
              IconButton(
                icon: const Icon(Icons.delete_outline_rounded, size: 18),
                onPressed: onDelete,
                tooltip: l10n.scheduleRuleDelete,
                color: theme.colorScheme.error.withValues(alpha: 0.7),
              ),
            ],
          ),
        ),
        if (!isLast)
          Divider(
            height: 1,
            indent: AppTheme.spacingM + 38 + AppTheme.spacingM,
            endIndent: AppTheme.spacingM,
            color: Theme.of(context).dividerColor.withValues(alpha: 0.45),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Rule edit dialog
// ---------------------------------------------------------------------------

class _RuleEditDialog extends StatefulWidget {
  final ScheduleRule? existing;
  const _RuleEditDialog({this.existing});

  @override
  State<_RuleEditDialog> createState() => _RuleEditDialogState();
}

class _RuleEditDialogState extends State<_RuleEditDialog> {
  late final TextEditingController _nameCtrl;
  late List<int> _days;
  late TimeOfDay _start;
  late TimeOfDay _end;
  late ScheduleRuleMode _mode;
  late int _priority;

  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final r = widget.existing;
    _nameCtrl = TextEditingController(text: r?.name ?? '');
    _days = r != null ? List<int>.from(r.daysOfWeek) : [1, 2, 3, 4, 5];
    _start = r != null
        ? TimeOfDay(hour: r.startHour, minute: r.startMinute)
        : const TimeOfDay(hour: 7, minute: 0);
    _end = r != null
        ? TimeOfDay(hour: r.endHour, minute: r.endMinute)
        : const TimeOfDay(hour: 23, minute: 0);
    _mode = r?.mode ?? ScheduleRuleMode.adaptive;
    _priority = r?.priority ?? ScheduleRule.defaultPriority;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  String _fmt(TimeOfDay t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

  Future<void> _pickTime(bool isStart) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: isStart ? _start : _end,
      builder: (ctx, child) => MediaQuery(
        data: MediaQuery.of(ctx).copyWith(alwaysUse24HourFormat: true),
        child: child!,
      ),
    );
    if (picked == null) return;
    setState(() {
      if (isStart) {
        _start = picked;
      } else {
        _end = picked;
      }
    });
  }

  Color _modeColor(ScheduleRuleMode m) {
    switch (m) {
      case ScheduleRuleMode.adaptive:
        return AppTheme.pvColor;
      case ScheduleRuleMode.arbitrage:
        return AppTheme.gridColor;
      case ScheduleRuleMode.storm:
        return AppTheme.batteryColor;
    }
  }

  IconData _modeIcon(ScheduleRuleMode m) {
    switch (m) {
      case ScheduleRuleMode.adaptive:
        return Icons.auto_awesome_rounded;
      case ScheduleRuleMode.arbitrage:
        return Icons.nightlight_round;
      case ScheduleRuleMode.storm:
        return Icons.thunderstorm_rounded;
    }
  }

  String _modeLabel(AppLocalizations l10n, ScheduleRuleMode m) {
    switch (m) {
      case ScheduleRuleMode.adaptive:
        return l10n.scheduleRuleModeAdaptive;
      case ScheduleRuleMode.arbitrage:
        return l10n.scheduleRuleModeArbitrage;
      case ScheduleRuleMode.storm:
        return l10n.scheduleRuleModeStorm;
    }
  }

  String _dayLabel(AppLocalizations l10n, int day) {
    switch (day) {
      case 1:
        return l10n.mon;
      case 2:
        return l10n.tue;
      case 3:
        return l10n.wed;
      case 4:
        return l10n.thu;
      case 5:
        return l10n.fri;
      case 6:
        return l10n.sat;
      case 7:
        return l10n.sun;
      default:
        return '';
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    if (_nameCtrl.text.trim().isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.scheduleRuleNameEmpty)));
      return;
    }
    if (_days.isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.scheduleRuleNoDays)));
      return;
    }
    setState(() => _saving = true);
    final id =
        widget.existing?.id ?? DateTime.now().millisecondsSinceEpoch.toString();
    final rule = ScheduleRule(
      id: id,
      name: _nameCtrl.text.trim(),
      daysOfWeek: List<int>.from(_days)..sort(),
      startHour: _start.hour,
      startMinute: _start.minute,
      endHour: _end.hour,
      endMinute: _end.minute,
      mode: _mode,
      enabled: widget.existing?.enabled ?? true,
      priority: _priority,
    );
    if (widget.existing == null) {
      await ScheduleRulesService.instance.addRule(rule);
    } else {
      await ScheduleRulesService.instance.updateRule(rule);
    }
    if (mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final isEdit = widget.existing != null;

    return AlertDialog(
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTheme.radiusXL)),
      title: Text(isEdit ? l10n.scheduleRuleEdit : l10n.scheduleRuleAdd),
      contentPadding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
      content: SingleChildScrollView(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Name
              TextField(
                controller: _nameCtrl,
                decoration: InputDecoration(
                  labelText: l10n.scheduleRuleName,
                  hintText: l10n.scheduleRuleNameHint,
                  border: const OutlineInputBorder(),
                  isDense: true,
                ),
                textCapitalization: TextCapitalization.sentences,
              ),
              const SizedBox(height: AppTheme.spacingL),

              // Days
              Text(l10n.scheduleRuleDays,
                  style: theme.textTheme.labelMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: theme.colorScheme.onSurfaceVariant,
                  )),
              const SizedBox(height: AppTheme.spacingS),
              Wrap(
                spacing: 6,
                children: List.generate(7, (i) {
                  final day = i + 1; // 1=Mon … 7=Sun
                  final selected = _days.contains(day);
                  return FilterChip(
                    label: Text(_dayLabel(l10n, day)),
                    selected: selected,
                    onSelected: (v) {
                      setState(() {
                        if (v) {
                          _days.add(day);
                        } else {
                          _days.remove(day);
                        }
                      });
                    },
                    selectedColor: theme.colorScheme.primaryContainer,
                    checkmarkColor: theme.colorScheme.primary,
                    labelStyle: theme.textTheme.labelMedium?.copyWith(
                      color: selected
                          ? theme.colorScheme.primary
                          : theme.colorScheme.onSurfaceVariant,
                      fontWeight:
                          selected ? FontWeight.w700 : FontWeight.normal,
                    ),
                  );
                }),
              ),
              const SizedBox(height: AppTheme.spacingL),

              // Time row
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(l10n.scheduleRuleStartTime,
                            style: theme.textTheme.labelMedium?.copyWith(
                              fontWeight: FontWeight.w600,
                              color: theme.colorScheme.onSurfaceVariant,
                            )),
                        const SizedBox(height: AppTheme.spacingXS),
                        OutlinedButton.icon(
                          onPressed: () => _pickTime(true),
                          icon: const Icon(Icons.access_time_rounded, size: 16),
                          label: Text(_fmt(_start),
                              style: const TextStyle(fontFeatures: [
                                FontFeature.tabularFigures()
                              ])),
                          style: OutlinedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 12, vertical: 10),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: AppTheme.spacingM),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(l10n.scheduleRuleEndTime,
                            style: theme.textTheme.labelMedium?.copyWith(
                              fontWeight: FontWeight.w600,
                              color: theme.colorScheme.onSurfaceVariant,
                            )),
                        const SizedBox(height: AppTheme.spacingXS),
                        OutlinedButton.icon(
                          onPressed: () => _pickTime(false),
                          icon: const Icon(Icons.access_time_rounded, size: 16),
                          label: Text(_fmt(_end),
                              style: const TextStyle(fontFeatures: [
                                FontFeature.tabularFigures()
                              ])),
                          style: OutlinedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 12, vertical: 10),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppTheme.spacingL),

              // Mode
              Text(l10n.scheduleRuleMode,
                  style: theme.textTheme.labelMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: theme.colorScheme.onSurfaceVariant,
                  )),
              const SizedBox(height: AppTheme.spacingS),
              ...ScheduleRuleMode.values.map((m) {
                final mColor = _modeColor(m);
                final isSelected = _mode == m;
                return RadioListTile<ScheduleRuleMode>(
                  value: m,
                  groupValue: _mode,
                  onChanged: (v) => setState(() => _mode = v!),
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  activeColor: mColor,
                  title: Row(
                    children: [
                      Icon(_modeIcon(m),
                          color: isSelected
                              ? mColor
                              : theme.colorScheme.onSurfaceVariant,
                          size: 16),
                      const SizedBox(width: 8),
                      Text(
                        _modeLabel(l10n, m),
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color:
                              isSelected ? mColor : theme.colorScheme.onSurface,
                          fontWeight:
                              isSelected ? FontWeight.w600 : FontWeight.normal,
                        ),
                      ),
                    ],
                  ),
                );
              }),
              const SizedBox(height: AppTheme.spacingL),

              // Priority stepper
              Text(l10n.scheduleRulePriority,
                  style: theme.textTheme.labelMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: theme.colorScheme.onSurfaceVariant,
                  )),
              const SizedBox(height: AppTheme.spacingXS),
              Text(l10n.scheduleRulePriorityHint,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  )),
              const SizedBox(height: AppTheme.spacingS),
              Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.remove_circle_outline_rounded,
                        size: 22),
                    onPressed: _priority > ScheduleRule.minPriority
                        ? () => setState(() => _priority = (_priority - 1)
                            .clamp(ScheduleRule.minPriority,
                                ScheduleRule.maxPriority))
                        : null,
                    color: theme.colorScheme.primary,
                  ),
                  ...List.generate(ScheduleRule.maxPriority, (i) {
                    final val = i + 1;
                    final active = val == _priority;
                    return GestureDetector(
                      onTap: () => setState(() => _priority = val),
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 150),
                        margin: const EdgeInsets.symmetric(horizontal: 2),
                        width: active ? 18 : 12,
                        height: active ? 18 : 12,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: active
                              ? theme.colorScheme.primary
                              : theme.colorScheme.outlineVariant,
                        ),
                      ),
                    );
                  }),
                  IconButton(
                    icon:
                        const Icon(Icons.add_circle_outline_rounded, size: 22),
                    onPressed: _priority < ScheduleRule.maxPriority
                        ? () => setState(() => _priority = (_priority + 1)
                            .clamp(ScheduleRule.minPriority,
                                ScheduleRule.maxPriority))
                        : null,
                    color: theme.colorScheme.primary,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '$_priority / ${ScheduleRule.maxPriority}',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: theme.colorScheme.primary,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppTheme.spacingM),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l10n.cancel),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: _saving
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : Text(l10n.save),
        ),
      ],
    );
  }
}

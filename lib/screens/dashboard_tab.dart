import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/foundation.dart'
    show kIsWeb, defaultTargetPlatform, TargetPlatform;
import 'package:fl_chart/fl_chart.dart';
import 'package:window_manager/window_manager.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../services/log_service.dart';
import '../services/soc_history_service.dart';
import '../services/weather_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';
import '../widgets/energy_flow.dart';
import '../utils/formatters.dart';
import '../utils/dashboard_diagnostics_export.dart';

class DashboardTab extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const DashboardTab({super.key, required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final statusMessage = provider.statusMessage.trim();
    final lastUpdatedAt = provider.lastSuccessfulRealtimeAt;
    final lastUpdatedShortLabel = lastUpdatedAt == null
        ? null
        : '${lastUpdatedAt.hour.toString().padLeft(2, '0')}:${lastUpdatedAt.minute.toString().padLeft(2, '0')}';
    final diagnosticsSnapshotText =
        buildDashboardDiagnosticsSnapshot(provider, data);
    final sections = <Widget>[
      RepaintBoundary(
          child: _EnergyFlowSection(provider: provider, data: data)),
      if (statusMessage.isNotEmpty)
        AppStatusBanner(
          message: statusMessage,
          icon: Icons.info_outline,
          meta: lastUpdatedShortLabel == null
              ? null
              : '${l10n.lastRealtimeUpdate}: $lastUpdatedShortLabel',
        ),
      _DashboardDiagnosticsCard(
        snapshotText: diagnosticsSnapshotText,
      ),
      RepaintBoundary(
          child: _QuickPulseSection(provider: provider, data: data)),
      RepaintBoundary(child: _StatsSection(provider: provider, data: data)),
      LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 1180;
          if (!wide) {
            return Column(
              children: [
                RepaintBoundary(
                  child: _EnergyChartSection(provider: provider),
                ),
                const SizedBox(height: AppTheme.spacingL),
                RepaintBoundary(
                  child: _SystemCapacitySection(
                    provider: provider,
                    data: data,
                  ),
                ),
              ],
            );
          }

          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                flex: 6,
                child: RepaintBoundary(
                  child: _EnergyChartSection(provider: provider),
                ),
              ),
              const SizedBox(width: AppTheme.spacingL),
              Expanded(
                flex: 4,
                child: RepaintBoundary(
                  child: _SystemCapacitySection(
                    provider: provider,
                    data: data,
                  ),
                ),
              ),
            ],
          );
        },
      ),
      RepaintBoundary(child: _MonthEconomicsBreakdown(provider: provider)),
      RepaintBoundary(child: _BatterySocHistoryCard(provider: provider)),
    ];

    final isCompact = MediaQuery.sizeOf(context).width < 600;
    return CallbackShortcuts(
      bindings: {
        const SingleActivator(LogicalKeyboardKey.keyR, control: true): () {
          if (!provider.isDataLoading) {
            unawaited(provider.fetchData());
          }
        },
      },
      child: Focus(
        autofocus: true,
        child: RefreshIndicator(
          color: Theme.of(context).colorScheme.primary,
          onRefresh: provider.fetchData,
          child: ListView.separated(
            physics: const AlwaysScrollableScrollPhysics(
              parent: ClampingScrollPhysics(),
            ),
            padding: EdgeInsets.fromLTRB(
              isCompact ? AppTheme.spacingM : AppTheme.spacingL,
              isCompact ? AppTheme.spacingS : AppTheme.spacingL,
              isCompact ? AppTheme.spacingM : AppTheme.spacingL,
              AppTheme.spacingL,
            ),
            itemBuilder: (context, index) => sections[index],
            separatorBuilder: (_, __) =>
                const SizedBox(height: AppTheme.spacingL),
            itemCount: sections.length,
          ),
        ),
      ),
    );
  }
}

class _DashboardDiagnosticsCard extends StatelessWidget {
  final String snapshotText;

  const _DashboardDiagnosticsCard({required this.snapshotText});

  Future<void> _copySnapshot(BuildContext context) async {
    final l10n = AppLocalizations.of(context)!;
    await Clipboard.setData(ClipboardData(text: snapshotText));
    final savedPath =
        await appendDashboardDiagnosticsSnapshotToFile(snapshotText);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(savedPath == null
            ? l10n.diagnosticsSnapshotCopied
            : '${l10n.diagnosticsSnapshotCopied}\n$savedPath'),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;

    return AppCard(
      borderRadius: expressive.cornerXL,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color:
                    theme.colorScheme.primaryContainer.withValues(alpha: 0.8),
                borderRadius: BorderRadius.circular(expressive.cornerMedium),
              ),
              child: Icon(
                Icons.content_copy_rounded,
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(width: AppTheme.spacingM),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    l10n.diagnosticsSnapshot,
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    l10n.diagnosticsSnapshotHint,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: AppTheme.spacingM),
            FilledButton.tonalIcon(
              onPressed: () => _copySnapshot(context),
              icon: const Icon(Icons.copy_rounded, size: 18),
              label: Text(l10n.copyDiagnosticsSnapshot),
            ),
          ],
        ),
      ),
    );
  }
}

class _EnergyFlowSection extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const _EnergyFlowSection({required this.provider, required this.data});

  Future<void> _openEnergyFlowFullscreen(BuildContext context) async {
    await showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (dialogContext) {
        return _EnergyFlowFullscreenView(data: data);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final isGridOutage = provider.isGridOutageDetected;
    final lastUpdatedAt = provider.lastSuccessfulRealtimeAt;
    final backupHours = provider.estimateBackupHoursRemaining();
    final batteryOnlyHours = provider.estimateBatteryOnlyHoursRemaining();
    final lastUpdatedLabel = lastUpdatedAt == null
        ? l10n.lastRealtimeUpdate
        : '${l10n.lastRealtimeUpdate}: ${lastUpdatedAt.hour.toString().padLeft(2, '0')}:${lastUpdatedAt.minute.toString().padLeft(2, '0')}';
    return AppCard(
      borderRadius: expressive.cornerLarge,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.account_tree_rounded,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Text(
                  l10n.equipmentStatus,
                  style: theme.textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(width: AppTheme.spacingS),
              IconButton.filledTonal(
                onPressed: provider.isDataLoading ? null : provider.fetchData,
                tooltip: l10n.refreshChart,
                icon: AnimatedSwitcher(
                  duration: context.motion.quick,
                  child: provider.isDataLoading
                      ? SizedBox(
                          key: const ValueKey('refresh-loading'),
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: theme.colorScheme.primary,
                          ),
                        )
                      : const Icon(
                          Icons.refresh_rounded,
                          key: ValueKey('refresh-icon'),
                          size: 18,
                        ),
                ),
              ),
              IconButton.filledTonal(
                onPressed: () => _openEnergyFlowFullscreen(context),
                icon: const Icon(Icons.open_in_full_rounded, size: 18),
              ),
            ],
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            lastUpdatedLabel,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          if (isGridOutage) ...[
            const SizedBox(height: AppTheme.spacingM),
            _GridOutageRuntimeBanner(
              voltage: data.gridVoltage,
              backupHours: backupHours,
              batteryOnlyHours: batteryOnlyHours,
              isSolarCoveringLoad: provider.isLoadCoveredBySolarNow,
            ),
          ],
          const SizedBox(height: AppTheme.spacingM),
          RepaintBoundary(
            child: ExcludeSemantics(child: EnergyFlowDiagram(data: data)),
          ),
        ],
      ),
    );
  }
}

class _EnergyFlowFullscreenView extends StatefulWidget {
  final InverterData data;

  const _EnergyFlowFullscreenView({required this.data});

  @override
  State<_EnergyFlowFullscreenView> createState() =>
      _EnergyFlowFullscreenViewState();
}

class _EnergyFlowFullscreenViewState extends State<_EnergyFlowFullscreenView> {
  bool _enteredNativeFullscreen = false;
  bool _wasNativeFullscreen = false;

  bool get _supportsNativeFullscreen {
    if (kIsWeb) return false;
    return defaultTargetPlatform == TargetPlatform.windows ||
        defaultTargetPlatform == TargetPlatform.linux ||
        defaultTargetPlatform == TargetPlatform.macOS;
  }

  @override
  void initState() {
    super.initState();
    // Enter native OS fullscreen so the view fills the monitor, not only app layout.
    unawaited(_enterNativeFullscreen());
  }

  Future<void> _enterNativeFullscreen() async {
    if (!_supportsNativeFullscreen) return;
    final isFull = await windowManager.isFullScreen();
    _wasNativeFullscreen = isFull;
    if (!isFull) {
      await windowManager.setFullScreen(true);
      _enteredNativeFullscreen = true;
    }
  }

  Future<void> _restoreNativeFullscreen() async {
    if (!_supportsNativeFullscreen) return;
    if (_enteredNativeFullscreen && !_wasNativeFullscreen) {
      await windowManager.setFullScreen(false);
    }
  }

  Future<void> _closeView() async {
    await _restoreNativeFullscreen();
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  @override
  void dispose() {
    unawaited(_restoreNativeFullscreen());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final gridImport = math.max(widget.data.gridPower, 0.0);
    final batteryDischarge = math.max(-widget.data.batteryPower, 0.0);
    final solar = math.max(widget.data.pvPower, 0.0);

    final dominant = [
      (label: l10n.solar, value: solar, icon: Icons.solar_power_rounded),
      (label: l10n.grid, value: gridImport, icon: Icons.electric_bolt_rounded),
      (
        label: l10n.battery,
        value: batteryDischarge,
        icon: Icons.battery_charging_full_rounded,
      ),
    ]..sort((a, b) => b.value.compareTo(a.value));

    final mainSource = dominant.first;

    return Dialog.fullscreen(
      child: WillPopScope(
        onWillPop: () async {
          await _restoreNativeFullscreen();
          return true;
        },
        child: Scaffold(
          appBar: AppBar(
            title: Text(l10n.equipmentStatus),
            actions: [
              IconButton(
                icon: const Icon(Icons.close_rounded),
                onPressed: _closeView,
              ),
            ],
          ),
          body: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(AppTheme.spacingL),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  AppCard(
                    borderRadius: context.expressive.cornerMedium,
                    enableBlur: false,
                    backgroundColor: theme.colorScheme.surfaceContainerLow,
                    padding: const EdgeInsets.all(AppTheme.spacingM),
                    child: Wrap(
                      spacing: AppTheme.spacingM,
                      runSpacing: AppTheme.spacingS,
                      children: [
                        _DashboardPill(
                          icon: mainSource.icon,
                          label:
                              'Main source: ${mainSource.label} ${mainSource.value.toStringAsFixed(0)} W',
                          color: theme.colorScheme.primary,
                        ),
                        _DashboardPill(
                          icon: Icons.home_rounded,
                          label:
                              '${l10n.load}: ${widget.data.loadPower.toStringAsFixed(0)} W',
                          color: AppTheme.loadColor,
                        ),
                        _DashboardPill(
                          icon: Icons.battery_6_bar_rounded,
                          label:
                              '${l10n.battery}: ${widget.data.batterySoc.toStringAsFixed(0)}% (${widget.data.batteryPower.toStringAsFixed(0)} W)',
                          color: AppTheme.batteryColor,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: AppTheme.spacingM),
                  Expanded(
                    child: SingleChildScrollView(
                      child: EnergyFlowDiagram(
                        data: widget.data,
                        showInteractiveToolbar: true,
                        autofocusShortcuts: true,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _GridOutageRuntimeBanner extends StatelessWidget {
  final double voltage;
  final double? backupHours;
  final double? batteryOnlyHours;
  final bool isSolarCoveringLoad;

  const _GridOutageRuntimeBanner({
    required this.voltage,
    required this.backupHours,
    required this.batteryOnlyHours,
    required this.isSolarCoveringLoad,
  });

  String _formatHoursLabel(AppLocalizations l10n, double? hours) {
    if (hours == null) return '--';
    if (hours.isInfinite) return l10n.runtimeInfinite;
    if (hours <= 0.02) return l10n.runtimeNow;
    if (hours < 1.0) {
      final mins = (hours * 60).round().clamp(1, 59);
      return l10n.runtimeMinutes(mins.toString());
    }
    final h = hours.floor();
    final mins = ((hours - h) * 60).round();
    if (mins <= 0) return l10n.runtimeHoursOnly(h.toString());
    return l10n.runtimeHoursMinutes(h.toString(), mins.toString());
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final effectiveBackupLabel = _formatHoursLabel(l10n, backupHours);
    final batteryOnlyLabel = _formatHoursLabel(l10n, batteryOnlyHours);

    return AppCard(
      borderRadius: context.expressive.cornerMedium,
      enableBlur: false,
      backgroundColor: theme.colorScheme.errorContainer.withValues(alpha: 0.58),
      padding: const EdgeInsets.all(AppTheme.spacingM),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.power_off_rounded,
                color: theme.colorScheme.onErrorContainer,
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Text(
                  l10n.gridOutageVisualTitle,
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: theme.colorScheme.onErrorContainer,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            l10n.gridOutageVisualBody(voltage.toStringAsFixed(0)),
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onErrorContainer,
            ),
          ),
          const SizedBox(height: AppTheme.spacingS),
          Text(
            l10n.backupRuntimeHybrid(effectiveBackupLabel),
            style: theme.textTheme.labelLarge?.copyWith(
              color: theme.colorScheme.onErrorContainer,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            l10n.backupRuntimeBatteryOnly(batteryOnlyLabel),
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onErrorContainer,
            ),
          ),
          if (isSolarCoveringLoad) ...[
            const SizedBox(height: AppTheme.spacingXS),
            Text(
              l10n.backupRuntimeSolarCoverHint,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onErrorContainer,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _QuickPulseSection extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const _QuickPulseSection({required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final entries = [
      (
        label: l10n.production,
        value: Formatters.formatPower(data.pvPower),
        subtitle: '${provider.pvTotalCapacityW.toStringAsFixed(0)} W',
        icon: Icons.wb_sunny_rounded,
        color: AppTheme.pvColor,
      ),
      (
        label: l10n.consumption,
        value: Formatters.formatPower(data.loadPower),
        subtitle:
            '${provider.currentCostPerHourUah.toStringAsFixed(2)} ${l10n.uahPerHour}',
        icon: Icons.home_rounded,
        color: AppTheme.loadColor,
      ),
      (
        label: l10n.battery,
        value: '${data.batterySoc.toStringAsFixed(0)}%',
        subtitle: Formatters.formatPower(data.batteryPower.abs()),
        icon: Icons.battery_6_bar_rounded,
        color: AppTheme.batteryColor,
      ),
      (
        label: l10n.grid,
        value: Formatters.formatPower(data.gridPower.abs()),
        subtitle: provider.isGridOutageDetected
            ? l10n.gridOutageDetectedShort
            : provider.isInverterOffline
                ? l10n.connectionOffline
                : l10n.connectionOnline,
        icon: Icons.electrical_services_rounded,
        color: provider.isGridOutageDetected
            ? theme.colorScheme.error
            : AppTheme.gridColor,
      ),
    ];

    return AppSectionCard(
      title: l10n.energyOverview,
      subtitle: l10n.realtimeReadings,
      icon: Icons.bolt_rounded,
      trailing: AppStatusChip(
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
      borderRadius: context.expressive.cornerXL,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth;
          final columns = width >= 980
              ? 4
              : width >= 640
                  ? 2
                  : 1;
          final cardWidth =
              (width - (columns - 1) * AppTheme.spacingM) / columns;
          return Wrap(
            spacing: AppTheme.spacingM,
            runSpacing: AppTheme.spacingM,
            children: entries
                .map(
                  (entry) => SizedBox(
                    width: cardWidth,
                    child: _PulseMetricTile(
                      label: entry.label,
                      value: entry.value,
                      subtitle: entry.subtitle,
                      icon: entry.icon,
                      color: entry.color,
                    ),
                  ),
                )
                .toList(),
          );
        },
      ),
    );
  }
}

class _PulseMetricTile extends StatelessWidget {
  final String label;
  final String value;
  final String subtitle;
  final IconData icon;
  final Color color;

  const _PulseMetricTile({
    required this.label,
    required this.value,
    required this.subtitle,
    required this.icon,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AppCard(
      borderRadius: context.expressive.cornerMedium,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerHigh,
      padding: const EdgeInsets.all(AppTheme.spacingM),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius:
                  BorderRadius.circular(context.expressive.cornerSmall),
            ),
            child: Icon(icon, size: 20, color: color),
          ),
          const SizedBox(width: AppTheme.spacingM),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: AppTheme.spacingXS),
                AnimatedSwitcher(
                  duration: context.motion.quick,
                  switchInCurve: context.motion.standardCurve,
                  switchOutCurve: context.motion.standardCurve,
                  child: Text(
                    value,
                    key: ValueKey(value),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ======================== STATS SECTION ========================

class _StatsSection extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const _StatsSection({required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final daily = provider.service.dailyEnergy.toStringAsFixed(1);
    final total = provider.service.totalEnergy.toStringAsFixed(0);
    final co2 = provider.service.co2Reduction.toStringAsFixed(1);
    final savedMoney = provider.monthSavedUah;
    final monthToPay = provider.monthToPayUah;
    final projectedSavedMoney = provider.projectedMonthSavedUah;
    final projectedMonthToPay = provider.projectedMonthToPayUah;
    final batteryEff =
        provider.batteryRoundTripEfficiencyPercent.toStringAsFixed(0);
    final nightShare = provider.nightEnergySharePercent.toStringAsFixed(0);
    final dayStart = provider.tariffDayStartHour.toString().padLeft(2, '0');
    final nightStart = provider.tariffNightStartHour.toString().padLeft(2, '0');
    final paymentTooltip = provider.monthEconomicsUsesTelemetryTou
        ? l10n.tooltipPaymentThisMonthTelemetry(dayStart, nightStart)
        : l10n.tooltipPaymentThisMonthEstimated(nightShare);
    final savedTooltip = provider.monthEconomicsUsesTelemetryTou
        ? l10n.tooltipMoneySavedMonthTelemetry(batteryEff)
        : l10n.tooltipMoneySavedMonthEstimated(nightShare, batteryEff);
    final sourceValue = provider.monthEconomicsUsesTelemetryTou
        ? l10n.calculationSourceTelemetry
        : l10n.calculationSourceFallback;
    final paymentPrefix =
        provider.monthEconomicsUsesEstimatedFallback ? '~' : '';
    const savingsPrefix = '~';
    const projectionPrefix = '~';
    return AppCard(
      borderRadius: expressive.cornerXL,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: LayoutBuilder(
          builder: (context, constraints) {
            // ignore: unused_local_variable
            final compact = constraints.maxWidth < 1080;
            final featuredMetrics = [
              _HeroMetricCard(
                label: l10n.today,
                value: daily,
                unit: 'kWh',
                icon: Icons.today_rounded,
                color: AppTheme.pvColor,
                tooltip: l10n.tooltipTodayEnergy,
              ),
              _HeroMetricCard(
                label: l10n.moneySavedMonth,
                value: savedMoney == null || savedMoney == 0.0
                    ? '0.0'
                    : '$savingsPrefix${savedMoney.toStringAsFixed(1)}',
                unit: l10n.currencyUah,
                icon: Icons.savings_rounded,
                color: AppTheme.batteryColor,
                tooltip: savedTooltip,
              ),
              _HeroMetricCard(
                label: l10n.paymentThisMonth,
                value: monthToPay == null || monthToPay == 0.0
                    ? '0.0'
                    : '$paymentPrefix${monthToPay.toStringAsFixed(1)}',
                unit: l10n.currencyUah,
                icon: Icons.receipt_long_rounded,
                color: AppTheme.gridColor,
                tooltip: paymentTooltip,
              ),
            ];

            final supportingMetrics = [
              _SupportingMetricCard(
                label: l10n.total,
                value: total,
                unit: 'kWh',
                icon: Icons.assessment_rounded,
                color: AppTheme.pvColor,
                tooltip: l10n.tooltipTotalEnergy,
              ),
              _SupportingMetricCard(
                label: 'CO2',
                value: co2,
                unit: 'kg',
                icon: Icons.eco_rounded,
                color: AppTheme.batteryColor,
                tooltip: l10n.tooltipCo2,
              ),
              _SupportingMetricCard(
                label: l10n.projectedSavedMonth,
                value: projectedSavedMoney == null || projectedSavedMoney == 0.0
                    ? '0.0'
                    : '$projectionPrefix${projectedSavedMoney.toStringAsFixed(1)}',
                unit: l10n.currencyUah,
                icon: Icons.trending_up_rounded,
                color: AppTheme.batteryColor,
                tooltip: l10n.tooltipProjectedSavedMonth,
              ),
              _SupportingMetricCard(
                label: l10n.projectedPaymentMonth,
                value: projectedMonthToPay == null || projectedMonthToPay == 0.0
                    ? '0.0'
                    : '$projectionPrefix${projectedMonthToPay.toStringAsFixed(1)}',
                unit: l10n.currencyUah,
                icon: Icons.calendar_month_rounded,
                color: AppTheme.gridColor,
                tooltip: l10n.tooltipProjectedPaymentMonth,
              ),
            ];

            final summary = Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 40,
                      height: 40,
                      decoration: BoxDecoration(
                        color: theme.colorScheme.surfaceContainerHighest,
                        borderRadius:
                            BorderRadius.circular(expressive.cornerMedium),
                      ),
                      child: Icon(
                        Icons.dashboard_customize_rounded,
                        color: theme.colorScheme.primary,
                      ),
                    ),
                    const SizedBox(width: AppTheme.spacingM),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            l10n.energyOverview,
                            style: theme.textTheme.titleLarge?.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: AppTheme.spacingXS),
                          Text(
                            '${l10n.calculationSourceLabel}: $sourceValue',
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
                    _DashboardPill(
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
                    _DashboardPill(
                      icon: Icons.tune_rounded,
                      label: sourceValue,
                      color: theme.colorScheme.primary,
                    ),
                    _DashboardPill(
                      icon: Icons.bolt_rounded,
                      label:
                          '${Formatters.formatPower(data.loadPower)} / ${Formatters.formatPower(provider.inverterMaxPowerW)}',
                      color: AppTheme.loadColor,
                    ),
                    _DashboardPill(
                      icon: Icons.currency_exchange_rounded,
                      label:
                          '${l10n.currentCostPerHour}: ${provider.currentCostPerHourUah.toStringAsFixed(2)} ${l10n.uahPerHour}',
                      color: AppTheme.gridColor,
                    ),
                    _DashboardPill(
                      icon: Icons.repeat_rounded,
                      label: '${l10n.batteryCycles}: ${provider.batteryCycles}',
                      color: AppTheme.batteryColor,
                    ),
                    _DashboardPill(
                      icon: Icons.health_and_safety_rounded,
                      label: l10n.battSohPercent(
                        provider.batteryHealthPercent.toStringAsFixed(0),
                      ),
                      color: theme.colorScheme.tertiary,
                    ),
                  ],
                ),
                const SizedBox(height: AppTheme.spacingL),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final metricCompact = constraints.maxWidth < 760;
                    if (metricCompact) {
                      return Column(
                        children: [
                          for (var i = 0; i < featuredMetrics.length; i++) ...[
                            featuredMetrics[i],
                            if (i != featuredMetrics.length - 1)
                              const SizedBox(height: AppTheme.spacingM),
                          ],
                        ],
                      );
                    }
                    return Row(
                      children: [
                        for (var i = 0; i < featuredMetrics.length; i++) ...[
                          Expanded(child: featuredMetrics[i]),
                          if (i != featuredMetrics.length - 1)
                            const SizedBox(width: AppTheme.spacingM),
                        ],
                      ],
                    );
                  },
                ),
                const SizedBox(height: AppTheme.spacingM),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final width = constraints.maxWidth;
                    final columns = width >= 900
                        ? 4
                        : width >= 540
                            ? 2
                            : 1;
                    final cardWidth =
                        (width - (AppTheme.spacingM * (columns - 1))) / columns;
                    return Wrap(
                      spacing: AppTheme.spacingM,
                      runSpacing: AppTheme.spacingM,
                      children: supportingMetrics
                          .map(
                            (metric) => SizedBox(
                              width: cardWidth,
                              child: metric,
                            ),
                          )
                          .toList(),
                    );
                  },
                ),
              ],
            );

            return summary;
          },
        ),
      ),
    );
  }
}

class _HeroMetricCard extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  final IconData icon;
  final Color color;
  final String? tooltip;

  const _HeroMetricCard({
    required this.label,
    required this.value,
    required this.unit,
    required this.icon,
    required this.color,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    final card = _MetricCard(
      label: label,
      value: value,
      unit: unit,
      icon: icon,
      color: color,
      compact: false,
    );
    return tooltip == null ? card : Tooltip(message: tooltip!, child: card);
  }
}

class _SupportingMetricCard extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  final IconData icon;
  final Color color;
  final String? tooltip;

  const _SupportingMetricCard({
    required this.label,
    required this.value,
    required this.unit,
    required this.icon,
    required this.color,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    final card = _MetricCard(
      label: label,
      value: value,
      unit: unit,
      icon: icon,
      color: color,
      compact: true,
    );
    return tooltip == null ? card : Tooltip(message: tooltip!, child: card);
  }
}

class _MetricCard extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  final IconData icon;
  final Color color;
  final bool compact;

  const _MetricCard({
    required this.label,
    required this.value,
    required this.unit,
    required this.icon,
    required this.color,
    required this.compact,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final expressive = context.expressive;

    if (compact) {
      return AppCard(
        borderRadius: expressive.cornerMedium,
        enableBlur: false,
        backgroundColor: theme.colorScheme.surfaceContainerLow,
        padding: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.all(AppTheme.spacingM),
          child: Row(
            children: [
              _MetricIcon(icon: icon, color: color, size: 36),
              const SizedBox(width: AppTheme.spacingM),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: AppTheme.spacingXS),
                    AnimatedSwitcher(
                      duration: context.motion.quick,
                      switchInCurve: context.motion.standardCurve,
                      switchOutCurve: context.motion.standardCurve,
                      child: Text(
                        '$value $unit',
                        key: ValueKey('$label-$value-$unit'),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    }

    return AppCard(
      borderRadius: expressive.cornerLarge,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _MetricIcon(icon: icon, color: color, size: 44),
          const SizedBox(height: AppTheme.spacingM),
          Text(
            label,
            style: theme.textTheme.labelMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Wrap(
            crossAxisAlignment: WrapCrossAlignment.end,
            spacing: AppTheme.spacingXS,
            children: [
              AnimatedSwitcher(
                duration: context.motion.quick,
                switchInCurve: context.motion.standardCurve,
                switchOutCurve: context.motion.standardCurve,
                child: Text(
                  value,
                  key: ValueKey('$label-$value'),
                  style: theme.textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    height: 1,
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: AppTheme.spacingXS),
                child: Text(
                  unit,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _MetricIcon extends StatelessWidget {
  final IconData icon;
  final Color color;
  final double size;

  const _MetricIcon({
    required this.icon,
    required this.color,
    required this.size,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(context.expressive.cornerMedium),
      ),
      child: Icon(icon, size: size * 0.5, color: color),
    );
  }
}

class _DashboardPill extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color color;

  const _DashboardPill({
    required this.icon,
    required this.label,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AppCard(
      borderRadius: 999,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerHigh,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingM,
        vertical: AppTheme.spacingS,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: color),
          const SizedBox(width: AppTheme.spacingS),
          Text(
            label,
            style: theme.textTheme.labelMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _MonthEconomicsBreakdown extends StatelessWidget {
  final AppStateProvider provider;

  const _MonthEconomicsBreakdown({required this.provider});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final l10n = AppLocalizations.of(context)!;
    final load = provider.monthLoadKwh;
    final grid = provider.monthGridKwh;
    final selfConsumed = provider.monthSelfConsumedKwh;
    final gridCost = provider.monthToPayUah;
    final savedCost = provider.monthSavedUah;
    final dailyEconomics = provider.monthDailyEconomics;
    final progress = provider.monthProgressFraction;
    final economicsMethod = provider.monthEconomicsUsesTelemetryTou
        ? l10n.economicsMethodTelemetry(
            provider.batteryRoundTripEfficiencyPercent.toStringAsFixed(0),
          )
        : l10n.economicsMethodEstimated(
            provider.nightEnergySharePercent.toStringAsFixed(0),
          );
    final sourceValue = provider.monthEconomicsUsesTelemetryTou
        ? l10n.calculationSourceTelemetry
        : l10n.calculationSourceFallback;
    final accuracyValue = provider.monthEconomicsUsesTelemetryTou
        ? l10n.calculationAccuracyHigh
        : l10n.calculationAccuracyEstimated;
    final paymentPrefix =
        provider.monthEconomicsUsesEstimatedFallback ? '~' : '';
    const savingsPrefix = '~';
    final effectiveTariffTooltip = provider.monthEconomicsUsesTelemetryTou
        ? l10n.tooltipEffectiveTariffTelemetry(
            provider.tariffDayStartHour.toString().padLeft(2, '0'),
            provider.tariffNightStartHour.toString().padLeft(2, '0'),
          )
        : l10n.effectiveTariffFormula(
            provider.dayTariffUahPerKwh.toStringAsFixed(2),
            provider.nightTariffUahPerKwh.toStringAsFixed(2),
            provider.nightEnergySharePercent.toStringAsFixed(0),
          );

    String fmt(double? v) => v == null ? '--' : v.toStringAsFixed(1);

    return AppCard(
      borderRadius: expressive.cornerLarge,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(expressive.cornerSmall),
                ),
                child: Icon(
                  Icons.pie_chart_rounded,
                  color: theme.colorScheme.primary,
                ),
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      l10n.monthlyEnergyBreakdown,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Text(
                      '${DateTime.now().month.toString().padLeft(2, '0')}.${DateTime.now().year}',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              Tooltip(
                message: l10n.tooltipMonthProgress,
                child: Text(
                  '${provider.monthProgressPercent}%',
                  style: theme.textTheme.labelMedium,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Tooltip(
            message:
                '${l10n.monthLoadEnergy} = ${l10n.monthGridImport} + ${l10n.monthSelfConsumed}',
            child: Text(
              '${l10n.monthLoadEnergy} = ${l10n.monthGridImport} + ${l10n.monthSelfConsumed}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          const SizedBox(height: AppTheme.spacingS),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 7,
              value: progress,
            ),
          ),
          const SizedBox(height: AppTheme.spacingM),
          _BreakdownItem(
            label: l10n.calculationSourceLabel,
            value: sourceValue,
            color: AppTheme.gridColor,
            tooltip: economicsMethod,
          ),
          const SizedBox(height: AppTheme.spacingS),
          _BreakdownItem(
            label: l10n.calculationAccuracyLabel,
            value: accuracyValue,
            color: AppTheme.loadColor,
          ),
          const SizedBox(height: AppTheme.spacingM),
          LayoutBuilder(builder: (context, constraints) {
            final compact = constraints.maxWidth < 700;
            final items = [
              _BreakdownItem(
                label: l10n.monthLoadEnergy,
                value: '${fmt(load)} kWh',
                color: AppTheme.pvColor,
              ),
              _BreakdownItem(
                label: l10n.monthGridImport,
                value: '${fmt(grid)} kWh',
                color: AppTheme.gridColor,
              ),
              _BreakdownItem(
                label: l10n.monthSelfConsumed,
                value: '${fmt(selfConsumed)} kWh',
                color: AppTheme.batteryColor,
              ),
            ];

            if (compact) {
              return Column(
                children: [
                  for (var i = 0; i < items.length; i++) ...[
                    items[i],
                    if (i != items.length - 1)
                      const SizedBox(height: AppTheme.spacingS),
                  ],
                ],
              );
            }

            return Row(
              children: [
                for (var i = 0; i < items.length; i++) ...[
                  Expanded(child: items[i]),
                  if (i != items.length - 1)
                    const SizedBox(width: AppTheme.spacingM),
                ],
              ],
            );
          }),
          const SizedBox(height: AppTheme.spacingM),
          LayoutBuilder(builder: (context, constraints) {
            final compact = constraints.maxWidth < 700;
            final moneyItems = [
              _BreakdownItem(
                label: l10n.monthGridCost,
                value:
                    '${gridCost == null || gridCost == 0.0 ? '0.0' : '$paymentPrefix${gridCost.toStringAsFixed(1)}'} ${l10n.currencyUah}',
                color: AppTheme.gridColor,
              ),
              _BreakdownItem(
                label: l10n.monthSavedCost,
                value:
                    '${savedCost == null || savedCost == 0.0 ? '0.0' : '$savingsPrefix${savedCost.toStringAsFixed(1)}'} ${l10n.currencyUah}',
                color: AppTheme.batteryColor,
              ),
              _BreakdownItem(
                label: l10n.monthEffectiveTariff,
                value:
                    '${provider.effectiveTariffUahPerKwh.toStringAsFixed(2)} ${l10n.energyTariffUnit}',
                color: AppTheme.loadColor,
                tooltip: effectiveTariffTooltip,
              ),
            ];

            if (compact) {
              return Column(
                children: [
                  for (var i = 0; i < moneyItems.length; i++) ...[
                    moneyItems[i],
                    if (i != moneyItems.length - 1)
                      const SizedBox(height: AppTheme.spacingS),
                  ],
                ],
              );
            }

            return Row(
              children: [
                for (var i = 0; i < moneyItems.length; i++) ...[
                  Expanded(child: moneyItems[i]),
                  if (i != moneyItems.length - 1)
                    const SizedBox(width: AppTheme.spacingM),
                ],
              ],
            );
          }),
          if (dailyEconomics.isNotEmpty) ...[
            const SizedBox(height: AppTheme.spacingM),
            RepaintBoundary(
                child: _MonthEconomicsMiniChart(data: dailyEconomics)),
          ],
        ],
      ),
    );
  }
}

class _MonthEconomicsMiniChart extends StatelessWidget {
  final List<({int day, double payableUah, double savedUah})> data;

  const _MonthEconomicsMiniChart({required this.data});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final bars = data
        .map(
          (d) => BarChartGroupData(
            x: d.day,
            barRods: [
              BarChartRodData(
                toY: d.payableUah,
                width: 6,
                borderRadius: BorderRadius.circular(2),
                color: AppTheme.gridColor.withValues(alpha: 0.85),
              ),
              BarChartRodData(
                toY: d.savedUah,
                width: 6,
                borderRadius: BorderRadius.circular(2),
                color: AppTheme.batteryColor,
              ),
            ],
            barsSpace: 3,
          ),
        )
        .toList(growable: false);

    return SizedBox(
      height: 120,
      child: BarChart(
        BarChartData(
          barGroups: bars,
          barTouchData: BarTouchData(
            enabled: true,
            touchTooltipData: BarTouchTooltipData(
              getTooltipColor: (_) => theme.colorScheme.surfaceContainerHighest,
              getTooltipItem: (group, groupIndex, rod, rodIndex) {
                final label =
                    rodIndex == 0 ? l10n.monthGridCost : l10n.monthSavedCost;
                final amount = rod.toY.isFinite ? rod.toY : 0.0;
                return BarTooltipItem(
                  '${group.x}\n$label: ${amount.toStringAsFixed(0)} ${l10n.currencyUah}',
                  (theme.textTheme.labelMedium ?? const TextStyle()).copyWith(
                    color: rod.color ?? theme.colorScheme.primary,
                    fontWeight: FontWeight.w700,
                  ),
                );
              },
            ),
          ),
          gridData: const FlGridData(show: false),
          borderData: FlBorderData(show: false),
          titlesData: FlTitlesData(
            topTitles:
                const AxisTitles(sideTitles: SideTitles(showTitles: false)),
            rightTitles:
                const AxisTitles(sideTitles: SideTitles(showTitles: false)),
            leftTitles:
                const AxisTitles(sideTitles: SideTitles(showTitles: false)),
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                interval: 5,
                getTitlesWidget: (value, meta) => Text(
                  value.toInt().toString(),
                  style: Theme.of(context).textTheme.labelSmall,
                ),
              ),
            ),
          ),
          groupsSpace: 5,
        ),
      ),
    );
  }
}

class _BreakdownItem extends StatelessWidget {
  final String label;
  final String value;
  final Color color;
  final String? tooltip;

  const _BreakdownItem({
    required this.label,
    required this.value,
    required this.color,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final card = AppCard(
      borderRadius: expressive.cornerMedium,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerHigh,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingM,
        vertical: AppTheme.spacingS,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Text(
                  label,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            value,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
    if (tooltip == null) return card;
    return Tooltip(message: tooltip!, child: card);
  }
}

// ======================== SYSTEM CAPACITY SECTION ========================

class _SystemCapacitySection extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const _SystemCapacitySection({
    required this.provider,
    required this.data,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final loadPercent = provider.inverterMaxPowerW > 0
        ? (data.loadPower / provider.inverterMaxPowerW).clamp(0.0, 1.0)
        : 0.0;
    final isOffline = provider.isInverterOffline;

    Color getLoadColor(double percent) {
      if (percent > 0.85) return const Color(0xFFEF4444);
      if (percent > 0.65) return const Color(0xFFF97316);
      return const Color(0xFF10B981);
    }

    return AppCard(
      borderRadius: expressive.cornerLarge,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSectionTitle(
              title: l10n.equipmentStatus,
              subtitle:
                  isOffline ? l10n.connectionOffline : l10n.connectionOnline,
              icon: Icons.memory_rounded,
            ),
            const SizedBox(height: AppTheme.spacingXS),
            _ConnectionBadge(
              isOffline: isOffline,
              onlineLabel: l10n.connectionOnline,
              offlineLabel: l10n.connectionOffline,
              lastUpdatedPrefix: l10n.lastRealtimeUpdate,
              lastUpdatedAt: provider.lastSuccessfulRealtimeAt,
            ),
            const SizedBox(height: AppTheme.spacingL),
            AppCard(
              borderRadius: expressive.cornerMedium,
              enableBlur: false,
              backgroundColor: theme.colorScheme.surfaceContainerHigh,
              padding: EdgeInsets.zero,
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.spacingM),
                child: AppProgressBar(
                  label: l10n.inverterLoad,
                  value: data.loadPower,
                  maxValue: provider.inverterMaxPowerW,
                  color: getLoadColor(loadPercent),
                  suffix: 'W',
                  tooltip: l10n.tooltipInverterLoad,
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingM),
            AppCard(
              borderRadius: expressive.cornerMedium,
              enableBlur: false,
              backgroundColor: theme.colorScheme.surfaceContainerHigh,
              padding: EdgeInsets.zero,
              child: Padding(
                padding: const EdgeInsets.all(AppTheme.spacingM),
                child: AppProgressBar(
                  label: l10n.pvGeneration,
                  value: data.pvPower,
                  maxValue: provider.pvTotalCapacityW,
                  color: AppTheme.pvColor,
                  suffix: 'W',
                  tooltip: l10n.tooltipPvGeneration,
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingM),
            AppCard(
              borderRadius: expressive.cornerMedium,
              enableBlur: false,
              backgroundColor: theme.colorScheme.surfaceContainerHigh,
              padding: const EdgeInsets.all(AppTheme.spacingM),
              child: Row(
                children: [
                  Icon(
                    Icons.battery_6_bar_rounded,
                    color: theme.colorScheme.primary,
                  ),
                  const SizedBox(width: AppTheme.spacingS),
                  Expanded(
                    child: Text(
                      '${AppLocalizations.of(context)!.battery}: ${data.batterySoc.toStringAsFixed(0)}%',
                      style: theme.textTheme.titleMedium,
                    ),
                  ),
                  Text(
                    Formatters.formatPower(data.batteryPower.abs()),
                    style: theme.textTheme.labelLarge?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConnectionBadge extends StatelessWidget {
  final bool isOffline;
  final String onlineLabel;
  final String offlineLabel;
  final String lastUpdatedPrefix;
  final DateTime? lastUpdatedAt;

  const _ConnectionBadge({
    required this.isOffline,
    required this.onlineLabel,
    required this.offlineLabel,
    required this.lastUpdatedPrefix,
    required this.lastUpdatedAt,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = isOffline ? const Color(0xFFEF4444) : const Color(0xFF10B981);
    final text = isOffline ? offlineLabel : onlineLabel;
    final icon = isOffline ? Icons.cloud_off_rounded : Icons.cloud_done_rounded;
    final updated = lastUpdatedAt;
    final timeLabel = updated == null
        ? '--:--:--'
        : '${updated.hour.toString().padLeft(2, '0')}:${updated.minute.toString().padLeft(2, '0')}:${updated.second.toString().padLeft(2, '0')}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          borderRadius: 999,
          enableBlur: false,
          backgroundColor: color.withValues(alpha: 0.14),
          padding: const EdgeInsets.symmetric(
            horizontal: AppTheme.spacingS,
            vertical: AppTheme.spacingXS,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: color),
              const SizedBox(width: AppTheme.spacingS),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 150),
                child: Text(
                  text,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: theme.colorScheme.onSurface,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppTheme.spacingXS),
        Text(
          '$lastUpdatedPrefix: $timeLabel',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.labelSmall
              ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      ],
    );
  }
}

// ======================== ENERGY CHART SECTION ========================

class _EnergyChartSection extends StatefulWidget {
  final AppStateProvider provider;

  const _EnergyChartSection({required this.provider});

  @override
  State<_EnergyChartSection> createState() => _EnergyChartSectionState();
}

class _EnergyChartSectionState extends State<_EnergyChartSection> {
  static const Color _forecastSeriesColor = Color(0xFF38BDF8);

  int _selectedRange = 0;
  DateTime _currentDate = DateTime.now();
  bool _showProduction = true;
  bool _showConsumption = true;
  bool _showBattery = true;
  bool _showGrid = false;
  bool _showForecast = true;

  bool _isLoading = true;
  bool _isBackgroundRefreshing = false;
  List<FlSpot> _productionData = [];
  List<FlSpot> _consumptionData = [];
  List<FlSpot> _batteryData = [];
  List<FlSpot> _gridData = [];
  List<FlSpot> _forecastData = [];
  Timer? _chartDebounce;
  Timer? _autoRefreshTimer;
  int _chartRequestSeq = 0;
  String? _lastRenderLogSignature;
  DateTime? _lastChartRefreshedAt;
  List<DailySolarForecast> _multiDayForecast = const [];
  bool _isDailyForecastLoading = true;

  static const Duration _autoRefreshInterval = Duration(minutes: 5);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance
        .addPostFrameCallback((_) => _scheduleFetchChartData(immediate: true));
    _autoRefreshTimer = Timer.periodic(_autoRefreshInterval, (_) {
      _maybeAutoRefresh();
    });
  }

  @override
  void dispose() {
    _chartDebounce?.cancel();
    _autoRefreshTimer?.cancel();
    super.dispose();
  }

  /// Silently refreshes chart only when viewing today in day mode.
  void _maybeAutoRefresh() {
    if (!mounted) return;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final viewing =
        DateTime(_currentDate.year, _currentDate.month, _currentDate.day);
    if (_selectedRange == 0 && viewing == today) {
      _fetchChartData(background: true);
    }
  }

  void _scheduleFetchChartData({bool immediate = false}) {
    _chartDebounce?.cancel();
    if (immediate) {
      _fetchChartData();
      return;
    }
    _chartDebounce = Timer(const Duration(milliseconds: 350), _fetchChartData);
  }

  void _startRangeLoadingState() {
    if (!mounted) return;
    setState(() {
      _isLoading = true;
      _isBackgroundRefreshing = false;
      _productionData = [];
      _consumptionData = [];
      _batteryData = [];
      _gridData = [];
      _forecastData = [];
    });
    LogService.log(
        '[chart] reset before fetch: range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}');
  }

  Future<void> _fetchChartData({bool background = false}) async {
    final requestId = ++_chartRequestSeq;
    final totalSw = Stopwatch()..start();
    LogService.log(
        '[chart] fetch start: requestId=$requestId, range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}, bg=$background');
    if (!background) {
      if (mounted && !_isLoading) setState(() => _isLoading = true);
    } else {
      if (mounted) setState(() => _isBackgroundRefreshing = true);
    }

    try {
      final chartSw = Stopwatch()..start();
      final chartFuture = widget.provider.service
          .getChartData(_selectedRange, _currentDate)
          .whenComplete(chartSw.stop);
      final dailySw = Stopwatch()..start();
      final dailyForecastFuture = widget.provider.weatherService
          .fetchDailyForecast(
            pvCapacityW: widget.provider.pvTotalCapacityW,
            efficiency: 0.85,
            historicalPvData: widget.provider.historicalPvData,
            daysAhead: 16,
          )
          .whenComplete(dailySw.stop);
      final daySw = Stopwatch()..start();
      final dayForecastFuture = (_selectedRange == 0
              ? widget.provider.weatherService.fetchLocalForecast(
                  pvCapacityW: widget.provider.pvTotalCapacityW,
                  efficiency: 0.85,
                  historicalPvData: widget.provider.historicalPvData,
                  targetDate: _currentDate,
                )
              : Future<Map<String, double>>.value(const <String, double>{}))
          .whenComplete(daySw.stop);

      final data = await chartFuture;
      final dailyForecast = await dailyForecastFuture;
      final forecast = await dayForecastFuture;

      if (!mounted || requestId != _chartRequestSeq) {
        LogService.log(
            '[chart] stale response ignored: requestId=$requestId, active=$_chartRequestSeq');
        return;
      }

      if (mounted) {
        final forecastSpots = _selectedRange == 0
            ? buildDayForecastSpots(forecast)
            : buildRangeForecastSpots(dailyForecast);

        setState(() {
          _productionData = normalizeSpots(data['pv'] ?? []);
          _consumptionData = normalizeSpots(data['load'] ?? []);
          _batteryData = normalizeSpots(data['battery'] ?? []);
          _gridData = normalizeSpots(data['grid'] ?? []);
          _forecastData = forecastSpots;
          _isLoading = false;
          _isBackgroundRefreshing = false;
          _lastChartRefreshedAt = DateTime.now();
          _multiDayForecast = dailyForecast;
          _isDailyForecastLoading = false;
        });

        logChartUiSummary('chart.ui fetched');
        totalSw.stop();
        LogService.log(
            '[chart] timing requestId=$requestId total=${totalSw.elapsedMilliseconds}ms '
            'chart=${chartSw.elapsedMilliseconds}ms daily=${dailySw.elapsedMilliseconds}ms '
            'day=${daySw.elapsedMilliseconds}ms');
      }
    } catch (e, stack) {
      totalSw.stop();
      LogService.log('[chart] fetch failed', error: e, stack: stack);
      if (!mounted || requestId != _chartRequestSeq) return;
      setState(() {
        _isLoading = false;
        _isBackgroundRefreshing = false;
        _isDailyForecastLoading = false;
      });
    }
  }

  List<FlSpot> buildRangeForecastSpots(List<DailySolarForecast> forecast) {
    if (forecast.isEmpty) return const [];

    if (_selectedRange == 1) {
      final weekStart = startOfWeek(_currentDate);
      final weekEnd = weekStart.add(const Duration(days: 6));
      return normalizeSpots(
        forecast.where((item) {
          final day = DateTime(item.date.year, item.date.month, item.date.day);
          return !day.isBefore(weekStart) && !day.isAfter(weekEnd);
        }).map((item) {
          final day = DateTime(item.date.year, item.date.month, item.date.day);
          return FlSpot(
              day.difference(weekStart).inDays.toDouble(), item.energyWh);
        }).toList(),
      );
    }

    if (_selectedRange == 2) {
      return normalizeSpots(
        forecast
            .where((item) =>
                item.date.year == _currentDate.year &&
                item.date.month == _currentDate.month)
            .map((item) => FlSpot(item.date.day.toDouble(), item.energyWh))
            .toList(),
      );
    }

    return const [];
  }

  List<FlSpot> buildDayForecastSpots(Map<String, double> forecast) {
    var spots = <FlSpot>[];
    var todayPrefix =
        "${_currentDate.year}-${_currentDate.month.toString().padLeft(2, '0')}-${_currentDate.day.toString().padLeft(2, '0')}";

    forecast.forEach((timeStr, value) {
      if (timeStr.startsWith(todayPrefix)) {
        final timeParts = timeStr.split(' ');
        if (timeParts.length > 1) {
          final hourStr = timeParts[1].split(':')[0];
          final minStr = timeParts[1].split(':')[1];
          final hour = double.tryParse(hourStr) ?? 0.0;
          final min = double.tryParse(minStr) ?? 0.0;
          final hourDouble = hour + min / 60.0;
          spots.add(FlSpot(hourDouble, value));
        }
      }
    });
    return normalizeSpots(spots);
  }

  List<FlSpot> normalizeSpots(List<FlSpot> raw) {
    final filtered = raw
        .where((s) => s.x.isFinite && s.y.isFinite)
        .map((s) => FlSpot(s.x, s.y))
        .toList();
    if (filtered.length < 2) return filtered;

    filtered.sort((a, b) => a.x.compareTo(b.x));

    // Merge duplicate/near-duplicate X values to avoid vertical segments that
    // can make cubic interpolation fold back into loops.
    const eps = 0.001;
    final merged = <FlSpot>[];
    for (final spot in filtered) {
      if (merged.isEmpty || (spot.x - merged.last.x).abs() > eps) {
        merged.add(spot);
      } else {
        merged[merged.length - 1] =
            FlSpot(merged.last.x, (merged.last.y + spot.y) / 2);
      }
    }
    return merged;
  }

  void changeDate(int offset) {
    setState(() {
      if (_selectedRange == 0) {
        _currentDate = _currentDate.add(Duration(days: offset));
      } else if (_selectedRange == 1) {
        _currentDate = _currentDate.add(Duration(days: 7 * offset));
      } else {
        _currentDate = DateTime(
            _currentDate.year, _currentDate.month + offset, _currentDate.day);
      }
    });
    _startRangeLoadingState();
    _scheduleFetchChartData();
  }

  String getDateText() {
    if (_selectedRange == 0) {
      return '${_currentDate.day.toString().padLeft(2, '0')}.${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    } else if (_selectedRange == 1) {
      final start = startOfWeek(_currentDate);
      final end = start.add(const Duration(days: 6));
      return '${start.day.toString().padLeft(2, '0')}.${start.month.toString().padLeft(2, '0')} - ${end.day.toString().padLeft(2, '0')}.${end.month.toString().padLeft(2, '0')}';
    } else {
      return '${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    }
  }

  DateTime startOfWeek(DateTime date) {
    final normalized = DateTime(date.year, date.month, date.day);
    return normalized.subtract(Duration(days: normalized.weekday - 1));
  }

  double? futureStartX0() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);

    if (_selectedRange == 0) {
      final currentDay =
          DateTime(_currentDate.year, _currentDate.month, _currentDate.day);
      if (currentDay != today) return null;
      final x = now.hour + (now.minute / 60.0);
      return x >= 23 ? null : x;
    }

    if (_selectedRange == 1) {
      final weekStart = startOfWeek(_currentDate);
      final thisWeekStart = startOfWeek(today);
      if (weekStart != thisWeekStart) return null;
      final dayIndex = now.weekday - 1;
      final futureStart = dayIndex + 1.0;
      return futureStart > 6 ? null : futureStart;
    }

    final sameMonth =
        _currentDate.year == now.year && _currentDate.month == now.month;
    if (!sameMonth) return null;
    final lastDay = DateTime(now.year, now.month + 1, 0).day;
    final futureStart = now.day + 1.0;
    return futureStart > lastDay ? null : futureStart;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;

    return AppCard(
      borderRadius: expressive.cornerLarge,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSectionTitle(
              title: l10n.energyOverview,
              subtitle: getDateText(),
              icon: Icons.insights_rounded,
            ),
            const SizedBox(height: AppTheme.spacingM),
            LayoutBuilder(
              builder: (context, constraints) {
                final isCompact = constraints.maxWidth < 760;
                final selector = AppCard(
                  borderRadius: expressive.cornerMedium,
                  enableBlur: false,
                  backgroundColor: theme.colorScheme.surfaceContainerHigh,
                  padding: EdgeInsets.zero,
                  child: Padding(
                    padding: const EdgeInsets.all(AppTheme.spacingXS),
                    child: buildTimeSelector(l10n),
                  ),
                );
                final refresh = AppCard(
                  borderRadius: expressive.cornerMedium,
                  enableBlur: false,
                  backgroundColor: theme.colorScheme.surfaceContainerHigh,
                  padding: EdgeInsets.zero,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppTheme.spacingM,
                      vertical: AppTheme.spacingS,
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (_lastChartRefreshedAt != null)
                          _ChartRefreshBadge(
                            refreshedAt: _lastChartRefreshedAt!,
                            isRefreshing: _isBackgroundRefreshing,
                          ),
                        if (_lastChartRefreshedAt != null)
                          const SizedBox(width: AppTheme.spacingS),
                        IconButton.filledTonal(
                          onPressed: _isBackgroundRefreshing
                              ? null
                              : () => _fetchChartData(background: true),
                          tooltip: l10n.refreshChart,
                          icon: _isBackgroundRefreshing
                              ? SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: theme.colorScheme.primary,
                                  ),
                                )
                              : const Icon(Icons.refresh_rounded, size: 18),
                        ),
                      ],
                    ),
                  ),
                );
                if (isCompact) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      selector,
                      const SizedBox(height: AppTheme.spacingS),
                      refresh
                    ],
                  );
                }
                return Row(
                  children: [
                    Expanded(child: selector),
                    const SizedBox(width: AppTheme.spacingM),
                    refresh,
                  ],
                );
              },
            ),
            const SizedBox(height: AppTheme.spacingM),
            buildDateNavigator(),
            const SizedBox(height: AppTheme.spacingM),
            buildLegend(l10n),
            if (_selectedRange == 0 || _showBattery) ...[
              const SizedBox(height: AppTheme.spacingS),
              buildBatterySignHint(context),
            ],
            const SizedBox(height: AppTheme.spacingL),
            AppCard(
              borderRadius: expressive.cornerLarge,
              enableBlur: false,
              backgroundColor: theme.colorScheme.surface,
              padding: EdgeInsets.zero,
              child: SizedBox(
                height: 320,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppTheme.spacingS,
                    AppTheme.spacingM,
                    AppTheme.spacingM,
                    AppTheme.spacingS,
                  ),
                  child: ExcludeSemantics(
                    child: RepaintBoundary(
                      child: _isLoading
                          ? const _ChartSkeleton()
                          : buildChart(context),
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: AppTheme.spacingL),
            buildMultiDayForecast(context, l10n),
          ],
        ),
      ),
    );
  }

  Widget buildMultiDayForecast(BuildContext context, AppLocalizations l10n) {
    final theme = Theme.of(context);
    final expressive = context.expressive;
    if (_isDailyForecastLoading && _multiDayForecast.isEmpty) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (_multiDayForecast.isEmpty) {
      return AppEmptyState(
        title: l10n.forecastNextDays,
        message: l10n.forecastUnavailable,
        icon: Icons.wb_cloudy_outlined,
      );
    }

    final previewDays = _multiDayForecast.take(7).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppSectionTitle(
          title: l10n.forecastNextDays,
          subtitle: l10n.forecastPeak,
          icon: Icons.wb_sunny_rounded,
        ),
        const SizedBox(height: AppTheme.spacingM),
        LayoutBuilder(
          builder: (context, constraints) {
            const spacing = AppTheme.spacingM;
            final width = constraints.maxWidth;
            final columns = width >= 980
                ? 4
                : width >= 740
                    ? 3
                    : width >= 520
                        ? 2
                        : 1;
            final cardWidth = ((width - (columns - 1) * spacing) / columns)
                .clamp(160.0, 320.0);

            return Wrap(
              spacing: spacing,
              runSpacing: spacing,
              children: previewDays
                  .map((day) => SizedBox(
                        width: cardWidth,
                        child: AppCard(
                          borderRadius: expressive.cornerMedium,
                          padding: const EdgeInsets.all(AppTheme.spacingM),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Container(
                                    width: 34,
                                    height: 34,
                                    decoration: BoxDecoration(
                                      color: theme
                                          .colorScheme.surfaceContainerHighest,
                                      borderRadius: BorderRadius.circular(
                                        expressive.cornerSmall,
                                      ),
                                    ),
                                    child: Icon(
                                      Icons.wb_sunny_rounded,
                                      size: 18,
                                      color: theme.colorScheme.tertiary,
                                    ),
                                  ),
                                  const SizedBox(width: AppTheme.spacingS),
                                  Expanded(
                                    child: Text(
                                      dayLabel(day.date, l10n),
                                      style: theme.textTheme.titleSmall,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: AppTheme.spacingS),
                              Text(
                                Formatters.formatEnergy(day.energyWh),
                                style: theme.textTheme.headlineSmall?.copyWith(
                                  fontWeight: FontWeight.w700,
                                  height: 1,
                                ),
                              ),
                              const SizedBox(height: AppTheme.spacingXS),
                              Text(
                                '${l10n.production} · ${l10n.forecastPeak}: ${Formatters.formatPower(day.peakPowerW)}',
                                style: theme.textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ),
                      ))
                  .toList(),
            );
          },
        ),
      ],
    );
  }

  String dayLabel(DateTime date, AppLocalizations l10n) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final current = DateTime(date.year, date.month, date.day);
    if (current == today) return l10n.today;
    const weekKeys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
    final key = weekKeys[current.weekday - 1];
    final weekLabel = weekLabel0(key, l10n);
    return '$weekLabel ${current.day.toString().padLeft(2, '0')}.${current.month.toString().padLeft(2, '0')}';
  }

  String weekLabel0(String key, AppLocalizations l10n) {
    switch (key) {
      case 'mon':
        return l10n.mon;
      case 'tue':
        return l10n.tue;
      case 'wed':
        return l10n.wed;
      case 'thu':
        return l10n.thu;
      case 'fri':
        return l10n.fri;
      case 'sat':
        return l10n.sat;
      case 'sun':
        return l10n.sun;
      default:
        return '';
    }
  }

  Widget buildTimeSelector(AppLocalizations l10n) {
    final expressive = context.expressive;
    return SegmentedButton<int>(
      showSelectedIcon: false,
      style: ButtonStyle(
        visualDensity: VisualDensity.compact,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        shape: WidgetStatePropertyAll(
          RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(expressive.cornerMedium),
          ),
        ),
      ),
      segments: [
        ButtonSegment<int>(
          value: 0,
          icon: const Icon(Icons.today_rounded, size: 16),
          label: Text(l10n.day),
        ),
        ButtonSegment<int>(
          value: 1,
          icon: const Icon(Icons.view_week_rounded, size: 16),
          label: Text(l10n.week),
        ),
        ButtonSegment<int>(
          value: 2,
          icon: const Icon(Icons.calendar_month_rounded, size: 16),
          label: Text(l10n.month),
        ),
      ],
      selected: {_selectedRange},
      onSelectionChanged: (selected) {
        final next = selected.first;
        if (next == _selectedRange) return;
        setState(() => _selectedRange = next);
        _startRangeLoadingState();
        _scheduleFetchChartData();
      },
    );
  }

  Widget buildDateNavigator() {
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final forecastLimit = today.add(const Duration(days: 16));
    final currentDay =
        DateTime(_currentDate.year, _currentDate.month, _currentDate.day);
    final currentMonth = DateTime(_currentDate.year, _currentDate.month);
    final maxMonth = DateTime(forecastLimit.year, forecastLimit.month);
    final currentWeek = startOfWeek(_currentDate);
    final maxWeek = startOfWeek(forecastLimit);

    final canGoForward = _selectedRange == 0
        ? currentDay.isBefore(forecastLimit)
        : _selectedRange == 1
            ? currentWeek.isBefore(maxWeek)
            : currentMonth.isBefore(maxMonth);

    return AppCard(
      borderRadius: expressive.cornerMedium,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerHigh,
      padding: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppTheme.spacingS,
          vertical: AppTheme.spacingXS,
        ),
        child: Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: AppTheme.spacingS,
          runSpacing: AppTheme.spacingS,
          children: [
            IconButton.filledTonal(
              icon: const Icon(Icons.chevron_left),
              onPressed: () => changeDate(-1),
            ),
            ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 160, maxWidth: 280),
              child: Text(
                getDateText(),
                style: theme.textTheme.titleMedium,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            IconButton.filledTonal(
              icon: const Icon(Icons.chevron_right),
              onPressed: canGoForward ? () => changeDate(1) : null,
            ),
          ],
        ),
      ),
    );
  }

  Widget buildLegend(AppLocalizations l10n) {
    return Wrap(
      spacing: AppTheme.spacingM,
      runSpacing: AppTheme.spacingS,
      children: [
        _buildLegendChip(
          label: l10n.production,
          color: AppTheme.pvColor,
          icon: Icons.wb_sunny_rounded,
          isActive: _showProduction,
          onTap: () => setState(() => _showProduction = !_showProduction),
        ),
        _buildLegendChip(
          label: l10n.consumption,
          color: AppTheme.loadColor,
          icon: Icons.home_rounded,
          isActive: _showConsumption,
          onTap: () => setState(() => _showConsumption = !_showConsumption),
        ),
        _buildLegendChip(
          label: l10n.battery,
          color: AppTheme.batteryColor,
          icon: Icons.battery_charging_full_rounded,
          isActive: _showBattery,
          onTap: () => setState(() => _showBattery = !_showBattery),
        ),
        _buildLegendChip(
          label: l10n.grid,
          color: AppTheme.gridColor,
          icon: Icons.electrical_services_rounded,
          isActive: _showGrid,
          onTap: () => setState(() => _showGrid = !_showGrid),
        ),
        _buildLegendChip(
          label: l10n.forecast,
          color: _forecastSeriesColor,
          icon: Icons.cloud_queue_rounded,
          isActive: _showForecast && _forecastData.isNotEmpty,
          onTap: _forecastData.isNotEmpty
              ? () => setState(() => _showForecast = !_showForecast)
              : null,
        ),
      ],
    );
  }

  Widget _buildLegendChip({
    required String label,
    required Color color,
    required IconData icon,
    required bool isActive,
    required VoidCallback? onTap,
  }) {
    final theme = Theme.of(context);
    return FilterChip(
      selected: isActive,
      onSelected: onTap == null ? null : (_) => onTap(),
      visualDensity: VisualDensity.compact,
      avatar: Icon(
        icon,
        size: 16,
        color: isActive ? color : color.withValues(alpha: 0.54),
      ),
      label: Text(label),
      labelStyle: theme.textTheme.labelMedium?.copyWith(
        color: isActive
            ? theme.colorScheme.onSurface
            : theme.colorScheme.onSurfaceVariant,
      ),
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      selectedColor: theme.colorScheme.secondaryContainer,
      side: BorderSide(
        color: isActive
            ? theme.colorScheme.secondary
            : theme.colorScheme.outlineVariant,
      ),
      showCheckmark: false,
      elevation: 0,
      pressElevation: 0,
    );
  }

  Widget buildBatterySignHint(BuildContext context) {
    final theme = Theme.of(context);
    return AppCard(
        borderRadius: context.expressive.cornerMedium,
        enableBlur: false,
        backgroundColor: theme.colorScheme.surfaceContainerHigh,
        padding: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppTheme.spacingM,
            vertical: AppTheme.spacingS,
          ),
          child: Row(
            children: [
              Icon(
                Icons.battery_charging_full_rounded,
                size: 16,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Text(
                  AppLocalizations.of(context)!.batterySignHint,
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ));
  }

  Widget buildChart(BuildContext context) {
    final minX = getMinX();
    final maxX = getMaxX();
    final futureStartX = futureStartX0();

    final visibleSpots = getVisibleSpots();
    final minY = getMinY(visibleSpots);
    final maxY = getMaxY(visibleSpots);
    final renderSignature =
        '$_selectedRange|${minX.toStringAsFixed(2)}|${maxX.toStringAsFixed(2)}|${minY.toStringAsFixed(1)}|${maxY.toStringAsFixed(1)}|${visibleSpots.length}';
    if (_lastRenderLogSignature != renderSignature) {
      _lastRenderLogSignature = renderSignature;
      LogService.log(
          '[chart] render: range=$_selectedRange, x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)}, y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}, visible=${visibleSpots.length}');
    }

    if (_selectedRange != 0) {
      return buildBarChart(context, minX, maxX, minY, maxY, futureStartX);
    }

    var lines = <LineChartBarData>[];

    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(buildLineData(
        _productionData,
        AppTheme.pvColor,
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(buildLineData(
        _consumptionData,
        AppTheme.loadColor,
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(buildLineData(
        _batteryData,
        AppTheme.batteryColor,
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(buildLineData(
        _gridData,
        AppTheme.gridColor,
        isCurved: _selectedRange == 0,
      ));
    }

    if (_showForecast && _forecastData.isNotEmpty && _selectedRange == 0) {
      lines.add(LineChartBarData(
        spots: normalizeSpots(_forecastData),
        isCurved: true,
        curveSmoothness: 0.18,
        preventCurveOverShooting: true,
        preventCurveOvershootingThreshold: 8,
        color: _forecastSeriesColor.withValues(alpha: 0.8),
        barWidth: 2,
        dotData: const FlDotData(show: false),
        dashArray: [5, 5],
        belowBarData: BarAreaData(show: false),
      ));
    }

    if (lines.isEmpty) {
      return AppEmptyState(
        title: AppLocalizations.of(context)!.chartNoDataTitle,
        message: AppLocalizations.of(context)!.chartNoDataMessage,
        icon: Icons.show_chart_rounded,
      );
    }

    return LineChart(
      LineChartData(
        minX: minX,
        maxX: maxX,
        minY: minY,
        maxY: maxY,
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (spot) =>
                Theme.of(context).colorScheme.surfaceContainerHighest,
            getTooltipItems: (spots) => spots
                .map((spot) => LineTooltipItem(
                      _selectedRange == 0
                          ? Formatters.formatPower(spot.y)
                          : Formatters.formatEnergy(spot.y),
                      TextStyle(
                        color: spot.bar.color,
                        fontWeight: FontWeight.bold,
                      ),
                    ))
                .toList(),
          ),
        ),
        titlesData: FlTitlesData(
          show: true,
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 52,
              interval: _computeLeftInterval(minY, maxY),
              getTitlesWidget: (value, meta) {
                if (value == meta.min || value == meta.max) {
                  return const SizedBox.shrink();
                }
                return SideTitleWidget(
                  meta: meta,
                  child: Text(
                    _selectedRange == 0
                        ? Formatters.formatAxisPower(value)
                        : Formatters.formatAxisEnergy(value),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              interval: _selectedRange == 0
                  ? 4
                  : _selectedRange == 1
                      ? 1
                      : ((maxX - minX) > 20 ? 5 : 2),
              getTitlesWidget: (value, meta) => SideTitleWidget(
                meta: meta,
                space: 10,
                child: Text(
                  formatBottomAxisLabel(value, AppLocalizations.of(context)!),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ),
          ),
        ),
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          getDrawingHorizontalLine: (value) => FlLine(
            color: Theme.of(context).dividerColor.withValues(alpha: 0.18),
            strokeWidth: 0.8,
          ),
        ),
        rangeAnnotations: futureStartX == null
            ? const RangeAnnotations()
            : RangeAnnotations(
                verticalRangeAnnotations: [
                  VerticalRangeAnnotation(
                    x1: futureStartX,
                    x2: maxX,
                    color: Theme.of(context)
                        .colorScheme
                        .primary
                        .withValues(alpha: 0.05),
                  ),
                ],
              ),
        borderData: FlBorderData(show: false),
        lineBarsData: lines,
      ),
    );
  }

  Widget buildBarChart(
    BuildContext context,
    double minX,
    double maxX,
    double minY,
    double maxY,
    double? futureStartX,
  ) {
    final series = buildBarSeries(AppLocalizations.of(context)!);
    if (series.isEmpty) {
      return AppEmptyState(
        title: AppLocalizations.of(context)!.chartNoDataTitle,
        message: AppLocalizations.of(context)!.chartNoDataMessage,
        icon: Icons.bar_chart_rounded,
      );
    }

    final groups = <BarChartGroupData>[];
    final start = minX.round();
    final end = maxX.round();

    for (var x = start; x <= end; x++) {
      final rods = <BarChartRodData>[];
      for (final s in series) {
        final value = s.points[x] ?? 0.0;
        rods.add(
          BarChartRodData(
            toY: value,
            color: s.color,
            width: (20 / series.length).clamp(3, 8).toDouble(),
            borderRadius: BorderRadius.circular(2),
          ),
        );
      }
      groups.add(
        BarChartGroupData(
          x: x,
          barsSpace: 4,
          barRods: rods,
        ),
      );
    }

    return BarChart(
      BarChartData(
        minY: minY,
        maxY: maxY,
        baselineY: 0,
        barGroups: groups,
        groupsSpace: 8,
        titlesData: FlTitlesData(
          show: true,
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 52,
              interval: _computeLeftInterval(minY, maxY),
              getTitlesWidget: (value, meta) {
                if (value == meta.min || value == meta.max) {
                  return const SizedBox.shrink();
                }
                return SideTitleWidget(
                  meta: meta,
                  child: Text(
                    Formatters.formatAxisEnergy(value),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              interval: _selectedRange == 1 ? 1 : ((maxX - minX) > 20 ? 5 : 2),
              getTitlesWidget: (value, meta) => SideTitleWidget(
                meta: meta,
                space: 10,
                child: Text(
                  formatBottomAxisLabel(value, AppLocalizations.of(context)!),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ),
          ),
        ),
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          getDrawingHorizontalLine: (value) => FlLine(
            color: Theme.of(context).dividerColor.withValues(alpha: 0.18),
            strokeWidth: 0.8,
          ),
        ),
        rangeAnnotations: futureStartX == null
            ? const RangeAnnotations()
            : RangeAnnotations(
                verticalRangeAnnotations: [
                  VerticalRangeAnnotation(
                    x1: futureStartX,
                    x2: maxX,
                    color: Theme.of(context)
                        .colorScheme
                        .primary
                        .withValues(alpha: 0.05),
                  ),
                ],
              ),
        borderData: FlBorderData(show: false),
        barTouchData: BarTouchData(
          enabled: true,
          touchTooltipData: BarTouchTooltipData(
            getTooltipColor: (_) =>
                Theme.of(context).colorScheme.surfaceContainerHighest,
            getTooltipItem: (group, groupIndex, rod, rodIndex) {
              final name = series[rodIndex].name;
              return BarTooltipItem(
                '$name\n${Formatters.formatEnergy(rod.toY)}',
                TextStyle(
                  color: rod.color,
                  fontWeight: FontWeight.bold,
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  List<_BarSeriesConfig> buildBarSeries(AppLocalizations l10n) {
    final result = <_BarSeriesConfig>[];

    Map<int, double> toMap(List<FlSpot> spots) {
      final map = <int, double>{};
      for (final spot in spots) {
        map[spot.x.round()] = spot.y;
      }
      return map;
    }

    if (_showProduction && _productionData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.production,
        color: AppTheme.pvColor,
        points: toMap(_productionData),
      ));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.consumption,
        color: AppTheme.loadColor,
        points: toMap(_consumptionData),
      ));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.battery,
        color: AppTheme.batteryColor,
        points: toMap(_batteryData),
      ));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.grid,
        color: AppTheme.gridColor,
        points: toMap(_gridData),
      ));
    }
    if (_showForecast && _forecastData.isNotEmpty && _selectedRange != 0) {
      result.add(_BarSeriesConfig(
        name: l10n.forecast,
        color: _forecastSeriesColor.withValues(alpha: 0.75),
        points: toMap(_forecastData),
      ));
    }

    return result;
  }

  LineChartBarData buildLineData(
    List<FlSpot> spots,
    Color color, {
    required bool isCurved,
  }) {
    final normalized = normalizeSpots(spots);
    final useCurved = isCurved && normalized.length >= 4;
    return LineChartBarData(
      spots: normalized,
      isCurved: useCurved,
      curveSmoothness: useCurved ? 0.24 : 0.0,
      preventCurveOverShooting: true,
      preventCurveOvershootingThreshold: 8,
      color: color,
      barWidth: 3,
      dotData: const FlDotData(show: false),
      belowBarData: BarAreaData(
        show: true,
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            color.withValues(alpha: 0.08),
            color.withValues(alpha: 0.0),
          ],
        ),
      ),
    );
  }

  double getMaxY([List<FlSpot>? spots]) {
    spots ??= getVisibleSpots();
    if (spots.isEmpty) return 10.0;
    var max = spots.map((e) => e.y).reduce((a, b) => a > b ? a : b);
    return max == 0 ? 10.0 : max * 1.15;
  }

  double getMaxX() {
    if (_selectedRange == 0) return 23;
    if (_selectedRange == 1) return 6;

    final lastDay = DateTime(_currentDate.year, _currentDate.month + 1, 0).day;
    return lastDay.toDouble();
  }

  double getMinX() {
    if (_selectedRange == 0 || _selectedRange == 1) return 0;
    return 1;
  }

  String formatBottomAxisLabel(double value, AppLocalizations l10n) {
    final rounded = value.round();
    if ((value - rounded).abs() > 0.01) return '';

    if (_selectedRange == 0) {
      return '$rounded:00';
    }

    if (_selectedRange == 1) {
      const weekKeys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
      if (rounded < 0 || rounded >= weekKeys.length) return '';
      switch (weekKeys[rounded]) {
        case 'mon':
          return l10n.mon;
        case 'tue':
          return l10n.tue;
        case 'wed':
          return l10n.wed;
        case 'thu':
          return l10n.thu;
        case 'fri':
          return l10n.fri;
        case 'sat':
          return l10n.sat;
        case 'sun':
          return l10n.sun;
      }
    }

    final lastDay = DateTime(_currentDate.year, _currentDate.month + 1, 0).day;
    if (rounded < 1 || rounded > lastDay) return '';
    final day = rounded.toString().padLeft(2, '0');
    final month = _currentDate.month.toString().padLeft(2, '0');
    return '$day.$month';
  }

  double getMinY([List<FlSpot>? spots]) {
    spots ??= getVisibleSpots();
    var minVal = 0.0;
    for (var spot in spots) {
      if (spot.y < minVal) minVal = spot.y;
    }
    return minVal >= 0 ? 0 : minVal * 1.2;
  }

  /// Compute a Y-axis interval that produces ~5 ticks max to prevent label overlap.
  double _computeLeftInterval(double minY, double maxY) {
    final range = maxY - minY;
    if (range <= 0) return 1;
    final rawInterval = range / 5;
    final niceSteps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
    for (final step in niceSteps) {
      if (rawInterval <= step) return step.toDouble();
    }
    return (rawInterval / 1000).ceil() * 1000.0;
  }

  List<FlSpot> getVisibleSpots() {
    var all = <FlSpot>[];
    if (_showProduction) all.addAll(_productionData);
    if (_showConsumption) all.addAll(_consumptionData);
    if (_showBattery) all.addAll(_batteryData);
    if (_showGrid) all.addAll(_gridData);
    if (_showForecast) all.addAll(_forecastData);
    return all;
  }

  void logChartUiSummary(String prefix) {
    String fmt(List<FlSpot> spots) {
      if (spots.isEmpty) return 'count=0';
      final minX = spots.map((e) => e.x).reduce((a, b) => a < b ? a : b);
      final maxX = spots.map((e) => e.x).reduce((a, b) => a > b ? a : b);
      final minY = spots.map((e) => e.y).reduce((a, b) => a < b ? a : b);
      final maxY = spots.map((e) => e.y).reduce((a, b) => a > b ? a : b);
      return 'count=${spots.length},x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)},y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}';
    }

    String quality(List<FlSpot> spots) {
      if (spots.isEmpty) return 'missing';
      if (spots.any((s) => !s.x.isFinite || !s.y.isFinite)) return 'invalid';

      final uniqueX = spots.map((e) => e.x.toStringAsFixed(3)).toSet().length;
      if (uniqueX < 2) return 'poor';

      final minX = spots.map((e) => e.x).reduce((a, b) => a < b ? a : b);
      final maxX = spots.map((e) => e.x).reduce((a, b) => a > b ? a : b);
      final axisMin = getMinX();
      final axisMax = getMaxX();
      final span = (axisMax - axisMin).abs();
      final covered = (maxX - minX).abs();
      final coverage = span > 0 ? (covered / span).clamp(0.0, 1.0) : 1.0;

      if (coverage < 0.25) return 'low';
      if (coverage < 0.6) return 'partial';
      return 'good';
    }

    LogService.log(
        '[chart] $prefix | range=$_selectedRange pv[q=${quality(_productionData)}](${fmt(_productionData)}) load[q=${quality(_consumptionData)}](${fmt(_consumptionData)}) battery[q=${quality(_batteryData)}](${fmt(_batteryData)}) grid[q=${quality(_gridData)}](${fmt(_gridData)}) forecast[q=${quality(_forecastData)}](${fmt(_forecastData)})');
  }
}

class _ChartRefreshBadge extends StatefulWidget {
  final DateTime refreshedAt;
  final bool isRefreshing;

  const _ChartRefreshBadge({
    required this.refreshedAt,
    required this.isRefreshing,
  });

  @override
  State<_ChartRefreshBadge> createState() => _ChartRefreshBadgeState();
}

class _ChartSkeleton extends StatelessWidget {
  const _ChartSkeleton();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final barColor = theme.colorScheme.surfaceContainerHighest;
    return Padding(
      padding: const EdgeInsets.all(AppTheme.spacingM),
      child: Column(
        children: [
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                for (final h in [0.25, 0.48, 0.36, 0.62, 0.44, 0.7, 0.52, 0.6])
                  Expanded(
                    child: Align(
                      alignment: Alignment.bottomCenter,
                      child: FractionallySizedBox(
                        widthFactor: 0.62,
                        heightFactor: h,
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: barColor,
                            borderRadius: BorderRadius.circular(6),
                          ),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: AppTheme.spacingM),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: theme.colorScheme.primary,
                ),
              ),
              const SizedBox(width: AppTheme.spacingS),
              Text(
                AppLocalizations.of(context)!.waitingInverterResponse,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ChartRefreshBadgeState extends State<_ChartRefreshBadge> {
  Timer? ticker;

  @override
  void initState() {
    super.initState();
    // Tick every 30s to keep the "minutes ago" label fresh.
    ticker = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    ticker?.cancel();
    super.dispose();
  }

  String label() {
    final l10n = AppLocalizations.of(context)!;
    if (widget.isRefreshing) return '...';
    final diff = DateTime.now().difference(widget.refreshedAt);
    if (diff.inSeconds < 60) return l10n.lessThanMinute;
    if (diff.inMinutes < 60) return l10n.minutesAgo(diff.inMinutes.toString());
    return l10n.hoursAgo(diff.inHours.toString());
  }

  @override
  Widget build(BuildContext context) {
    final isStale = !widget.isRefreshing &&
        DateTime.now().difference(widget.refreshedAt) >
            const Duration(minutes: 6);
    final color = isStale
        ? Theme.of(context).colorScheme.error
        : Theme.of(context).hintColor;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.isRefreshing)
          SizedBox(
            width: 10,
            height: 10,
            child: CircularProgressIndicator(
              strokeWidth: 1.5,
              color: color,
            ),
          )
        else
          Icon(
            isStale ? Icons.sync_problem_rounded : Icons.sync_rounded,
            size: 12,
            color: color,
          ),
        const SizedBox(width: AppTheme.spacingXS),
        Text(
          label(),
          style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color),
        ),
      ],
    );
  }
}

class _BarSeriesConfig {
  final String name;
  final Color color;
  final Map<int, double> points;

  const _BarSeriesConfig({
    required this.name,
    required this.color,
    required this.points,
  });
}

// ======================== SOC HISTORY CHART ========================

class _BatterySocHistoryCard extends StatelessWidget {
  final AppStateProvider provider;

  const _BatterySocHistoryCard({required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final samples = provider.socHistory.samples;

    return AppCard(
      borderRadius: expressive.cornerLarge,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSectionTitle(
              title: l10n.socHistoryTitle,
              subtitle: l10n.socHistorySubtitle,
              icon: Icons.battery_charging_full_rounded,
            ),
            const SizedBox(height: AppTheme.spacingM),
            if (samples.length < 2)
              _SocHistoryEmpty(message: l10n.socHistoryNoData)
            else
              _SocHistoryChart(
                samples: samples,
                reserveSoc: provider.hemsService
                    .buildDiagnosticsSnapshot()
                    .adaptiveReserveSoc,
                l10n: l10n,
              ),
          ],
        ),
      ),
    );
  }
}

class _SocHistoryEmpty extends StatelessWidget {
  final String message;
  const _SocHistoryEmpty({required this.message});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      height: 120,
      child: Center(
        child: Text(
          message,
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}

class _SocHistoryChart extends StatefulWidget {
  final List<SocSample> samples;
  final double reserveSoc;
  final AppLocalizations l10n;

  const _SocHistoryChart({
    required this.samples,
    required this.reserveSoc,
    required this.l10n,
  });

  @override
  State<_SocHistoryChart> createState() => _SocHistoryChartState();
}

class _SocHistoryChartState extends State<_SocHistoryChart> {
  int? _touchedIndex;

  List<FlSpot> _buildSpots() {
    if (widget.samples.isEmpty) return const [];
    final oldest = widget.samples.first.timestamp;
    return widget.samples
        .map((s) =>
            FlSpot(s.timestamp.difference(oldest).inMinutes.toDouble(), s.soc))
        .toList();
  }

  Color _spotColor(double soc) {
    if (soc >= 70) return AppTheme.batteryColor;
    if (soc >= 40) return AppTheme.pvColor;
    return const Color(0xFFEF4444);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final spots = _buildSpots();
    if (spots.isEmpty) return const SizedBox.shrink();

    final oldest = widget.samples.first.timestamp;
    final newest = widget.samples.last.timestamp;
    final totalMinutes =
        newest.difference(oldest).inMinutes.toDouble().clamp(1.0, 1440.0);

    // Determine a reasonable interval for X-axis labels (every 2h or 4h)
    final xInterval = (totalMinutes / 6).ceilToDouble().clamp(30.0, 240.0);

    final currentSoc = widget.samples.last.soc;
    final currentBatPower = widget.samples.last.batteryPower;
    final stateLabel = currentBatPower > 20
        ? widget.l10n.socHistoryChargingLabel
        : currentBatPower < -20
            ? widget.l10n.socHistoryDischargingLabel
            : widget.l10n.socHistoryIdleLabel;
    final stateColor = currentBatPower > 20
        ? AppTheme.batteryColor
        : currentBatPower < -20
            ? const Color(0xFFF97316)
            : theme.colorScheme.onSurfaceVariant;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Status strip
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTheme.spacingM, vertical: 4),
              decoration: BoxDecoration(
                color: _spotColor(currentSoc).withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              ),
              child: Text(
                '${currentSoc.toStringAsFixed(0)}%',
                style: theme.textTheme.titleMedium?.copyWith(
                  color: _spotColor(currentSoc),
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            const SizedBox(width: AppTheme.spacingS),
            Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTheme.spacingM, vertical: 4),
              decoration: BoxDecoration(
                color: stateColor.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              ),
              child: Text(
                stateLabel,
                style: theme.textTheme.labelMedium?.copyWith(
                  color: stateColor,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            const Spacer(),
            Text(
              '${widget.samples.length} pts',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppTheme.spacingM),
        SizedBox(
          height: 160,
          child: LineChart(
            LineChartData(
              minY: 0,
              maxY: 100,
              minX: 0,
              maxX: totalMinutes,
              clipData: const FlClipData.all(),
              lineTouchData: LineTouchData(
                enabled: true,
                touchCallback: (evt, resp) {
                  setState(() {
                    _touchedIndex = resp?.lineBarSpots?.firstOrNull?.spotIndex;
                  });
                },
                touchTooltipData: LineTouchTooltipData(
                  getTooltipColor: (_) =>
                      theme.colorScheme.surfaceContainerHighest,
                  getTooltipItems: (spots) => spots.map((s) {
                    final idx = s.spotIndex;
                    final sample = widget.samples[idx];
                    final hh = sample.timestamp.hour.toString().padLeft(2, '0');
                    final mm =
                        sample.timestamp.minute.toString().padLeft(2, '0');
                    return LineTooltipItem(
                      '$hh:$mm\n${s.y.toStringAsFixed(0)}%',
                      (theme.textTheme.labelMedium ?? const TextStyle())
                          .copyWith(
                        color: _spotColor(s.y),
                        fontWeight: FontWeight.w700,
                      ),
                    );
                  }).toList(),
                ),
              ),
              lineBarsData: [
                // Main SOC line
                LineChartBarData(
                  spots: spots,
                  isCurved: true,
                  curveSmoothness: 0.25,
                  color: AppTheme.batteryColor,
                  barWidth: 2.5,
                  isStrokeCapRound: true,
                  dotData: FlDotData(
                    show: true,
                    checkToShowDot: (s, bar) =>
                        _touchedIndex == spots.indexOf(s) || s == spots.last,
                    getDotPainter: (s, _, __, ___) => FlDotCirclePainter(
                      radius: s == spots.last ? 4 : 3,
                      color: _spotColor(s.y),
                      strokeWidth: 1.5,
                      strokeColor: theme.colorScheme.surface,
                    ),
                  ),
                  belowBarData: BarAreaData(
                    show: true,
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [
                        AppTheme.batteryColor.withValues(alpha: 0.28),
                        AppTheme.batteryColor.withValues(alpha: 0.04),
                      ],
                    ),
                  ),
                ),
              ],
              // Reserve threshold dashed line
              extraLinesData: ExtraLinesData(
                horizontalLines: [
                  HorizontalLine(
                    y: widget.reserveSoc,
                    color: const Color(0xFFEF4444).withValues(alpha: 0.55),
                    strokeWidth: 1.2,
                    dashArray: [6, 4],
                    label: HorizontalLineLabel(
                      show: true,
                      alignment: Alignment.topRight,
                      padding: const EdgeInsets.only(right: 6, bottom: 2),
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: const Color(0xFFEF4444),
                        fontWeight: FontWeight.w600,
                      ),
                      labelResolver: (_) => widget.l10n.socHistoryReserveLabel,
                    ),
                  ),
                ],
              ),
              gridData: FlGridData(
                show: true,
                drawVerticalLine: false,
                horizontalInterval: 20,
                getDrawingHorizontalLine: (v) => FlLine(
                  color:
                      theme.colorScheme.outlineVariant.withValues(alpha: 0.4),
                  strokeWidth: 0.8,
                ),
              ),
              borderData: FlBorderData(show: false),
              titlesData: FlTitlesData(
                topTitles:
                    const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                rightTitles:
                    const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                leftTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    interval: 20,
                    reservedSize: 30,
                    getTitlesWidget: (v, _) => Text(
                      '${v.toInt()}%',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                        fontSize: 10,
                      ),
                    ),
                  ),
                ),
                bottomTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    interval: xInterval,
                    reservedSize: 22,
                    getTitlesWidget: (v, _) {
                      final dt = oldest.add(Duration(minutes: v.toInt()));
                      final hh = dt.hour.toString().padLeft(2, '0');
                      final mm = dt.minute.toString().padLeft(2, '0');
                      return Text(
                        '$hh:$mm',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                          fontSize: 10,
                        ),
                      );
                    },
                  ),
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: AppTheme.spacingS),
        // Legend row
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _SocLegendDot(
              color: AppTheme.batteryColor,
              label: '≥70%',
            ),
            const SizedBox(width: AppTheme.spacingM),
            _SocLegendDot(
              color: AppTheme.pvColor,
              label: '40–69%',
            ),
            const SizedBox(width: AppTheme.spacingM),
            _SocLegendDot(
              color: const Color(0xFFEF4444),
              label: '<40%',
            ),
            const SizedBox(width: AppTheme.spacingL),
            Container(
              width: 18,
              height: 2,
              decoration: const BoxDecoration(
                color: Color(0xFFEF4444),
              ),
            ),
            const SizedBox(width: 4),
            Text(
              widget.l10n.socHistoryReserveLabel,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ],
        ),
      ],
    );
  }
}

class _SocLegendDot extends StatelessWidget {
  final Color color;
  final String label;
  const _SocLegendDot({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 4),
        Text(
          label,
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
        ),
      ],
    );
  }
}

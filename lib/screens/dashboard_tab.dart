import 'dart:async';

import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../services/log_service.dart';
import '../services/weather_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';
import '../widgets/energy_flow.dart';
import '../utils/formatters.dart';

class DashboardTab extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const DashboardTab({super.key, required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final statusMessage = provider.statusMessage.trim();
    final lastUpdatedAt = provider.lastSuccessfulRealtimeAt;
    final lastUpdatedLabel = lastUpdatedAt == null
        ? null
        : '${lastUpdatedAt.hour.toString().padLeft(2, '0')}:${lastUpdatedAt.minute.toString().padLeft(2, '0')}';
    final sections = <Widget>[
      if (statusMessage.isNotEmpty)
        AppStatusBanner(
          message: statusMessage,
          icon: Icons.info_outline,
          meta: lastUpdatedLabel == null
              ? null
              : '${l10n.lastRealtimeUpdate}: $lastUpdatedLabel',
        ),
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
    ];

    return RefreshIndicator(
      color: Theme.of(context).colorScheme.primary,
      onRefresh: provider.fetchData,
      child: ListView.separated(
        scrollCacheExtent: ScrollCacheExtent.pixels(1200), physics: const AlwaysScrollableScrollPhysics(
          parent: ClampingScrollPhysics(),
        ),
        padding: const EdgeInsets.all(AppTheme.spacingL),
        itemBuilder: (context, index) => sections[index],
        separatorBuilder: (_, __) => const SizedBox(height: AppTheme.spacingL),
        itemCount: sections.length,
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
        provider.monthEconomicsUsesEstimatedFallback ? 'Ã¢â€°Ë†' : '';
    const savingsPrefix = 'Ã¢â€°Ë†';
    const projectionPrefix = 'Ã¢â€°Ë†';
    return AppCard(
      borderRadius: expressive.cornerXL,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: LayoutBuilder(
          builder: (context, constraints) {
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

            final flowPanel = AppCard(
              borderRadius: expressive.cornerLarge,
              padding: const EdgeInsets.all(AppTheme.spacingM),
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
                          style: theme.textTheme.titleSmall?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: AppTheme.spacingS),
                  Text(
                    provider.lastSuccessfulRealtimeAt == null
                        ? l10n.lastRealtimeUpdate
                        : '${l10n.lastRealtimeUpdate}: ${provider.lastSuccessfulRealtimeAt!.hour.toString().padLeft(2, '0')}:${provider.lastSuccessfulRealtimeAt!.minute.toString().padLeft(2, '0')}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: AppTheme.spacingM),
                  RepaintBoundary(
                    child:
                        ExcludeSemantics(child: EnergyFlowDiagram(data: data)),
                  ),
                ],
              ),
            );

            if (compact) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  summary,
                  const SizedBox(height: AppTheme.spacingL),
                  flowPanel
                ],
              );
            }

            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(flex: 6, child: summary),
                const SizedBox(width: AppTheme.spacingL),
                Expanded(flex: 5, child: flowPanel),
              ],
            );
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
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final card = AppCard(
      borderRadius: expressive.cornerLarge,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(expressive.cornerMedium),
            ),
            child: Icon(icon, color: color),
          ),
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
              Text(
                value,
                style: theme.textTheme.headlineMedium?.copyWith(
                  fontWeight: FontWeight.w700,
                  height: 1,
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
    final theme = Theme.of(context);
    final expressive = context.expressive;
    final card = AppCard(
      borderRadius: expressive.cornerMedium,
      enableBlur: false,
      backgroundColor: theme.colorScheme.surfaceContainerLow,
      padding: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingM),
        child: Row(
          children: [
            Container(
              width: 36,
              height: 36,
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(expressive.cornerSmall),
              ),
              child: Icon(icon, size: 18, color: color),
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
                  Text(
                    '$value $unit',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
    return tooltip == null ? card : Tooltip(message: tooltip!, child: card);
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
        provider.monthEconomicsUsesEstimatedFallback ? 'Ã¢â€°Ë†' : '';
    const savingsPrefix = 'Ã¢â€°Ë†';
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
        'Ã°Å¸Â§Â¹ chart.ui reset before fetch: range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}');
  }

  Future<void> _fetchChartData({bool background = false}) async {
    final requestId = ++_chartRequestSeq;
    LogService.log(
        'Ã°Å¸â€œÅ  chart.ui fetch start: requestId=$requestId, range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}, bg=$background');
    if (!background) {
      if (mounted && !_isLoading) setState(() => _isLoading = true);
    } else {
      if (mounted) setState(() => _isBackgroundRefreshing = true);
    }

    final data = await widget.provider.service
        .getChartData(_selectedRange, _currentDate);

    var forecast = <String, double>{};
    var dailyForecast = _multiDayForecast;
    if (_selectedRange == 0) {
      forecast = await widget.provider.weatherService.fetchLocalForecast(
        pvCapacityW: widget.provider.pvTotalCapacityW,
        efficiency: 0.85,
        historicalPvData: widget.provider.historicalPvData,
        targetDate: _currentDate,
      );
    }

    dailyForecast = await widget.provider.weatherService.fetchDailyForecast(
      pvCapacityW: widget.provider.pvTotalCapacityW,
      efficiency: 0.85,
      historicalPvData: widget.provider.historicalPvData,
      daysAhead: 16,
    );

    if (!mounted || requestId != _chartRequestSeq) {
      LogService.log(
          'Ã¢ÂÂ­Ã¯Â¸Â chart.ui stale response ignored: requestId=$requestId, active=$_chartRequestSeq');
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
                          ? const Center(child: CircularProgressIndicator())
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
                                '${l10n.production} Ã‚Â· ${l10n.forecastPeak}: ${Formatters.formatPower(day.peakPowerW)}',
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
          'Ã°Å¸â€“Â¼Ã¯Â¸Â chart.ui render: range=$_selectedRange, x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)}, y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}, visible=${visibleSpots.length}');
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
                  formatBottomAxisLabel(
                    value,
                    AppLocalizations.of(context)!,
                  ),
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
        'Ã°Å¸â€œË† $prefix | range=$_selectedRange pv[q=${quality(_productionData)}](${fmt(_productionData)}) load[q=${quality(_consumptionData)}](${fmt(_consumptionData)}) battery[q=${quality(_batteryData)}](${fmt(_batteryData)}) grid[q=${quality(_gridData)}](${fmt(_gridData)}) forecast[q=${quality(_forecastData)}](${fmt(_forecastData)})');
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

class _ChartRefreshBadgeState extends State<_ChartRefreshBadge> {
  Timer? ticker;

  @override
  void initState() {
    super.initState();
    // Tick every 30 s to keep "X Ã‘â€¦ÃÂ² Ã‘â€šÃÂ¾ÃÂ¼Ã‘Æ’" label fresh
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
    if (widget.isRefreshing) return 'Ã¢â‚¬Â¦';
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


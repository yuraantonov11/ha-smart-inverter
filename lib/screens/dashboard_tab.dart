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

    return RefreshIndicator(
      color: Theme.of(context).colorScheme.primary,
      onRefresh: provider.fetchData,
      child: ListView(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        children: [
          if (statusMessage.isNotEmpty) ...[
            AppStatusBanner(
              message: statusMessage,
              icon: Icons.info_outline,
              meta: lastUpdatedLabel == null
                  ? null
                  : '${l10n.lastRealtimeUpdate}: $lastUpdatedLabel',
            ),
            const SizedBox(height: AppTheme.spacingL),
          ],

          // Energy Flow Diagram
          AppGlassSurface(
            isStrong: true,
            borderRadius: 26,
            child: ExcludeSemantics(
              child: EnergyFlowDiagram(data: data),
            ),
          ),

          const SizedBox(height: AppTheme.spacingL),

          // Stats Row
          _StatsSection(provider: provider),

          const SizedBox(height: AppTheme.spacingL),

          LayoutBuilder(
            builder: (context, constraints) {
              final wide = constraints.maxWidth >= 1180;
              if (!wide) {
                return Column(
                  children: [
                    _SystemCapacitySection(provider: provider, data: data),
                    const SizedBox(height: AppTheme.spacingL),
                    _EnergyChartSection(provider: provider),
                  ],
                );
              }

              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    flex: 4,
                    child:
                        _SystemCapacitySection(provider: provider, data: data),
                  ),
                  const SizedBox(width: AppTheme.spacingL),
                  Expanded(
                    flex: 6,
                    child: _EnergyChartSection(provider: provider),
                  ),
                ],
              );
            },
          ),

          const SizedBox(height: AppTheme.spacingL),
        ],
      ),
    );
  }
}

// ======================== STATS SECTION ========================

class _StatsSection extends StatelessWidget {
  final AppStateProvider provider;

  const _StatsSection({required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
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
    final paymentPrefix =
        provider.monthEconomicsUsesEstimatedFallback ? '≈' : '';
    const savingsPrefix = '≈';
    const projectionPrefix = '≈';
    final cards = [
      AppStatCard(
        label: l10n.today,
        value: daily,
        unit: 'kWh',
        icon: Icons.today_rounded,
        color: AppTheme.gridColor,
        tooltip: l10n.tooltipTodayEnergy,
      ),
      AppStatCard(
        label: l10n.total,
        value: total,
        unit: 'kWh',
        icon: Icons.assessment_rounded,
        color: AppTheme.pvColor,
        tooltip: l10n.tooltipTotalEnergy,
      ),
      AppStatCard(
        label: 'CO2',
        value: co2,
        unit: 'kg',
        icon: Icons.eco_rounded,
        color: AppTheme.batteryColor,
        tooltip: l10n.tooltipCo2,
      ),
      AppStatCard(
        label: l10n.moneySavedMonth,
        value: savedMoney == null || savedMoney == 0.0
            ? '0.0'
            : '$savingsPrefix${savedMoney.toStringAsFixed(1)}',
        unit: l10n.currencyUah,
        icon: Icons.savings_rounded,
        color: AppTheme.batteryColor,
        tooltip: savedTooltip,
      ),
      AppStatCard(
        label: l10n.paymentThisMonth,
        value: monthToPay == null || monthToPay == 0.0
            ? '0.0'
            : '$paymentPrefix${monthToPay.toStringAsFixed(1)}',
        unit: l10n.currencyUah,
        icon: Icons.receipt_long_rounded,
        color: AppTheme.pvColor,
        tooltip: paymentTooltip,
      ),
      AppStatCard(
        label: l10n.projectedSavedMonth,
        value: projectedSavedMoney == null || projectedSavedMoney == 0.0
            ? '0.0'
            : '$projectionPrefix${projectedSavedMoney.toStringAsFixed(1)}',
        unit: l10n.currencyUah,
        icon: Icons.trending_up_rounded,
        color: AppTheme.batteryColor,
        tooltip: l10n.tooltipProjectedSavedMonth,
      ),
      AppStatCard(
        label: l10n.projectedPaymentMonth,
        value: projectedMonthToPay == null || projectedMonthToPay == 0.0
            ? '0.0'
            : '$projectionPrefix${projectedMonthToPay.toStringAsFixed(1)}',
        unit: l10n.currencyUah,
        icon: Icons.calendar_month_rounded,
        color: AppTheme.pvColor,
        tooltip: l10n.tooltipProjectedPaymentMonth,
      ),
    ];

    return Column(
      children: [
        AppSectionTitle(
          title: l10n.energyOverview,
          icon: Icons.trending_up_rounded,
        ),
        LayoutBuilder(
          builder: (context, constraints) {
            final width = constraints.maxWidth;
            if (width < 640) {
              return SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    for (var i = 0; i < cards.length; i++) ...[
                      SizedBox(width: 210, child: cards[i]),
                      if (i != cards.length - 1)
                        const SizedBox(width: AppTheme.spacingM),
                    ],
                  ],
                ),
              );
            }
            final columns = width >= 1500
                ? 4
                : width >= 1080
                    ? 3
                    : 2;
            final cardWidth =
                (width - (AppTheme.spacingL * (columns - 1))) / columns;

            return Wrap(
              spacing: AppTheme.spacingL,
              runSpacing: AppTheme.spacingL,
              children: [
                for (final card in cards)
                  SizedBox(
                    width: cardWidth,
                    child: card,
                  ),
              ],
            );
          },
        ),
        const SizedBox(height: AppTheme.spacingL),
        _MonthEconomicsBreakdown(provider: provider),
      ],
    );
  }
}

class _MonthEconomicsBreakdown extends StatelessWidget {
  final AppStateProvider provider;

  const _MonthEconomicsBreakdown({required this.provider});

  @override
  Widget build(BuildContext context) {
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
        provider.monthEconomicsUsesEstimatedFallback ? '≈' : '';
    const savingsPrefix = '≈';
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
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.pie_chart_rounded,
                  color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      l10n.monthlyEnergyBreakdown,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    Text(
                      '${DateTime.now().month.toString().padLeft(2, '0')}.${DateTime.now().year}',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context).hintColor,
                          ),
                    ),
                  ],
                ),
              ),
              Tooltip(
                message: l10n.tooltipMonthProgress,
                child: Text(
                  '${provider.monthProgressPercent}%',
                  style: Theme.of(context).textTheme.labelMedium,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Text(
              '${l10n.monthLoadEnergy} = ${l10n.monthGridImport} + ${l10n.monthSelfConsumed}',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).hintColor,
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
            _MonthEconomicsMiniChart(data: dailyEconomics),
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
              getTooltipColor: (_) => Theme.of(context).cardColor,
              getTooltipItem: (group, groupIndex, rod, rodIndex) {
                final label =
                    rodIndex == 0 ? l10n.monthGridCost : l10n.monthSavedCost;
                final amount = rod.toY.isFinite ? rod.toY : 0.0;
                return BarTooltipItem(
                  '${group.x}\n$label: ${amount.toStringAsFixed(0)} ${l10n.currencyUah}',
                  TextStyle(
                    color: rod.color,
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
    final card = Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingM,
        vertical: AppTheme.spacingS,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: Theme.of(context).textTheme.labelSmall,
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
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
    final loadPercent = provider.inverterMaxPowerW > 0
        ? (data.loadPower / provider.inverterMaxPowerW).clamp(0.0, 1.0)
        : 0.0;
    final isOffline = provider.isInverterOffline;

    Color getLoadColor(double percent) {
      if (percent > 0.85) return const Color(0xFFEF4444);
      if (percent > 0.65) return const Color(0xFFF97316);
      return const Color(0xFF10B981);
    }

    return AppGlassSurface(
      isStrong: true,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            LayoutBuilder(
              builder: (context, constraints) {
                final compact = constraints.maxWidth < 640;
                if (compact) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        l10n.equipmentStatus,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                      const SizedBox(height: AppTheme.spacingS),
                      _ConnectionBadge(
                        isOffline: isOffline,
                        onlineLabel: l10n.connectionOnline,
                        offlineLabel: l10n.connectionOffline,
                        lastUpdatedPrefix: l10n.lastRealtimeUpdate,
                        lastUpdatedAt: provider.lastSuccessfulRealtimeAt,
                      ),
                    ],
                  );
                }
                return Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Expanded(
                      child: Text(
                        l10n.equipmentStatus,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    const SizedBox(width: AppTheme.spacingS),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 220),
                      child: _ConnectionBadge(
                        isOffline: isOffline,
                        onlineLabel: l10n.connectionOnline,
                        offlineLabel: l10n.connectionOffline,
                        lastUpdatedPrefix: l10n.lastRealtimeUpdate,
                        lastUpdatedAt: provider.lastSuccessfulRealtimeAt,
                      ),
                    ),
                  ],
                );
              },
            ),
            const SizedBox(height: AppTheme.spacingL),
            AppProgressBar(
              label: l10n.inverterLoad,
              value: data.loadPower,
              maxValue: provider.inverterMaxPowerW,
              color: getLoadColor(loadPercent),
              suffix: 'W',
              tooltip: l10n.tooltipInverterLoad,
            ),
            const SizedBox(height: AppTheme.spacingL),
            AppProgressBar(
              label: l10n.pvGeneration,
              value: data.pvPower,
              maxValue: provider.pvTotalCapacityW,
              color: const Color(0xFFF59E0B),
              suffix: 'W',
              tooltip: l10n.tooltipPvGeneration,
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
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: color.withValues(alpha: 0.35)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: color),
              const SizedBox(width: 6),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 150),
                child: Text(
                  text,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                        color: color,
                        fontWeight: FontWeight.w700,
                      ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        Text(
          '$lastUpdatedPrefix: $timeLabel',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: Theme.of(context)
              .textTheme
              .labelSmall
              ?.copyWith(color: Theme.of(context).hintColor),
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
        '🧹 chart.ui reset before fetch: range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}');
  }

  Future<void> _fetchChartData({bool background = false}) async {
    final requestId = ++_chartRequestSeq;
    LogService.log(
        '📊 chart.ui fetch start: requestId=$requestId, range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}, bg=$background');
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
          '⏭️ chart.ui stale response ignored: requestId=$requestId, active=$_chartRequestSeq');
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

    return AppGlassSurface(
      isStrong: true,
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            LayoutBuilder(
              builder: (context, constraints) {
                final isCompact = constraints.maxWidth < 760;
                final controls = Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    if (_lastChartRefreshedAt != null)
                      _ChartRefreshBadge(
                        refreshedAt: _lastChartRefreshedAt!,
                        isRefreshing: _isBackgroundRefreshing,
                      ),
                    buildTimeSelector(l10n),
                  ],
                );

                if (isCompact) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.energyOverview,
                          style: Theme.of(context).textTheme.titleLarge),
                      const SizedBox(height: AppTheme.spacingS),
                      controls,
                    ],
                  );
                }

                return Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        l10n.energyOverview,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    const SizedBox(width: AppTheme.spacingS),
                    controls,
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
            SizedBox(
              height: 300,
              child: ExcludeSemantics(
                child: RepaintBoundary(
                  child: _isLoading
                      ? const Center(child: CircularProgressIndicator())
                      : buildChart(context),
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
        Text(l10n.forecastNextDays,
            style: Theme.of(context).textTheme.titleMedium),
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
                        child: Container(
                          padding: const EdgeInsets.all(AppTheme.spacingM),
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              colors: [
                                Theme.of(context)
                                    .cardColor
                                    .withValues(alpha: 0.88),
                                Theme.of(context)
                                    .colorScheme
                                    .surface
                                    .withValues(alpha: 0.66),
                              ],
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                            ),
                            borderRadius:
                                BorderRadius.circular(AppTheme.radiusMedium),
                            border: Border.all(
                              color: Theme.of(context)
                                  .dividerColor
                                  .withValues(alpha: 0.7),
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.black.withValues(alpha: 0.08),
                                blurRadius: 16,
                                offset: const Offset(0, 8),
                              ),
                            ],
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                dayLabel(day.date, l10n),
                                style: Theme.of(context).textTheme.titleSmall,
                              ),
                              const SizedBox(height: AppTheme.spacingS),
                              Text(
                                '${l10n.production}: ${Formatters.formatEnergy(day.energyWh)}',
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                              Text(
                                '${l10n.forecastPeak}: ${Formatters.formatPower(day.peakPowerW)}',
                                style: Theme.of(context).textTheme.bodySmall,
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
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: [
              Theme.of(context).cardColor.withValues(alpha: 0.8),
              Theme.of(context).colorScheme.surface.withValues(alpha: 0.58),
            ],
          ),
          borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
          border: Border.all(
            color: Theme.of(context).dividerColor.withValues(alpha: 0.7),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _TimeButton(
              label: l10n.day,
              isActive: _selectedRange == 0,
              onTap: () {
                setState(() => _selectedRange = 0);
                _startRangeLoadingState();
                _scheduleFetchChartData();
              },
            ),
            _TimeButton(
              label: l10n.week,
              isActive: _selectedRange == 1,
              onTap: () {
                setState(() => _selectedRange = 1);
                _startRangeLoadingState();
                _scheduleFetchChartData();
              },
            ),
            _TimeButton(
              label: l10n.month,
              isActive: _selectedRange == 2,
              onTap: () {
                setState(() => _selectedRange = 2);
                _startRangeLoadingState();
                _scheduleFetchChartData();
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget buildDateNavigator() {
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

    return Wrap(
      alignment: WrapAlignment.center,
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 4,
      runSpacing: 6,
      children: [
        IconButton(
          icon: const Icon(Icons.chevron_left),
          onPressed: () => changeDate(-1),
        ),
        ConstrainedBox(
          constraints: const BoxConstraints(minWidth: 140, maxWidth: 260),
          child: Text(
            getDateText(),
            style: Theme.of(context).textTheme.titleMedium,
            textAlign: TextAlign.center,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        IconButton(
          icon: const Icon(Icons.chevron_right),
          onPressed: canGoForward ? () => changeDate(1) : null,
        ),
        IconButton(
          icon: _isBackgroundRefreshing
              ? SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                )
              : Icon(Icons.refresh_rounded,
                  size: 18, color: Theme.of(context).colorScheme.primary),
          tooltip: AppLocalizations.of(context)!.refreshChart,
          onPressed: _isBackgroundRefreshing
              ? null
              : () => _fetchChartData(background: true),
        ),
      ],
    );
  }

  Widget buildLegend(AppLocalizations l10n) {
    return Wrap(
      spacing: AppTheme.spacingL,
      runSpacing: AppTheme.spacingM,
      children: [
        AppLegendItem(
          label: l10n.production,
          color: const Color(0xFFF59E0B),
          isActive: _showProduction,
          onTap: () => setState(() => _showProduction = !_showProduction),
        ),
        AppLegendItem(
          label: l10n.consumption,
          color: const Color(0xFF8B5CF6),
          isActive: _showConsumption,
          onTap: () => setState(() => _showConsumption = !_showConsumption),
        ),
        AppLegendItem(
          label: l10n.battery,
          color: const Color(0xFF10B981),
          isActive: _showBattery,
          onTap: () => setState(() => _showBattery = !_showBattery),
        ),
        AppLegendItem(
          label: l10n.grid,
          color: const Color(0xFF06B6D4),
          isActive: _showGrid,
          onTap: () => setState(() => _showGrid = !_showGrid),
        ),
        AppLegendItem(
          label: l10n.forecast,
          color: const Color(0xFF38BDF8),
          isActive: _showForecast && _forecastData.isNotEmpty,
          onTap: _forecastData.isNotEmpty
              ? () => setState(() => _showForecast = !_showForecast)
              : null,
        ),
      ],
    );
  }

  Widget buildBatterySignHint(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingM,
        vertical: AppTheme.spacingS,
      ),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor.withValues(alpha: 0.7),
        borderRadius: BorderRadius.circular(AppTheme.radiusSmall),
        border: Border.all(
            color: Theme.of(context).dividerColor.withValues(alpha: 0.7)),
      ),
      child: Row(
        children: [
          const Icon(Icons.battery_charging_full_rounded,
              size: 16, color: Color(0xFF10B981)),
          const SizedBox(width: AppTheme.spacingS),
          Expanded(
            child: Text(
              AppLocalizations.of(context)!.batterySignHint,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }

  Widget buildChart(BuildContext context) {
    final minX = getMinX();
    final maxX = getMaxX();
    final futureStartX = futureStartX0();

    final minY = getMinY();
    final maxY = getMaxY();
    final renderSignature =
        '$_selectedRange|${minX.toStringAsFixed(2)}|${maxX.toStringAsFixed(2)}|${minY.toStringAsFixed(1)}|${maxY.toStringAsFixed(1)}|${getVisibleSpots().length}';
    if (_lastRenderLogSignature != renderSignature) {
      _lastRenderLogSignature = renderSignature;
      LogService.log(
          '🖼️ chart.ui render: range=$_selectedRange, x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)}, y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}, visible=${getVisibleSpots().length}');
    }

    if (_selectedRange != 0) {
      return buildBarChart(context, minX, maxX, minY, maxY, futureStartX);
    }

    var lines = <LineChartBarData>[];

    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(buildLineData(
        _productionData,
        const Color(0xFFF59E0B),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(buildLineData(
        _consumptionData,
        const Color(0xFF8B5CF6),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(buildLineData(
        _batteryData,
        const Color(0xFF10B981),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(buildLineData(
        _gridData,
        const Color(0xFF06B6D4),
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
        color: const Color(0xFF38BDF8).withValues(alpha: 0.8),
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
            getTooltipColor: (spot) => Theme.of(context).cardColor,
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
            getTooltipColor: (_) => Theme.of(context).cardColor,
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
        color: const Color(0xFFF59E0B),
        points: toMap(_productionData),
      ));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.consumption,
        color: const Color(0xFF8B5CF6),
        points: toMap(_consumptionData),
      ));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.battery,
        color: const Color(0xFF10B981),
        points: toMap(_batteryData),
      ));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      result.add(_BarSeriesConfig(
        name: l10n.grid,
        color: const Color(0xFF06B6D4),
        points: toMap(_gridData),
      ));
    }
    if (_showForecast && _forecastData.isNotEmpty && _selectedRange != 0) {
      result.add(_BarSeriesConfig(
        name: l10n.forecast,
        color: const Color(0xFF38BDF8).withValues(alpha: 0.75),
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
      gradient: LinearGradient(
        colors: [
          color.withValues(alpha: 0.72),
          color,
        ],
      ),
      barWidth: 3,
      dotData: const FlDotData(show: false),
      belowBarData: BarAreaData(
        show: true,
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            color.withValues(alpha: 0.24),
            color.withValues(alpha: 0.02),
          ],
        ),
      ),
    );
  }

  double getMaxY() {
    var spots = getVisibleSpots();
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

  double getMinY() {
    var minVal = 0.0;
    for (var spot in getVisibleSpots()) {
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
        '📈 $prefix | range=$_selectedRange pv[q=${quality(_productionData)}](${fmt(_productionData)}) load[q=${quality(_consumptionData)}](${fmt(_consumptionData)}) battery[q=${quality(_batteryData)}](${fmt(_batteryData)}) grid[q=${quality(_gridData)}](${fmt(_gridData)}) forecast[q=${quality(_forecastData)}](${fmt(_forecastData)})');
  }
}

class _TimeButton extends StatelessWidget {
  final String label;
  final bool isActive;
  final VoidCallback onTap;

  const _TimeButton({
    required this.label,
    required this.isActive,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppTheme.spacingL,
            vertical: AppTheme.spacingS,
          ),
          decoration: BoxDecoration(
            color: isActive
                ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.1)
                : Colors.transparent,
            border: Border(
              bottom: BorderSide(
                color: isActive
                    ? Theme.of(context).colorScheme.primary
                    : Colors.transparent,
                width: 2,
              ),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontWeight: isActive ? FontWeight.w600 : FontWeight.w500,
              color: isActive
                  ? Theme.of(context).colorScheme.primary
                  : Theme.of(context).textTheme.bodyMedium?.color,
            ),
          ),
        ),
      ),
    );
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
    // Tick every 30 s to keep "X хв тому" label fresh
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
    if (widget.isRefreshing) return '…';
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
        const SizedBox(width: 4),
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

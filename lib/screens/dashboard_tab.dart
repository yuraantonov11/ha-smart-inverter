import 'dart:async';

import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../services/log_service.dart';
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
    return RefreshIndicator(
      color: Theme.of(context).colorScheme.primary,
      onRefresh: provider.fetchData,
      child: ListView(
        padding: const EdgeInsets.all(AppTheme.spacingL),
        children: [
          // Status Banner
          AppStatusBanner(
            message: provider.statusMessage,
            icon: Icons.info_outline,
          ),

          const SizedBox(height: AppTheme.spacingL),

          // Energy Flow Diagram
          AppCard(
            child: EnergyFlowDiagram(data: data),
          ),

          const SizedBox(height: AppTheme.spacingL),

          // Stats Row
          _StatsSection(provider: provider),

          const SizedBox(height: AppTheme.spacingL),

          // System Capacity
          _SystemCapacitySection(provider: provider, data: data),

          const SizedBox(height: AppTheme.spacingL),

          // Energy Chart
          _EnergyChartSection(provider: provider),

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

    return Column(
      children: [
        AppSectionTitle(
          title: l10n.energyOverview,
          icon: Icons.trending_up_rounded,
        ),
        Row(
          children: [
            Expanded(
              child: AppStatCard(
                label: l10n.today,
                value: daily,
                unit: 'kWh',
                icon: Icons.today_rounded,
                color: const Color(0xFF06B6D4),
              ),
            ),
            const SizedBox(width: AppTheme.spacingL),
            Expanded(
              child: AppStatCard(
                label: l10n.total,
                value: total,
                unit: 'kWh',
                icon: Icons.assessment_rounded,
                color: const Color(0xFFF59E0B),
              ),
            ),
            const SizedBox(width: AppTheme.spacingL),
            Expanded(
              child: AppStatCard(
                label: 'CO₂',
                value: co2,
                unit: 'kg',
                icon: Icons.eco_rounded,
                color: const Color(0xFF10B981),
              ),
            ),
          ],
        ),
      ],
    );
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
    final localeCode = Localizations.localeOf(context).languageCode;
    final onlineLabel = localeCode == 'uk' ? 'Онлайн' : 'Online';
    final offlineLabel = localeCode == 'uk' ? 'Офлайн' : 'Offline';
    final lastUpdatedLabel =
        localeCode == 'uk' ? 'Останнє оновлення' : 'Last update';
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Статус обладнання',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              _ConnectionBadge(
                isOffline: isOffline,
                onlineLabel: onlineLabel,
                offlineLabel: offlineLabel,
                lastUpdatedPrefix: lastUpdatedLabel,
                lastUpdatedAt: provider.lastSuccessfulRealtimeAt,
              ),
            ],
          ),
          const SizedBox(height: AppTheme.spacingL),
          AppProgressBar(
            label: 'Навантаження інвертора',
            value: data.loadPower,
            maxValue: provider.inverterMaxPowerW,
            color: getLoadColor(loadPercent),
            suffix: 'W',
          ),
          const SizedBox(height: AppTheme.spacingL),
          AppProgressBar(
            label: 'Генерація PV',
            value: data.pvPower,
            maxValue: provider.pvTotalCapacityW,
            color: const Color(0xFFF59E0B),
            suffix: 'W',
          ),
        ],
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
      crossAxisAlignment: CrossAxisAlignment.end,
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
              Text(
                text,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: color,
                      fontWeight: FontWeight.w700,
                    ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        Text(
          '$lastUpdatedPrefix: $timeLabel',
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
  List<FlSpot> _productionData = [];
  List<FlSpot> _consumptionData = [];
  List<FlSpot> _batteryData = [];
  List<FlSpot> _gridData = [];
  List<FlSpot> _forecastData = [];
  Timer? _chartDebounce;
  int _chartRequestSeq = 0;
  String? _lastRenderLogSignature;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance
        .addPostFrameCallback((_) => _scheduleFetchChartData(immediate: true));
  }

  @override
  void dispose() {
    _chartDebounce?.cancel();
    super.dispose();
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
      _productionData = [];
      _consumptionData = [];
      _batteryData = [];
      _gridData = [];
      _forecastData = [];
    });
    LogService.log(
        '🧹 chart.ui reset before fetch: range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}');
  }

  Future<void> _fetchChartData() async {
    final requestId = ++_chartRequestSeq;
    LogService.log(
        '📊 chart.ui fetch start: requestId=$requestId, range=$_selectedRange, date=${_currentDate.toIso8601String().substring(0, 10)}');
    if (mounted && !_isLoading) setState(() => _isLoading = true);

    final data = await widget.provider.service
        .getChartData(_selectedRange, _currentDate);

    var forecast = <String, double>{};
    if (_selectedRange == 0) {
      forecast = await widget.provider.weatherService.fetchLocalForecast(
        pvCapacityW: widget.provider.pvTotalCapacityW,
        efficiency: 0.85,
        historicalPvData: widget.provider.historicalPvData,
        targetDate: _currentDate,
      );
    }

    if (!mounted || requestId != _chartRequestSeq) {
      LogService.log(
          '⏭️ chart.ui stale response ignored: requestId=$requestId, active=$_chartRequestSeq');
      return;
    }

    if (mounted) {
      setState(() {
        _productionData = _normalizeSpots(data['pv'] ?? []);
        _consumptionData = _normalizeSpots(data['load'] ?? []);
        _batteryData = _normalizeSpots(data['battery'] ?? []);
        _gridData = _normalizeSpots(data['grid'] ?? []);
        _isLoading = false;
      });

      _logChartUiSummary('chart.ui fetched');

      if (_selectedRange == 0) {
        _loadForecastForToday(forecast);
      } else {
        setState(() => _forecastData = []);
      }
    }
  }

  void _loadForecastForToday(Map<String, double> forecast) {
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

    if (mounted) {
      setState(() {
        _forecastData = _normalizeSpots(spots);
      });
    }
  }

  List<FlSpot> _normalizeSpots(List<FlSpot> raw) {
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

  void _changeDate(int offset) {
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

  String _getDateText() {
    if (_selectedRange == 0) {
      return '${_currentDate.day.toString().padLeft(2, '0')}.${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    } else if (_selectedRange == 1) {
      final start = _startOfWeek(_currentDate);
      final end = start.add(const Duration(days: 6));
      return '${start.day.toString().padLeft(2, '0')}.${start.month.toString().padLeft(2, '0')} - ${end.day.toString().padLeft(2, '0')}.${end.month.toString().padLeft(2, '0')}';
    } else {
      return '${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    }
  }

  DateTime _startOfWeek(DateTime date) {
    final normalized = DateTime(date.year, date.month, date.day);
    return normalized.subtract(Duration(days: normalized.weekday - 1));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    return AppCard(
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.energyOverview,
                  style: Theme.of(context).textTheme.titleLarge),
              _buildTimeSelector(l10n),
            ],
          ),
          const SizedBox(height: AppTheme.spacingM),
          _buildDateNavigator(),
          const SizedBox(height: AppTheme.spacingM),
          _buildLegend(l10n),
          if (_selectedRange == 0 || _showBattery) ...[
            const SizedBox(height: AppTheme.spacingS),
            _buildBatterySignHint(context),
          ],
          const SizedBox(height: AppTheme.spacingL),
          SizedBox(
            height: 300,
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : _buildChart(context),
          ),
        ],
      ),
    );
  }

  Widget _buildTimeSelector(AppLocalizations l10n) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
        border: Border.all(
          color: Theme.of(context).dividerColor,
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
    );
  }

  Widget _buildDateNavigator() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final currentDay =
        DateTime(_currentDate.year, _currentDate.month, _currentDate.day);
    final currentMonth = DateTime(_currentDate.year, _currentDate.month);
    final nowMonth = DateTime(now.year, now.month);
    final currentWeek = _startOfWeek(_currentDate);
    final nowWeek = _startOfWeek(today);

    final canGoForward = _selectedRange == 0
        ? currentDay.isBefore(today)
        : _selectedRange == 1
            ? currentWeek.isBefore(nowWeek)
            : currentMonth.isBefore(nowMonth);

    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          icon: const Icon(Icons.chevron_left),
          onPressed: () => _changeDate(-1),
        ),
        Text(
          _getDateText(),
          style: Theme.of(context).textTheme.titleMedium,
        ),
        IconButton(
          icon: const Icon(Icons.chevron_right),
          onPressed: canGoForward ? () => _changeDate(1) : null,
        ),
      ],
    );
  }

  Widget _buildLegend(AppLocalizations l10n) {
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
        if (_selectedRange == 0)
          AppLegendItem(
            label: 'Прогноз',
            color: const Color(0xFFF59E0B),
            isActive: _showForecast && _forecastData.isNotEmpty,
            onTap: _forecastData.isNotEmpty
                ? () => setState(() => _showForecast = !_showForecast)
                : null,
          ),
      ],
    );
  }

  Widget _buildBatterySignHint(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(
        horizontal: AppTheme.spacingM,
        vertical: AppTheme.spacingS,
      ),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(AppTheme.radiusSmall),
      ),
      child: Row(
        children: [
          const Icon(Icons.battery_charging_full_rounded,
              size: 16, color: Color(0xFF10B981)),
          const SizedBox(width: AppTheme.spacingS),
          Expanded(
            child: Text(
              "АКБ: '+' означає заряд, '-' означає розряд.",
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildChart(BuildContext context) {
    final minX = _getMinX();
    final maxX = _getMaxX();

    final minY = _getMinY();
    final maxY = _getMaxY();
    final renderSignature =
        '$_selectedRange|${minX.toStringAsFixed(2)}|${maxX.toStringAsFixed(2)}|${minY.toStringAsFixed(1)}|${maxY.toStringAsFixed(1)}|${_getVisibleSpots().length}';
    if (_lastRenderLogSignature != renderSignature) {
      _lastRenderLogSignature = renderSignature;
      LogService.log(
          '🖼️ chart.ui render: range=$_selectedRange, x=${minX.toStringAsFixed(2)}..${maxX.toStringAsFixed(2)}, y=${minY.toStringAsFixed(1)}..${maxY.toStringAsFixed(1)}, visible=${_getVisibleSpots().length}');
    }

    var lines = <LineChartBarData>[];

    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(_buildLineData(
        _productionData,
        const Color(0xFFF59E0B),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(_buildLineData(
        _consumptionData,
        const Color(0xFF8B5CF6),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(_buildLineData(
        _batteryData,
        const Color(0xFF10B981),
        isCurved: _selectedRange == 0,
      ));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(_buildLineData(
        _gridData,
        const Color(0xFF06B6D4),
        isCurved: _selectedRange == 0,
      ));
    }

    if (_showForecast && _forecastData.isNotEmpty && _selectedRange == 0) {
      lines.add(LineChartBarData(
        spots: _normalizeSpots(_forecastData),
        isCurved: true,
        curveSmoothness: 0.18,
        preventCurveOverShooting: true,
        preventCurveOvershootingThreshold: 8,
        color: const Color(0xFFF59E0B).withValues(alpha: 0.5),
        barWidth: 2,
        dotData: const FlDotData(show: false),
        dashArray: [5, 5],
        belowBarData: BarAreaData(show: false),
      ));
    }

    if (lines.isEmpty) {
      return AppEmptyState(
        title: 'Нема даних',
        message: 'Графік повинен завантажитися через деякий час',
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
              reservedSize: 40,
              getTitlesWidget: (value, meta) => SideTitleWidget(
                meta: meta,
                child: Text(
                  _selectedRange == 0
                      ? Formatters.formatPower(value)
                      : Formatters.formatEnergy(value),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
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
                  _formatBottomAxisLabel(
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
            color: Theme.of(context).dividerColor.withValues(alpha: 0.3),
            strokeWidth: 1,
          ),
        ),
        borderData: FlBorderData(show: false),
        lineBarsData: lines,
      ),
    );
  }

  LineChartBarData _buildLineData(
    List<FlSpot> spots,
    Color color, {
    required bool isCurved,
  }) {
    final normalized = _normalizeSpots(spots);
    final useCurved = isCurved && normalized.length >= 4;
    return LineChartBarData(
      spots: normalized,
      isCurved: useCurved,
      curveSmoothness: useCurved ? 0.18 : 0.0,
      preventCurveOverShooting: true,
      preventCurveOvershootingThreshold: 8,
      color: color,
      barWidth: 2.5,
      dotData: const FlDotData(show: false),
      belowBarData:
          BarAreaData(show: true, color: color.withValues(alpha: 0.1)),
    );
  }

  double _getMaxY() {
    var spots = _getVisibleSpots();
    if (spots.isEmpty) return 10.0;
    var max = spots.map((e) => e.y).reduce((a, b) => a > b ? a : b);
    return max == 0 ? 10.0 : max * 1.15;
  }

  double _getMaxX() {
    if (_selectedRange == 0) return 23;
    if (_selectedRange == 1) return 6;

    final lastDay = DateTime(_currentDate.year, _currentDate.month + 1, 0).day;
    return lastDay.toDouble();
  }

  double _getMinX() {
    if (_selectedRange == 0 || _selectedRange == 1) return 0;
    return 1;
  }

  String _formatBottomAxisLabel(double value, AppLocalizations l10n) {
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

  double _getMinY() {
    var minVal = 0.0;
    for (var spot in _getVisibleSpots()) {
      if (spot.y < minVal) minVal = spot.y;
    }
    return minVal >= 0 ? 0 : minVal * 1.2;
  }

  List<FlSpot> _getVisibleSpots() {
    var all = <FlSpot>[];
    if (_showProduction) all.addAll(_productionData);
    if (_showConsumption) all.addAll(_consumptionData);
    if (_showBattery) all.addAll(_batteryData);
    if (_showGrid) all.addAll(_gridData);
    if (_showForecast && _selectedRange == 0) all.addAll(_forecastData);
    return all;
  }

  void _logChartUiSummary(String prefix) {
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
      final axisMin = _getMinX();
      final axisMax = _getMaxX();
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

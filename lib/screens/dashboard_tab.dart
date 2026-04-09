import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
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
    final loadPercent = provider.inverterMaxPowerW > 0
        ? (data.loadPower / provider.inverterMaxPowerW).clamp(0.0, 1.0)
        : 0.0;

    Color getLoadColor(double percent) {
      if (percent > 0.85) return const Color(0xFFEF4444);
      if (percent > 0.65) return const Color(0xFFF97316);
      return const Color(0xFF10B981);
    }

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Статус обладнання',
            style: Theme.of(context).textTheme.titleLarge,
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

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _fetchChartData());
  }

  Future<void> _fetchChartData() async {
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

    if (mounted) {
      setState(() {
        _productionData = data['pv'] ?? [];
        _consumptionData = data['load'] ?? [];
        _batteryData = data['battery'] ?? [];
        _gridData = data['grid'] ?? [];
        _isLoading = false;
      });

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
        _forecastData = spots;
      });
    }
  }

  void _changeDate(int offset) {
    setState(() {
      if (_selectedRange == 0) {
        _currentDate = _currentDate.add(Duration(days: offset));
      } else if (_selectedRange == 1) {
        _currentDate =
            DateTime(_currentDate.year, _currentDate.month + offset, 1);
      } else {
        _currentDate = DateTime(_currentDate.year + offset, 1, 1);
      }
    });
    _fetchChartData();
  }

  String _getDateText() {
    if (_selectedRange == 0) {
      return '${_currentDate.day.toString().padLeft(2, '0')}.${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    } else if (_selectedRange == 1) {
      return '${_currentDate.month.toString().padLeft(2, '0')}.${_currentDate.year}';
    } else {
      return '${_currentDate.year}';
    }
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
              _fetchChartData();
            },
          ),
          _TimeButton(
            label: l10n.week,
            isActive: _selectedRange == 1,
            onTap: () {
              setState(() => _selectedRange = 1);
              _fetchChartData();
            },
          ),
          _TimeButton(
            label: l10n.month,
            isActive: _selectedRange == 2,
            onTap: () {
              setState(() => _selectedRange = 2);
              _fetchChartData();
            },
          ),
        ],
      ),
    );
  }

  Widget _buildDateNavigator() {
    var canGoForward = false;
    if (_selectedRange == 0) {
      canGoForward =
          _currentDate.isBefore(DateTime.now().add(const Duration(days: 1)));
    } else {
      canGoForward = _currentDate
              .isBefore(DateTime.now().subtract(const Duration(days: 1))) ||
          (_selectedRange > 0 && _currentDate.year <= DateTime.now().year);
    }

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

  Widget _buildChart(BuildContext context) {
    var lines = <LineChartBarData>[];

    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(_buildLineData(_productionData, const Color(0xFFF59E0B)));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(_buildLineData(_consumptionData, const Color(0xFF8B5CF6)));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(_buildLineData(_batteryData, const Color(0xFF10B981)));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(_buildLineData(_gridData, const Color(0xFF06B6D4)));
    }

    if (_showForecast && _forecastData.isNotEmpty && _selectedRange == 0) {
      lines.add(LineChartBarData(
        spots: _forecastData,
        isCurved: true,
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
        minX: 0,
        maxX: 23,
        minY: _getMinY(),
        maxY: _getMaxY(),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (spot) => Theme.of(context).cardColor,
            getTooltipItems: (spots) => spots
                .map((spot) => LineTooltipItem(
                      Formatters.formatPower(spot.y),
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
                  Formatters.formatPower(value),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              interval: _selectedRange == 0 ? 4 : 1,
              getTitlesWidget: (value, meta) => SideTitleWidget(
                meta: meta,
                space: 10,
                child: Text(
                  _selectedRange == 0
                      ? '${value.toInt()}:00'
                      : '${value.toInt()}',
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

  LineChartBarData _buildLineData(List<FlSpot> spots, Color color) {
    return LineChartBarData(
      spots: spots,
      isCurved: true,
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

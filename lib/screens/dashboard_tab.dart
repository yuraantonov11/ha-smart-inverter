import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:provider/provider.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../widgets/energy_flow.dart';
import '../widgets/control_panel.dart';
import '../utils/formatters.dart';

class DashboardTab extends StatelessWidget {
  final AppStateProvider provider;
  final InverterData data;

  const DashboardTab({super.key, required this.provider, required this.data});

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      color: Colors.amber,
      onRefresh: provider.fetchData,
      child: ListView(
        padding: const EdgeInsets.all(20.0),
        children: [
          _buildStatusBanner(provider.statusMessage),
          const SizedBox(height: 16),
          EnergyFlowDiagram(data: data, isEn: provider.isEn),
          const SizedBox(height: 16),
          _HistoricalStatsWidget(provider: provider),
          const SizedBox(height: 16),
          const _EnergyLineChartWidget(), // Графік сам керує своїми даними
          const SizedBox(height: 16),
          ControlPanel(provider: provider),
        ],
      ),
    );
  }

  Widget _buildStatusBanner(String message) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 16),
      decoration: BoxDecoration(
        color: Colors.amber.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.sync, color: Colors.amber, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                  color: Colors.amber,
                  fontWeight: FontWeight.w600,
                  fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoricalStatsWidget extends StatelessWidget {
  final AppStateProvider provider;

  const _HistoricalStatsWidget({required this.provider});

  @override
  Widget build(BuildContext context) {
    final isEn = provider.isEn;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    final daily = provider.service.dailyEnergy.toStringAsFixed(1);
    final total = provider.service.totalEnergy.toStringAsFixed(0);
    final co2 = provider.service.co2Reduction.toStringAsFixed(1);

    return Row(
      children: [
        Expanded(
          child: _buildStatCard(context, isEn ? 'Today' : 'Сьогодні',
              '$daily kWh', Icons.today_rounded, Colors.blueAccent, isDark),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildStatCard(
              context,
              isEn ? 'Total' : 'Всього',
              '$total kWh',
              Icons.account_balance_wallet_rounded,
              Colors.amber,
              isDark),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildStatCard(context, 'CO₂', '$co2 kg', Icons.eco_rounded,
              Colors.greenAccent, isDark),
        ),
      ],
    );
  }

  Widget _buildStatCard(BuildContext context, String title, String value,
      IconData icon, Color color, bool isDark) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 8),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(20),
        boxShadow: isDark
            ? []
            : [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.05), blurRadius: 10)
              ],
      ),
      child: Column(
        children: [
          Icon(icon, color: color, size: 24),
          const SizedBox(height: 8),
          Text(value,
              style:
                  const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(height: 4),
          Text(title,
              style: const TextStyle(color: Colors.grey, fontSize: 11),
              textAlign: TextAlign.center),
        ],
      ),
    );
  }
}

class _EnergyLineChartWidget extends StatefulWidget {
  const _EnergyLineChartWidget();

  @override
  State<_EnergyLineChartWidget> createState() => _EnergyLineChartWidgetState();
}

class _EnergyLineChartWidgetState extends State<_EnergyLineChartWidget> {
  int _selectedRange = 0; // 0 = Day, 1 = Week, 2 = Month

  // 4 Тумблери відображення
  bool _showProduction = true;
  bool _showConsumption = true;
  bool _showBattery = true;
  bool _showGrid =
      false; // Вимкнено за замовчуванням, щоб не перевантажувати графік

  bool _isLoading = true;
  List<FlSpot> _productionData = [];
  List<FlSpot> _consumptionData = [];
  List<FlSpot> _batteryData = [];
  List<FlSpot> _gridData = [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _fetchChartData();
    });
  }

  Future<void> _fetchChartData() async {
    if (!mounted) return;
    setState(() => _isLoading = true);

    final provider = context.read<AppStateProvider>();
    final result = await provider.service.getChartData(_selectedRange);

    if (mounted) {
      setState(() {
        _productionData = result['generationPower'] ?? [];
        _consumptionData = result['loadPower'] ?? [];
        _batteryData = result['batteryPower'] ?? [];
        _gridData = result['gridPower'] ?? [];
        _isLoading = false;
      });
    }
  }

  void _onRangeChanged(int index) {
    if (_selectedRange == index) return;
    setState(() => _selectedRange = index);
    _fetchChartData();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    // Формуємо масив ліній та кольорів для тултипів
    var lines = <LineChartBarData>[];
    var visibleColors = <Color>[];

    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(_buildLineData(_productionData, Colors.amber));
      visibleColors.add(Colors.amber);
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(_buildLineData(_consumptionData, Colors.purpleAccent));
      visibleColors.add(Colors.purpleAccent);
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(_buildLineData(_batteryData, Colors.greenAccent));
      visibleColors.add(Colors.greenAccent);
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(_buildLineData(_gridData, Colors.blueAccent));
      visibleColors.add(Colors.blueAccent);
    }

    return Container(
      height: 400, // Збільшено висоту для легенди
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: isDark
            ? []
            : [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.05), blurRadius: 10)
              ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.energyOverview,
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.bold)),
              _buildTimeSelector(isDark, l10n),
            ],
          ),
          const SizedBox(height: 16),
          // Wrap гарантує, що кнопки плавно перейдуть на новий рядок, якщо не помістяться
          Wrap(
            spacing: 16,
            runSpacing: 12,
            children: [
              _buildLegendItem(Colors.amber, l10n.production, _showProduction,
                  () => setState(() => _showProduction = !_showProduction)),
              _buildLegendItem(
                  Colors.purpleAccent,
                  l10n.consumption,
                  _showConsumption,
                  () => setState(() => _showConsumption = !_showConsumption)),
              _buildLegendItem(Colors.greenAccent, l10n.battery, _showBattery,
                  () => setState(() => _showBattery = !_showBattery)),
              _buildLegendItem(Colors.blueAccent, l10n.grid, _showGrid,
                  () => setState(() => _showGrid = !_showGrid)),
            ],
          ),
          const SizedBox(height: 24),
          Expanded(
            child: Stack(
              children: [
                LineChart(
                  LineChartData(
                    minY:
                        _getMinY(), // Дозволяє графіку опускатись нижче нуля (розряд батареї)
                    maxY: _getMaxY(),
                    lineTouchData: LineTouchData(
                      touchTooltipData: LineTouchTooltipData(
                        getTooltipColor: (spot) =>
                            Theme.of(context).cardColor.withValues(alpha: 0.9),
                        getTooltipItems: (spots) => spots.map((spot) {
                          // Знаходимо правильний колір завдяки масиву visibleColors
                          final color = visibleColors[spot.barIndex];
                          return LineTooltipItem(
                            _selectedRange == 0
                                ? Formatters.formatPower(spot.y)
                                : Formatters.formatEnergy(spot.y),
                            TextStyle(
                                color: color, fontWeight: FontWeight.bold),
                          );
                        }).toList(),
                      ),
                    ),
                    gridData: FlGridData(
                      show: true,
                      drawVerticalLine: false,
                      // Жирніша лінія на рівні нуля
                      getDrawingHorizontalLine: (value) => FlLine(
                          color: value == 0
                              ? Colors.grey.withValues(alpha: 0.3)
                              : Colors.grey.withValues(alpha: 0.1),
                          strokeWidth: value == 0 ? 2 : 1),
                    ),
                    titlesData: FlTitlesData(
                      show: true,
                      rightTitles: const AxisTitles(
                          sideTitles: SideTitles(showTitles: false)),
                      topTitles: const AxisTitles(
                          sideTitles: SideTitles(showTitles: false)),
                      leftTitles: AxisTitles(
                        sideTitles: SideTitles(
                          showTitles: true,
                          reservedSize: 50,
                          getTitlesWidget: (value, meta) {
                            if (value == 0 && _getMinY() == 0) {
                              return const SizedBox.shrink();
                            }
                            return SideTitleWidget(
                              meta: meta,
                              child: Text(
                                _selectedRange == 0
                                    ? Formatters.formatPower(value)
                                    : Formatters.formatEnergy(value),
                                style: const TextStyle(
                                    color: Colors.grey, fontSize: 9),
                              ),
                            );
                          },
                        ),
                      ),
                      bottomTitles: AxisTitles(
                        sideTitles: SideTitles(
                          showTitles: true,
                          interval: _selectedRange == 0 ? 4 : 1,
                          getTitlesWidget: (value, meta) =>
                              _getBottomTitles(value, meta, l10n),
                        ),
                      ),
                    ),
                    borderData: FlBorderData(show: false),
                    lineBarsData: lines, // Передаємо зібраний список
                  ),
                  duration: const Duration(milliseconds: 400),
                ),
                if (_isLoading)
                  Container(
                    color: Theme.of(context).cardColor.withValues(alpha: 0.3),
                    child: const Center(
                        child: CircularProgressIndicator(color: Colors.amber)),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLegendItem(
      Color color, String text, bool isActive, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              width: 12,
              height: 12,
              decoration: BoxDecoration(
                color: isActive ? color : Colors.grey.withValues(alpha: 0.3),
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 8),
            Text(
              text,
              style: TextStyle(
                fontSize: 13,
                color: isActive ? null : Colors.grey,
                decoration: isActive ? null : TextDecoration.lineThrough,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTimeSelector(bool isDark, AppLocalizations l10n) {
    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isDark ? Colors.black26 : Colors.grey.shade100,
        borderRadius: BorderRadius.circular(16),
      ),
      child: ToggleButtons(
        isSelected: [
          _selectedRange == 0,
          _selectedRange == 1,
          _selectedRange == 2
        ],
        onPressed: _onRangeChanged,
        borderRadius: BorderRadius.circular(16),
        fillColor: Theme.of(context).colorScheme.primary.withValues(alpha: 0.1),
        selectedColor: Theme.of(context).colorScheme.primary,
        color: Colors.grey,
        constraints: const BoxConstraints(minHeight: 32, minWidth: 60),
        children: [
          Text(l10n.day,
              style:
                  const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
          Text(l10n.week,
              style:
                  const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
          Text(l10n.month,
              style:
                  const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }

  LineChartBarData _buildLineData(List<FlSpot> spots, Color color) {
    return LineChartBarData(
      spots: spots,
      isCurved: true,
      curveSmoothness: 0.3,
      color: color,
      barWidth: 3,
      isStrokeCapRound: true,
      dotData: const FlDotData(show: false),
      belowBarData: BarAreaData(
        show: true,
        gradient: LinearGradient(
          colors: [color.withValues(alpha: 0.2), color.withValues(alpha: 0.0)],
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
        ),
      ),
    );
  }

  Widget _getBottomTitles(double value, TitleMeta meta, AppLocalizations l10n) {
    const style = TextStyle(color: Colors.grey, fontSize: 10);
    if (_selectedRange == 0) {
      if (value % 4 == 0) {
        return SideTitleWidget(
            meta: meta,
            space: 10,
            child: Text('${value.toInt()}:00', style: style));
      }
    } else if (_selectedRange == 1) {
      final days = [
        l10n.mon,
        l10n.tue,
        l10n.wed,
        l10n.thu,
        l10n.fri,
        l10n.sat,
        l10n.sun
      ];
      if (value >= 0 && value < 7) {
        return SideTitleWidget(
            meta: meta,
            space: 10,
            child: Text(days[value.toInt()], style: style));
      }
    } else {
      if (value % 5 == 0 || value == 1) {
        return SideTitleWidget(
            meta: meta,
            space: 10,
            child: Text('${value.toInt()}', style: style));
      }
    }
    return const SizedBox.shrink();
  }

  List<FlSpot> _getVisibleSpots() {
    var all = <FlSpot>[];
    if (_showProduction) all.addAll(_productionData);
    if (_showConsumption) all.addAll(_consumptionData);
    if (_showBattery) all.addAll(_batteryData);
    if (_showGrid) all.addAll(_gridData);
    return all;
  }

  double _getMaxY() {
    var maxVal = 0.0;
    for (var spot in _getVisibleSpots()) {
      if (spot.y > maxVal) maxVal = spot.y;
    }
    return maxVal == 0 ? 100 : maxVal * 1.2;
  }

  double _getMinY() {
    var minVal = 0.0;
    for (var spot in _getVisibleSpots()) {
      if (spot.y < minVal) minVal = spot.y;
    }
    // Якщо мінімальне значення менше нуля (наприклад, розряджається батарея -2000 W)
    return minVal >= 0 ? 0 : minVal * 1.2;
  }
}

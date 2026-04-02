import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
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
          const _EnergyLineChartWidget(),
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
  bool _showProduction = true;
  bool _showConsumption = true;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    return Container(
      height: 360,
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
          Row(
            children: [
              _buildLegendItem(Colors.amber, l10n.production, _showProduction,
                  () => setState(() => _showProduction = !_showProduction)),
              const SizedBox(width: 20),
              _buildLegendItem(
                  Colors.purpleAccent,
                  l10n.consumption,
                  _showConsumption,
                  () => setState(() => _showConsumption = !_showConsumption)),
            ],
          ),
          const SizedBox(height: 24),
          Expanded(
            child: LineChart(
              LineChartData(
                minY: 0,
                maxY: _getMaxY(),
                lineTouchData: LineTouchData(
                  touchTooltipData: LineTouchTooltipData(
                    getTooltipColor: (spot) =>
                        Theme.of(context).cardColor.withValues(alpha: 0.9),
                    getTooltipItems: (spots) => spots.map((spot) {
                      final isProd = spot.barIndex == 0;
                      return LineTooltipItem(
                        _selectedRange == 0
                            ? Formatters.formatPower(spot.y)
                            : Formatters.formatEnergy(spot.y),
                        TextStyle(
                            color: isProd ? Colors.amber : Colors.purpleAccent,
                            fontWeight: FontWeight.bold),
                      );
                    }).toList(),
                  ),
                ),
                gridData: FlGridData(
                  show: true,
                  drawVerticalLine: false,
                  getDrawingHorizontalLine: (value) => FlLine(
                      color: Colors.grey.withValues(alpha: 0.1),
                      strokeWidth: 1),
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
                        if (value == 0) return const SizedBox.shrink();
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
                lineBarsData: [
                  if (_showProduction)
                    _buildLineData(_getProductionData(), Colors.amber),
                  if (_showConsumption)
                    _buildLineData(_getConsumptionData(), Colors.purpleAccent),
                ],
              ),
              duration: const Duration(milliseconds: 400),
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
        onPressed: (index) => setState(() => _selectedRange = index),
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

  double _getMaxY() {
    return _selectedRange == 0 ? 6000 : 40000;
  }

  List<FlSpot> _getProductionData() {
    if (_selectedRange == 0) {
      return List.generate(24, (h) {
        var val = 0.0;
        if (h > 5 && h < 19) {
          val = 4500.0 * (1.0 - (h - 12).abs() / 7.0);
        }
        return FlSpot(h.toDouble(), val < 0 ? 0.0 : val);
      });
    }
    return List.generate(
        7, (i) => FlSpot(i.toDouble(), 15000.0 + (i % 3) * 5000.0));
  }

  List<FlSpot> _getConsumptionData() {
    if (_selectedRange == 0) {
      return List.generate(24, (h) {
        var val = 500 + (h == 8 || h == 9 || h == 19 || h == 20 ? 2500 : 300);
        // Тут ми гарантуємо, що передаємо double у FlSpot:
        return FlSpot(h.toDouble(), val.toDouble());
      });
    }
    return List.generate(
        7, (i) => FlSpot(i.toDouble(), 18000.0 + (i % 2) * 4000.0));
  }
}

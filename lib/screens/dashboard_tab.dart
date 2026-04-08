import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';

import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../services/weather_service.dart';
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
          EnergyFlowDiagram(data: data),
          const SizedBox(height: 16),
          _HistoricalStatsWidget(provider: provider),
          const SizedBox(height: 16),
          _EnergyLineChartWidget(provider: provider),
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
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    final daily = provider.service.dailyEnergy.toStringAsFixed(1);
    final total = provider.service.totalEnergy.toStringAsFixed(0);
    final co2 = provider.service.co2Reduction.toStringAsFixed(1);

    return Row(
      children: [
        Expanded(
          child: _buildStatCard(context, l10n.today, '$daily kWh',
              Icons.today_rounded, Colors.blueAccent, isDark),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildStatCard(context, l10n.total, '$total kWh',
              Icons.account_balance_wallet_rounded, Colors.amber, isDark),
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
  final AppStateProvider provider;

  const _EnergyLineChartWidget({required this.provider});

  @override
  State<_EnergyLineChartWidget> createState() => _EnergyLineChartWidgetState();
}

class _EnergyLineChartWidgetState extends State<_EnergyLineChartWidget> {
  int _selectedRange = 0; // 0 = Day, 1 = Week, 2 = Month
  DateTime _currentDate = DateTime.now();

  bool _showProduction = true;
  bool _showConsumption = true;
  bool _showBattery = true;
  bool _showGrid = false;

  bool _isLoading = true;
  List<FlSpot> _productionData = [];
  List<FlSpot> _consumptionData = [];
  List<FlSpot> _batteryData = [];
  List<FlSpot> _gridData = [];

  List<FlSpot> _forecastData = [];
  late bool _showForecast = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _fetchChartData());
  }

  Future<void> _fetchChartData() async {
    if (mounted && !_isLoading) setState(() => _isLoading = true);

    // 1. Отримуємо дані з інвертора (реальні лінії графіка)
    final data = await widget.provider.service
        .getChartData(_selectedRange, _currentDate);

    // 2. Розумний прогноз (лише для денного графіка)
    var forecast = <String, double>{};
    if (_selectedRange == 0) {
      // Спочатку витягуємо історію
      final histPv =
          await widget.provider.service.getHistoricalPvMapForForecast();
      // Передаємо історію в сервіс погоди для навчання
      forecast = await WeatherService().fetchDynamicForecast(histPv);
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

    forecast.forEach((timeStr, predictedWatts) {
      if (timeStr.startsWith(todayPrefix)) {
        // Формат: "2026-04-08 13:00" (зі пробілом, не 'T')
        final timeParts = timeStr.split(' ');
        if (timeParts.length > 1) {
          // timeParts[1] = "13:00"
          final hour = double.tryParse(timeParts[1].split(':')[0]) ?? 0.0;
          spots.add(FlSpot(hour, predictedWatts));
        }
      }
    });

    if (mounted) {
      setState(() {
        _forecastData = spots;
      });
    }
  }

  void _onRangeChanged(int index) {
    if (_selectedRange == index) return;
    setState(() => _selectedRange = index);
    _fetchChartData();
  }

  void _changeDate(int offset) {
    setState(() {
      if (_selectedRange == 0) {
        // День
        _currentDate = _currentDate.add(Duration(days: offset));
      } else if (_selectedRange == 1) {
        // Місяць
        _currentDate =
            DateTime(_currentDate.year, _currentDate.month + offset, 1);
      } else {
        // Рік
        _currentDate = DateTime(_currentDate.year + offset, 1, 1);
      }
    });
    _fetchChartData(); // Завантажуємо нові дані
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

  Widget _buildDateSelector() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          icon: const Icon(Icons.chevron_left, color: Colors.white),
          onPressed: () => _changeDate(-1),
        ),
        Text(
          _getDateText(),
          style: const TextStyle(
              fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
        ),
        IconButton(
          icon: const Icon(Icons.chevron_right, color: Colors.white),
          // Блокуємо перехід у майбутнє, якщо потрібно
          onPressed: _currentDate.isBefore(
                      DateTime.now().subtract(const Duration(days: 1))) ||
                  (_selectedRange > 0 &&
                      _currentDate.year <= DateTime.now().year)
              ? () => _changeDate(1)
              : null,
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    return Container(
      height: 450, // Трохи збільшимо висоту
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
          const SizedBox(height: 8),
          _buildDateSelector(),
          const SizedBox(height: 8),
          _buildLegend(l10n),
          const SizedBox(height: 24),
          Expanded(
            child: Stack(
              children: [
                // ВИБІР ТИПУ ГРАФІКА
                _selectedRange == 0
                    ? _buildLineChart(context, l10n)
                    : _buildBarChart(context, l10n),
                if (_isLoading)
                  const Center(
                      child: CircularProgressIndicator(color: Colors.amber)),
              ],
            ),
          ),
        ],
      ),
    );
  }

// --- МЕТОД ДЛЯ ЛІНІЙНОГО ГРАФІКА (ДЕНЬ) ---
  Widget _buildLineChart(BuildContext context, AppLocalizations l10n) {
    var lines = <LineChartBarData>[];

    // ДОДАНО: Перевірка .isNotEmpty, щоб уникнути крашу графіка через порожні масиви
    if (_showProduction && _productionData.isNotEmpty) {
      lines.add(_buildLineData(_productionData, Colors.amber));
    }
    if (_showConsumption && _consumptionData.isNotEmpty) {
      lines.add(_buildLineData(_consumptionData, Colors.purpleAccent));
    }
    if (_showBattery && _batteryData.isNotEmpty) {
      lines.add(_buildLineData(_batteryData, Colors.greenAccent));
    }
    if (_showGrid && _gridData.isNotEmpty) {
      lines.add(_buildLineData(_gridData, Colors.blueAccent));
    }

    if (_showForecast && _forecastData.isNotEmpty && _selectedRange == 0) {
      lines.add(LineChartBarData(
        spots: _forecastData,
        isCurved: true,
        color: Colors.amber
            .withValues(alpha: 0.6), // Напівпрозорий жовтий для прогнозу
        barWidth: 2,
        dotData: const FlDotData(show: false),
        dashArray: [5, 5], // РОБИТИ ЛІНІЮ ПУНКТИРНОЮ
        belowBarData: BarAreaData(show: false), // Без заливки знизу
      ));
    }

    return LineChart(
      LineChartData(
        minX: 0, // ФІКСУЄМО початок доби (00:00)
        maxX: 23, // ФІКСУЄМО кінець доби (23:00)
        minY: _getMinY(),
        maxY: _getMaxY(),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (spot) => Theme.of(context).cardColor,
            getTooltipItems: (spots) => spots
                .map((spot) => LineTooltipItem(
                      Formatters.formatPower(spot.y),
                      TextStyle(
                          color: spot.bar.color, fontWeight: FontWeight.bold),
                    ))
                .toList(),
          ),
        ),
        titlesData: _buildTitlesData(l10n),
        gridData: _buildGridData(),
        borderData: FlBorderData(show: false),
        lineBarsData: lines,
      ),
    );
  }

  int _getMaxDataLength() {
    var maxLen = 0;
    if (_productionData.length > maxLen) maxLen = _productionData.length;
    if (_consumptionData.length > maxLen) maxLen = _consumptionData.length;
    if (_batteryData.length > maxLen) maxLen = _batteryData.length;
    if (_gridData.length > maxLen) maxLen = _gridData.length;
    return maxLen;
  }

// --- МЕТОД ДЛЯ СТОВПЧИКОВОГО ГРАФІКА (ТИЖДЕНЬ/МІСЯЦЬ) ---
  Widget _buildBarChart(BuildContext context, AppLocalizations l10n) {
    final maxLen =
        _getMaxDataLength(); // Отримуємо правильну кількість елементів

    return BarChart(
      BarChartData(
        alignment: BarChartAlignment.spaceAround,
        maxY: _getMaxY(),
        minY: _getMinY(),
        barTouchData: BarTouchData(
          touchTooltipData: BarTouchTooltipData(
            getTooltipColor: (group) => Theme.of(context).cardColor,
            getTooltipItem: (group, groupIndex, rod, rodIndex) {
              return BarTooltipItem(
                '${rod.toY.toStringAsFixed(2)} kWh',
                TextStyle(color: rod.color, fontWeight: FontWeight.bold),
              );
            },
          ),
        ),
        titlesData: _buildTitlesData(l10n),
        gridData: _buildGridData(),
        borderData: FlBorderData(show: false),
        barGroups: List.generate(maxLen, (index) {
          // ВИПРАВЛЕНО ТУТ
          return BarChartGroupData(
            x: index,
            barRods: _buildBarRods(index),
          );
        }),
      ),
    );
  }

  List<BarChartRodData> _buildBarRods(int index) {
    var rods = <BarChartRodData>[];

    // Вказуємо значення як double явно (додаємо .0)
    var width = _selectedRange == 1 ? 8.0 : 4.0;

    if (_showProduction && index < _productionData.length) {
      rods.add(BarChartRodData(
        toY: _productionData[index].y.toDouble(), // Додаємо toDouble()
        color: Colors.amber,
        width: width,
      ));
    }
    if (_showConsumption && index < _consumptionData.length) {
      rods.add(BarChartRodData(
        toY: _consumptionData[index].y.toDouble(),
        color: Colors.purpleAccent,
        width: width,
      ));
    }
    if (_showBattery && index < _batteryData.length) {
      rods.add(BarChartRodData(
        toY: _batteryData[index].y.toDouble(),
        color: Colors.greenAccent,
        width: width,
      ));
    }
    if (_showGrid && index < _gridData.length) {
      rods.add(BarChartRodData(
        toY: _gridData[index].y.toDouble(),
        color: Colors.blueAccent,
        width: width,
      ));
    }
    return rods;
  }

  FlTitlesData _buildTitlesData(AppLocalizations l10n) {
    return FlTitlesData(
      show: true,
      rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      leftTitles: AxisTitles(
        sideTitles: SideTitles(
          showTitles: true,
          reservedSize: 45,
          getTitlesWidget: (value, meta) {
            // Щоб не було 0 kWh всюди, використовуємо toStringAsFixed
            var text = _selectedRange == 0
                ? Formatters.formatPower(value)
                : value.toStringAsFixed(1);
            return SideTitleWidget(
                meta: meta,
                child: Text(text,
                    style: const TextStyle(color: Colors.grey, fontSize: 9)));
          },
        ),
      ),
      bottomTitles: AxisTitles(
        sideTitles: SideTitles(
          showTitles: true,
          interval: _selectedRange == 0 ? 4 : (_selectedRange == 1 ? 1 : 5),
          getTitlesWidget: (value, meta) => _getBottomTitles(value, meta, l10n),
        ),
      ),
    );
  }

  FlGridData _buildGridData() {
    return FlGridData(
      show: true,
      drawVerticalLine: false,
      horizontalInterval: (_getMaxY() - _getMinY()) / 5,
      // Динамічний крок сітки
      getDrawingHorizontalLine: (value) => FlLine(
        color: value == 0
            ? Colors.grey.withValues(alpha: 0.5)
            : Colors.grey.withValues(alpha: 0.1),
        strokeWidth: value == 0 ? 1.5 : 1,
      ),
    );
  }

  // ... (решта допоміжних методів: _buildLegend, _buildTimeSelector, _getBottomTitles, _getMaxY, _getMinY) ...
  // У методі _getMaxY обов'язково додайте перевірку:
  double _getMaxY() {
    var spots = _getVisibleSpots();
    if (spots.isEmpty) return 10.0;
    var max = spots.map((e) => e.y).reduce((a, b) => a > b ? a : b);
    return max == 0 ? 10.0 : max * 1.15;
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

  LineChartBarData _buildLineData(List<FlSpot> spots, Color color) {
    return LineChartBarData(
      spots: spots,
      isCurved: true,
      color: color,
      barWidth: 3,
      dotData: const FlDotData(show: false),
      belowBarData:
          BarAreaData(show: true, color: color.withValues(alpha: 0.1)),
    );
  }

  Widget _buildLegend(AppLocalizations l10n) {
    return Wrap(
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
        if (_selectedRange == 0) // Показувати кнопку тільки на денному графіку
          _buildLegendItem(
              Colors.amber.withValues(alpha: 0.6),
              'Прогноз (Сонце)',
              _showForecast,
              () => setState(() => _showForecast = !_showForecast))
      ],
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

  Widget _getBottomTitles(double value, TitleMeta meta, AppLocalizations l10n) {
    const style = TextStyle(color: Colors.grey, fontSize: 10);

    if (_selectedRange == 0) {
      // ДЕНЬ: показуємо години (кожні 4 години)
      if (value % 4 == 0) {
        return SideTitleWidget(
            meta: meta,
            space: 10,
            child: Text('${value.toInt()}:00', style: style));
      }
    } else if (_selectedRange == 1) {
      // МІСЯЦЬ: показуємо дні місяця (1, 5, 10, 15...)
      if (value % 5 == 0 || value == 1) {
        // Прибрано +1, бо парсер тепер сам ставить правильний день
        return SideTitleWidget(
            meta: meta,
            space: 10,
            child: Text('${value.toInt()}', style: style));
      }
    } else {
      // РІК: показуємо місяці (1, 2, 3...)
      return SideTitleWidget(
          meta: meta, space: 10, child: Text('${value.toInt()}', style: style));
    }
    return const SizedBox.shrink();
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

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../models/inverter_data.dart';

class EnergyFlowDiagram extends StatefulWidget {
  final InverterData data;

  const EnergyFlowDiagram({super.key, required this.data});

  @override
  State<EnergyFlowDiagram> createState() => _EnergyFlowDiagramState();
}

class _EnergyFlowDiagramState extends State<EnergyFlowDiagram>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller =
        AnimationController(vsync: this, duration: const Duration(seconds: 10))
          ..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
// 1. БЕЗПЕЧНЕ вилучення даних з кешу
    final fullConfigs =
        widget.data.rawFields['fullConfigs'] as Map<String, dynamic>?;

    // 2. Зчитуємо потоки (Siseli віддає потужність у кВт, тому множимо на 1000 для Вт)
    // Якщо fullConfigs ще null, плавно використовуємо старі дані, щоб графік не падав
    final double pvPower = fullConfigs != null
        ? (fullConfigs['pvFlow']?['value']?['value'] ?? 0.0).toDouble() * 1000
        : widget.data.pvPower.toDouble();

    final double loadPower = fullConfigs != null
        ? (fullConfigs['loadFlow']?['value']?['value'] ?? 0.0).toDouble() * 1000
        : widget.data.loadPower.toDouble();

    final double gridPower = fullConfigs != null
        ? (fullConfigs['gridFlow']?['value']?['value'] ?? 0.0).toDouble() * 1000
        : widget.data.gridPower.toDouble();

    final int batterySoc = fullConfigs != null
        ? (fullConfigs['batteryFlow']?['value']?['value'] ?? 0).toInt()
        : widget.data.batterySoc.toInt();

    final isGridImport = fullConfigs != null
        ? (fullConfigs['gridFlow']?['flowDirection'] == 1)
        : gridPower > 0;

    final isGridExport = fullConfigs != null
        ? (fullConfigs['gridFlow']?['flowDirection'] == 2)
        : false;

    // 1 - Заряд, 2 - Розряд (fallback на старі дані струму)
    final isBatCharging = fullConfigs != null
        ? (fullConfigs['batteryFlow']?['flowDirection'] == 1)
        : widget.data.batteryPower > 0;

    final isBatDischarging = fullConfigs != null
        ? (fullConfigs['batteryFlow']?['flowDirection'] == 2) // Додано дужки
        : widget.data.batteryPower < 0;

    return Container(
      height: 240,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: Theme.of(context).brightness == Brightness.dark
            ? [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.2), blurRadius: 20)
              ]
            : [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.05),
                    blurRadius: 15,
                    offset: const Offset(0, 5))
              ],
      ),
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) {
          return CustomPaint(
            // Передаємо безпечні змінні у Painter замість сирого widget.data
            painter: _FlowPainter(
              animationValue: _controller.value,
              pvPower: pvPower,
              gridPower: gridPower,
              loadPower: loadPower,
              isGridImport: isGridImport,
              isGridExport: isGridExport,
              isBatCharging: isBatCharging,
              isBatDischarging: isBatDischarging,
            ),
            child: _buildNodes(context, pvPower, loadPower, batterySoc,
                widget.data.gridVoltage.toDouble()),
          );
        },
      ),
    );
  }

  Widget _buildNodes(BuildContext context, double pvPower, double loadPower,
      int batterySoc, double gridVoltage) {
    final l10n = AppLocalizations.of(context)!;
    return Stack(
      children: [
        Align(
            alignment: Alignment.topLeft,
            child: _NodeWidget(
                icon: Icons.solar_power,
                color: Colors.amber,
                title: l10n.solar,
                value: '${pvPower.toStringAsFixed(0)} W')),
        // Безпечний PV
        Align(
            alignment: Alignment.topRight,
            child: _NodeWidget(
                icon: Icons.electric_bolt,
                color: Colors.blueAccent,
                title: l10n.grid,
                value: '${gridVoltage.toStringAsFixed(1)} V')),
        Align(
            alignment: Alignment.bottomLeft,
            child: _NodeWidget(
                icon: Icons.battery_charging_full,
                color: Colors.greenAccent,
                title: l10n.battery,
                value: '$batterySoc%')),
        // Безпечний SOC
        Align(
            alignment: Alignment.bottomRight,
            child: _NodeWidget(
                icon: Icons.home_rounded,
                color: Colors.purpleAccent,
                title: l10n.load,
                value: '${loadPower.toStringAsFixed(0)} W')),
        // Безпечне Навантаження

        // Центральний хаб
        Align(
          alignment: Alignment.center,
          child: Container(
            width: 60,
            height: 60,
            decoration: BoxDecoration(
              color: Theme.of(context).scaffoldBackgroundColor,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                    color: Colors.black.withValues(alpha: 0.1), blurRadius: 10)
              ],
            ),
            child: const Icon(Icons.swap_horiz, size: 32, color: Colors.grey),
          ),
        ),
      ],
    );
  }
}

class _NodeWidget extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String title;
  final String value;

  const _NodeWidget(
      {required this.icon,
      required this.color,
      required this.title,
      required this.value});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 85,
      height: 90,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            color.withValues(alpha: 0.15),
            color.withValues(alpha: 0.05)
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.3), width: 1.5),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: color, size: 24),
          const SizedBox(height: 6),
          Text(title, style: const TextStyle(fontSize: 10, color: Colors.grey)),
          Text(value,
              style: TextStyle(
                  fontSize: 13, fontWeight: FontWeight.bold, color: color)),
        ],
      ),
    );
  }
}

class _FlowPainter extends CustomPainter {
  final double animationValue;
  final double pvPower;
  final double gridPower;
  final double loadPower;
  final bool isGridImport;
  final bool isGridExport;
  final bool isBatCharging;
  final bool isBatDischarging;

  _FlowPainter({
    required this.animationValue,
    required this.pvPower,
    required this.gridPower,
    required this.loadPower,
    required this.isGridImport,
    required this.isGridExport,
    required this.isBatCharging,
    required this.isBatDischarging,
  });

  Path _createSPath(Offset start, Offset end) {
    final path = Path();
    path.moveTo(start.dx, start.dy);
    path.cubicTo(
      start.dx + (end.dx - start.dx) / 2,
      start.dy,
      start.dx + (end.dx - start.dx) / 2,
      end.dy,
      end.dx,
      end.dy,
    );
    return path;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final pvPos = const Offset(42, 45);
    final gridPos = Offset(size.width - 42, 45);
    final batPos = Offset(42, size.height - 45);
    final loadPos = Offset(size.width - 42, size.height - 45);

    final pvPath = _createSPath(pvPos, center);
    final gridPath = _createSPath(gridPos, center);
    final batPath = _createSPath(batPos, center);
    final loadPath = _createSPath(center, loadPos);
    final centerToBatPath = _createSPath(center, batPos);
    final centerToGridPath = _createSPath(center, gridPos);

    // Малюємо базові напівпрозорі лінії
    final linePaint = Paint()
      ..color = Colors.grey.withValues(alpha: 0.15)
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    canvas.drawPath(pvPath, linePaint);
    canvas.drawPath(gridPath, linePaint);
    canvas.drawPath(batPath, linePaint);
    canvas.drawPath(loadPath, linePaint);

    // Малюємо анімовані точки чітко за фізикою
    if (pvPower > 0) {
      _drawParticles(canvas, pvPath, Colors.amber, animationValue);
    }
    if (gridPower > 0) {
      if (isGridImport) {
        _drawParticles(canvas, gridPath, Colors.blueAccent, animationValue);
      } else if (isGridExport) {
        _drawParticles(
            canvas, centerToGridPath, Colors.blueAccent, animationValue);
      }
    }
    if (loadPower > 0) {
      _drawParticles(canvas, loadPath, Colors.purpleAccent, animationValue);
    }

    if (isBatCharging) {
      // Заряджається (Центр -> Батарея)
      _drawParticles(
          canvas, centerToBatPath, Colors.greenAccent, animationValue);
    } else if (isBatDischarging) {
      // Розряджається (Батарея -> Центр)
      _drawParticles(canvas, batPath, Colors.greenAccent, animationValue);
    }
  }

  void _drawParticles(
      Canvas canvas, Path path, Color color, double animationValue) {
    final metrics = path.computeMetrics().toList();
    if (metrics.isEmpty) return;
    final metric = metrics.first;
    final length = metric.length;

    final particlePaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 5);

    // Змінна для кількості частинок. Легко змінити на 4, 5 тощо.
    const particleCount = 3;

    for (var i = 0; i < particleCount; i++) {
      // Динамічний розрахунок відступів замість 0.33
      var progress = (animationValue + (i * (1.0 / particleCount))) % 1.0;

      final pos = metric.getTangentForOffset(length * progress)?.position;
      if (pos != null) {
        // Зверніть увагу: якщо у вас був інший радіус замість 4.0, залиште свій
        canvas.drawCircle(pos, 4.0, particlePaint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}

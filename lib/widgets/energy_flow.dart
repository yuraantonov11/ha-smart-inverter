import 'package:flutter/material.dart';
import '../models/inverter_data.dart';

class EnergyFlowDiagram extends StatefulWidget {
  final InverterData data;
  final bool isEn;

  const EnergyFlowDiagram({super.key, required this.data, this.isEn = true});

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
            painter: _FlowPainter(
                animationValue: _controller.value, data: widget.data),
            child: _buildNodes(
                widget.data, Theme.of(context).scaffoldBackgroundColor),
          );
        },
      ),
    );
  }

  Widget _buildNodes(InverterData data, Color centerColor) {
    return Stack(
      children: [
        Align(
            alignment: Alignment.topLeft,
            child: _NodeWidget(
                icon: Icons.solar_power,
                color: Colors.amber,
                title: widget.isEn ? 'Solar' : 'Сонце',
                value: '${data.pvPower.toStringAsFixed(0)} W')),
        Align(
            alignment: Alignment.topRight,
            child: _NodeWidget(
                icon: Icons.electric_bolt,
                color: Colors.blueAccent,
                title: widget.isEn ? 'Grid' : 'Мережа',
                value: '${data.gridVoltage.toStringAsFixed(1)} V')),
        Align(
            alignment: Alignment.bottomLeft,
            child: _NodeWidget(
                icon: Icons.battery_charging_full,
                color: Colors.greenAccent,
                title: widget.isEn ? 'Battery' : 'АКБ',
                value: '${data.batterySoc.toStringAsFixed(0)}%')),
        Align(
            alignment: Alignment.bottomRight,
            child: _NodeWidget(
                icon: Icons.home_rounded,
                color: Colors.purpleAccent,
                title: widget.isEn ? 'Load' : 'Будинок',
                value: '${data.loadPower.toStringAsFixed(0)} W')),
        Align(
          alignment: Alignment.center,
          child: Container(
            width: 64,
            height: 64,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: centerColor,
              border: Border.all(
                  color: Colors.amber.withValues(alpha: 0.4), width: 2),
              boxShadow: [
                BoxShadow(
                    color: Colors.amber.withValues(alpha: 0.15),
                    blurRadius: 20,
                    spreadRadius: 5),
              ],
            ),
            child: const Icon(Icons.developer_board,
                size: 28, color: Colors.amber),
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
  final InverterData data;

  _FlowPainter({required this.animationValue, required this.data});

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
    if (data.pvPower > 0) {
      _drawParticles(canvas, pvPath, Colors.amber, animationValue);
    }
    if (data.gridPower > 0) {
      _drawParticles(canvas, gridPath, Colors.blueAccent, animationValue);
    }
    if (data.loadPower > 0) {
      _drawParticles(canvas, loadPath, Colors.purpleAccent, animationValue);
    }

    if (data.batteryPower > 0) {
      // Заряджається (Центр -> Батарея)
      final chargePath = _createSPath(center, batPos);
      _drawParticles(canvas, chargePath, Colors.greenAccent, animationValue);
    } else if (data.batteryPower < 0) {
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

    for (var i = 0; i < 3; i++) {
      var progress = (animationValue + (i * 0.33)) % 1.0;
      final pos = metric.getTangentForOffset(length * progress)?.position;
      if (pos != null) {
        canvas.drawCircle(pos, 4, particlePaint); // Сяюча аура
        canvas.drawCircle(
            pos, 1.5, Paint()..color = Colors.white); // Біле ядро точки
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}

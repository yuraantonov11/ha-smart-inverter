import 'package:flutter/material.dart';
import '../models/inverter_data.dart';

class EnergyFlowDiagram extends StatefulWidget {
  final InverterData data;
  const EnergyFlowDiagram({super.key, required this.data});

  @override
  State<EnergyFlowDiagram> createState() => _EnergyFlowDiagramState();
}

class _EnergyFlowDiagramState extends State<EnergyFlowDiagram> with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(seconds: 2))..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 320,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(24),
        boxShadow: Theme.of(context).brightness == Brightness.dark
            ? []
            : [BoxShadow(color: Colors.black.withValues(alpha: 0.05), blurRadius: 15, offset: const Offset(0, 5))],
      ),
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, child) {
          return CustomPaint(
            painter: _FlowPainter(animationValue: _controller.value, data: widget.data),
            child: _buildNodes(widget.data, Theme.of(context).scaffoldBackgroundColor),
          );
        },
      ),
    );
  }

  Widget _buildNodes(InverterData data, Color centerColor) {
    return Stack(
      children: [
        Align(alignment: Alignment.topLeft, child: _NodeWidget(icon: Icons.solar_power, color: Colors.amber, title: "Сонце", value: "${data.pvPower.toStringAsFixed(0)} W")),
        Align(alignment: Alignment.topRight, child: _NodeWidget(icon: Icons.electric_bolt, color: Colors.blueAccent, title: "Мережа", value: "${data.gridVoltage.toStringAsFixed(1)} V")),
        Align(alignment: Alignment.bottomLeft, child: _NodeWidget(icon: Icons.battery_charging_full, color: Colors.greenAccent, title: "АКБ", value: "${data.batterySoc.toStringAsFixed(0)}%")),
        Align(alignment: Alignment.bottomRight, child: _NodeWidget(icon: Icons.home_rounded, color: Colors.purpleAccent, title: "Будинок", value: "${data.loadPower.toStringAsFixed(0)} W")),
        Align(
          alignment: Alignment.center,
          child: Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: centerColor,
              border: Border.all(color: Colors.grey.withValues(alpha: 0.2), width: 2),
            ),
            child: const Icon(Icons.sync_alt, size: 40, color: Colors.grey),
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

  const _NodeWidget({required this.icon, required this.color, required this.title, required this.value});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 90,
      height: 100,
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: color, size: 28),
          const SizedBox(height: 6),
          Text(title, style: const TextStyle(fontSize: 11, color: Colors.grey)),
          Text(value, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: color)),
        ],
      ),
    );
  }
}

class _FlowPainter extends CustomPainter {
  final double animationValue;
  final InverterData data;

  _FlowPainter({required this.animationValue, required this.data});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final pvPos = const Offset(45, 50);
    final gridPos = Offset(size.width - 45, 50);
    final batPos = Offset(45, size.height - 50);
    final loadPos = Offset(size.width - 45, size.height - 50);

    final linePaint = Paint()..color = Colors.grey.withValues(alpha: 0.1)..strokeWidth = 3..style = PaintingStyle.stroke;
    canvas.drawLine(pvPos, center, linePaint);
    canvas.drawLine(gridPos, center, linePaint);
    canvas.drawLine(batPos, center, linePaint);
    canvas.drawLine(center, loadPos, linePaint);

    if (data.pvPower > 0) _drawParticles(canvas, pvPos, center, Colors.amber);
    if (data.gridVoltage > 0) _drawParticles(canvas, gridPos, center, Colors.blueAccent);
    if (data.loadPower > 0) _drawParticles(canvas, center, loadPos, Colors.purpleAccent);

    if (data.batteryPower > 0) {
      _drawParticles(canvas, center, batPos, Colors.greenAccent);
    } else if (data.batteryPower < 0) {
      _drawParticles(canvas, batPos, center, Colors.greenAccent);
    }
  }

  void _drawParticles(Canvas canvas, Offset start, Offset end, Color color) {
    final particlePaint = Paint()..color = color..style = PaintingStyle.fill..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3);
    for (int i = 0; i < 3; i++) {
      double progress = (animationValue + (i * 0.33)) % 1.0;
      final x = start.dx + (end.dx - start.dx) * progress;
      final y = start.dy + (end.dy - start.dy) * progress;
      canvas.drawCircle(Offset(x, y), 4, particlePaint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}
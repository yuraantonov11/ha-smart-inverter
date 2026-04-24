import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';

import '../l10n/app_localizations.dart';
import '../models/inverter_data.dart';
import '../theme/app_theme.dart';

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
        AnimationController(vsync: this, duration: const Duration(seconds: 8))
          ..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final compact = MediaQuery.sizeOf(context).width < 430;
    final pvPower = widget.data.pvPower.toDouble();
    final loadPower = widget.data.loadPower.toDouble();
    final gridPower = widget.data.gridPower.toDouble();
    final batterySoc = widget.data.batterySoc.toInt();

    final isGridImport = gridPower > 30;
    final isGridExport = gridPower < -30;

    final isBatCharging = widget.data.batteryPower > 30;
    final isBatDischarging = widget.data.batteryPower < -30;

    final isDark = Theme.of(context).brightness == Brightness.dark;
    final base = isDark ? const Color(0xFF0D172B) : const Color(0xFFEFF5FC);

    if (!_controller.isAnimating) {
      _controller.repeat();
    }
    final animation = _controller;

    return ClipRRect(
      borderRadius: BorderRadius.circular(26),
      child: Stack(
        children: [
          Container(
            height: compact ? 252 : 280,
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  base.withValues(alpha: 0.94),
                  Theme.of(context)
                      .cardColor
                      .withValues(alpha: isDark ? 0.78 : 0.92),
                ],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(26),
              boxShadow: [
                BoxShadow(
                  color: (isDark ? Colors.black : Colors.blueGrey)
                      .withValues(alpha: isDark ? 0.35 : 0.14),
                  blurRadius: 26,
                  offset: const Offset(0, 12),
                ),
              ],
            ),
          ),
          if (!compact)
            Positioned.fill(
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
                child: const SizedBox.expand(),
              ),
            ),
          Positioned.fill(
            child: RepaintBoundary(
              child: AnimatedBuilder(
                animation: animation,
                child: _buildNodes(
                  context,
                  pvPower,
                  loadPower,
                  batterySoc,
                  gridPower.abs(),
                  compact: compact,
                ),
                builder: (context, child) {
                  return CustomPaint(
                    painter: _FlowPainter(
                      animationValue: animation.value,
                      pvPower: pvPower,
                      gridPower: gridPower.abs(),
                      loadPower: loadPower,
                      isGridImport: isGridImport,
                      isGridExport: isGridExport,
                      isBatCharging: isBatCharging,
                      isBatDischarging: isBatDischarging,
                      isDark: isDark,
                      reduceEffects: compact,
                    ),
                    child: child,
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNodes(BuildContext context, double pvPower, double loadPower,
      int batterySoc, double gridPower,
      {required bool compact}) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Stack(
        children: [
          Align(
            alignment: Alignment.topLeft,
            child: _NodeWidget(
              icon: Icons.solar_power_rounded,
              color: AppTheme.pvColor,
              title: l10n.solar,
              value: '${pvPower.toStringAsFixed(0)} W',
              compact: compact,
            ),
          ),
          Align(
            alignment: Alignment.topRight,
            child: _NodeWidget(
              icon: Icons.electric_bolt_rounded,
              color: AppTheme.gridColor,
              title: l10n.grid,
              value: '${gridPower.toStringAsFixed(0)} W',
              compact: compact,
            ),
          ),
          Align(
            alignment: Alignment.bottomLeft,
            child: _NodeWidget(
              icon: Icons.battery_charging_full_rounded,
              color: AppTheme.batteryColor,
              title: l10n.battery,
              value: '$batterySoc%',
              compact: compact,
            ),
          ),
          Align(
            alignment: Alignment.bottomRight,
            child: _NodeWidget(
              icon: Icons.home_rounded,
              color: AppTheme.loadColor,
              title: l10n.load,
              value: '${loadPower.toStringAsFixed(0)} W',
              compact: compact,
            ),
          ),
          Align(
            alignment: Alignment.center,
            child: _CoreHub(animation: _controller, compact: compact),
          ),
        ],
      ),
    );
  }
}

class _CoreHub extends StatelessWidget {
  final AnimationController animation;
  final bool compact;

  const _CoreHub({required this.animation, required this.compact});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return AnimatedBuilder(
      animation: animation,
      builder: (context, child) {
        final t = (math.sin(animation.value * math.pi * 2) + 1) / 2;
        return Container(
          width: compact ? 64 : 78,
          height: compact ? 64 : 78,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(
              colors: [
                Colors.white.withValues(alpha: isDark ? 0.18 : 0.72),
                Theme.of(context)
                    .cardColor
                    .withValues(alpha: isDark ? 0.74 : 0.96),
              ],
            ),
            border: Border.all(
              color: AppTheme.gridColor.withValues(alpha: 0.36 + t * 0.4),
            ),
            boxShadow: [
              BoxShadow(
                color: AppTheme.gridColor.withValues(alpha: 0.18 + t * 0.18),
                blurRadius: 24,
                spreadRadius: 2,
              ),
            ],
          ),
          child: Icon(
            Icons.hub_rounded,
            size: compact ? 28 : 34,
            color: isDark ? Colors.white70 : const Color(0xFF1A2A42),
          ),
        );
      },
    );
  }
}

class _NodeWidget extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String title;
  final String value;
  final bool compact;

  const _NodeWidget({
    required this.icon,
    required this.color,
    required this.title,
    required this.value,
    required this.compact,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
        child: Container(
          width: compact ? 94 : 108,
          height: compact ? 84 : 96,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                color.withValues(alpha: isDark ? 0.22 : 0.22),
                Theme.of(context)
                    .cardColor
                    .withValues(alpha: isDark ? 0.44 : 0.94),
              ],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(
              color: color.withValues(alpha: isDark ? 0.5 : 0.62),
              width: 1.1,
            ),
            boxShadow: [
              BoxShadow(
                color: color.withValues(alpha: 0.22),
                blurRadius: 20,
                spreadRadius: -1,
              ),
            ],
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, color: color, size: 24),
              SizedBox(height: compact ? 3 : 5),
              Text(
                title,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      fontSize: compact ? 10 : 11,
                      color: Theme.of(context)
                          .textTheme
                          .bodySmall
                          ?.color
                          ?.withValues(alpha: 0.95),
                    ),
              ),
              const SizedBox(height: 2),
              Text(
                value,
                style: TextStyle(
                  fontSize: compact ? 12 : 13,
                  fontWeight: FontWeight.w700,
                  color: isDark ? color : color.withValues(alpha: 0.9),
                ),
              ),
            ],
          ),
        ),
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
  final bool isDark;
  final bool reduceEffects;

  _FlowPainter({
    required this.animationValue,
    required this.pvPower,
    required this.gridPower,
    required this.loadPower,
    required this.isGridImport,
    required this.isGridExport,
    required this.isBatCharging,
    required this.isBatDischarging,
    required this.isDark,
    required this.reduceEffects,
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
    final pvPos = const Offset(64, 56);
    final gridPos = Offset(size.width - 64, 56);
    final batPos = Offset(64, size.height - 56);
    final loadPos = Offset(size.width - 64, size.height - 56);

    final pvPath = _createSPath(pvPos, center);
    final gridPath = _createSPath(gridPos, center);
    final batPath = _createSPath(batPos, center);
    final loadPath = _createSPath(center, loadPos);
    final centerToBatPath = _createSPath(center, batPos);
    final centerToGridPath = _createSPath(center, gridPos);

    _drawBaseLine(canvas, pvPath);
    _drawBaseLine(canvas, gridPath);
    _drawBaseLine(canvas, batPath);
    _drawBaseLine(canvas, loadPath);

    if (pvPower > 0) {
      _drawEnergy(canvas, pvPath, AppTheme.pvColor, _intensity(pvPower));
    }

    if (gridPower > 0) {
      if (isGridImport) {
        _drawEnergy(
            canvas, gridPath, AppTheme.gridColor, _intensity(gridPower));
      } else if (isGridExport) {
        _drawEnergy(
          canvas,
          centerToGridPath,
          AppTheme.gridColor,
          _intensity(gridPower),
        );
      }
    }

    if (loadPower > 0) {
      _drawEnergy(canvas, loadPath, AppTheme.loadColor, _intensity(loadPower));
    }

    if (isBatCharging) {
      _drawEnergy(canvas, centerToBatPath, AppTheme.batteryColor, 0.72);
    } else if (isBatDischarging) {
      _drawEnergy(canvas, batPath, AppTheme.batteryColor, 0.86);
    }
  }

  void _drawBaseLine(Canvas canvas, Path path) {
    final linePaint = Paint()
      ..color = (isDark ? Colors.white : const Color(0xFF6B7F97))
          .withValues(alpha: isDark ? 0.1 : 0.24)
      ..strokeWidth = 2.8
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    canvas.drawPath(path, linePaint);
  }

  double _intensity(double watts) {
    return (watts / 4000).clamp(0.22, 1.0);
  }

  void _drawEnergy(Canvas canvas, Path path, Color color, double intensity) {
    final metrics = path.computeMetrics().toList();
    if (metrics.isEmpty) return;

    final metric = metrics.first;
    final segment = metric.extractPath(0, metric.length);

    final glow = Paint()
      ..shader = LinearGradient(
        colors: [
          color.withValues(alpha: isDark ? 0.08 : 0.12),
          color.withValues(alpha: (isDark ? 0.22 : 0.28) + intensity * 0.24),
          color.withValues(alpha: isDark ? 0.08 : 0.12),
        ],
      ).createShader(segment.getBounds())
      ..strokeWidth =
          (reduceEffects ? 3.2 : 4) + intensity * (reduceEffects ? 1.8 : 3)
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..maskFilter = reduceEffects
          ? const MaskFilter.blur(BlurStyle.normal, 3)
          : const MaskFilter.blur(BlurStyle.normal, 6);

    canvas.drawPath(segment, glow);
    if (!reduceEffects) {
      _drawParticles(canvas, metric, color, intensity);
    }
  }

  void _drawParticles(
    Canvas canvas,
    PathMetric metric,
    Color color,
    double intensity,
  ) {
    final particleCount = 2 + (intensity * 3).round();
    final speed = 0.55 + intensity * 1.1;

    final particlePaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, 4 + intensity * 3);

    for (var i = 0; i < particleCount; i++) {
      final progress =
          ((animationValue * speed) + (i * (1.0 / particleCount))) % 1.0;
      final tangent = metric.getTangentForOffset(metric.length * progress);
      if (tangent == null) continue;

      final pulse = 0.7 + 0.3 * math.sin((animationValue + i) * math.pi * 2);
      canvas.drawCircle(
        tangent.position,
        2.6 + intensity * 2.4 * pulse,
        particlePaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _FlowPainter oldDelegate) {
    return oldDelegate.animationValue != animationValue ||
        oldDelegate.pvPower != pvPower ||
        oldDelegate.gridPower != gridPower ||
        oldDelegate.loadPower != loadPower ||
        oldDelegate.isGridImport != isGridImport ||
        oldDelegate.isGridExport != isGridExport ||
        oldDelegate.isBatCharging != isBatCharging ||
        oldDelegate.isBatDischarging != isBatDischarging ||
        oldDelegate.isDark != isDark ||
        oldDelegate.reduceEffects != reduceEffects;
  }
}

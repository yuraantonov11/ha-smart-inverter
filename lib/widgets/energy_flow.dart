import 'dart:async';
import 'dart:io' show Platform;
import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../l10n/app_localizations.dart';
import '../models/inverter_data.dart';
import '../theme/app_theme.dart';

/// True when BackdropFilter / ImageFilter.blur should be disabled.
/// Disabled on mobile (low GPU bandwidth) and Windows (EGL_CONTEXT_LOST 12302).
bool get _isMobilePlatform =>
    !kIsWeb && (Platform.isAndroid || Platform.isIOS || Platform.isWindows);

class EnergyFlowDiagram extends StatefulWidget {
  final InverterData data;
  final bool showInteractiveToolbar;
  final bool autofocusShortcuts;

  const EnergyFlowDiagram({
    super.key,
    required this.data,
    this.showInteractiveToolbar = false,
    this.autofocusShortcuts = false,
  });

  @override
  State<EnergyFlowDiagram> createState() => _EnergyFlowDiagramState();
}

class _FlowSnapshot {
  final double pvPowerW;
  final double loadPowerW;
  final double gridImportW;
  final double gridExportW;
  final double batteryChargeW;
  final double batteryDischargeW;
  final double solarToLoadW;
  final double solarToBatteryW;
  final double solarToGridW;
  final double batteryToLoadW;
  final double gridToLoadW;
  final double gridToBatteryW;
  final double balanceErrorW;

  const _FlowSnapshot({
    required this.pvPowerW,
    required this.loadPowerW,
    required this.gridImportW,
    required this.gridExportW,
    required this.batteryChargeW,
    required this.batteryDischargeW,
    required this.solarToLoadW,
    required this.solarToBatteryW,
    required this.solarToGridW,
    required this.batteryToLoadW,
    required this.gridToLoadW,
    required this.gridToBatteryW,
    required this.balanceErrorW,
  });

  factory _FlowSnapshot.fromData(InverterData data) {
    final pv = data.pvPower.clamp(0.0, 50000.0).toDouble();
    final load = data.loadPower.clamp(0.0, 50000.0).toDouble();
    final gridImport = math.max(data.gridPower, 0.0).clamp(0.0, 50000.0);
    final gridExport = math.max(-data.gridPower, 0.0).clamp(0.0, 50000.0);
    final batteryCharge = math.max(data.batteryPower, 0.0).clamp(0.0, 50000.0);
    final batteryDischarge =
        math.max(-data.batteryPower, 0.0).clamp(0.0, 50000.0);

    final solarToLoad = math.min(pv, load);
    final remainingLoadAfterSolar = math.max(load - solarToLoad, 0.0);
    final batteryToLoad = math.min(batteryDischarge, remainingLoadAfterSolar);
    final remainingLoad =
        math.max(remainingLoadAfterSolar - batteryToLoad, 0.0);
    final gridToLoad = math.min(gridImport, remainingLoad);

    final solarExcess = math.max(pv - solarToLoad, 0.0);
    final solarToBattery = math.min(solarExcess, batteryCharge);
    final solarToGrid =
        math.max(solarExcess - solarToBattery, 0.0) + gridExport;
    final gridToBattery = math.max(gridImport - gridToLoad, 0.0);

    final balanceError = (pv + gridImport + batteryDischarge) -
        (load + batteryCharge + gridExport);

    return _FlowSnapshot(
      pvPowerW: pv,
      loadPowerW: load,
      gridImportW: gridImport,
      gridExportW: gridExport,
      batteryChargeW: batteryCharge,
      batteryDischargeW: batteryDischarge,
      solarToLoadW: solarToLoad,
      solarToBatteryW: solarToBattery,
      solarToGridW: solarToGrid,
      batteryToLoadW: batteryToLoad,
      gridToLoadW: gridToLoad,
      gridToBatteryW: gridToBattery,
      balanceErrorW: balanceError,
    );
  }

  double get batteryNetPowerW => batteryChargeW - batteryDischargeW;
  bool get balanceStable => balanceErrorW.abs() <= 120.0;
}

enum _InteractiveFlowLink {
  solarToLoad,
  solarToBattery,
  batteryToLoad,
  gridToLoad,
  gridToBattery,
  solarToGrid,
}

class _EnergyFlowDiagramState extends State<EnergyFlowDiagram>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  _InteractiveFlowLink? _selectedLink;
  late final FocusNode _focusNode;
  Timer? _demoTimer;
  bool _isAutoDemoEnabled = false;
  int _demoIndex = 0;
  bool _isSelectionPinned = false;

  static const List<_InteractiveFlowLink> _demoOrder = [
    _InteractiveFlowLink.solarToLoad,
    _InteractiveFlowLink.solarToBattery,
    _InteractiveFlowLink.batteryToLoad,
    _InteractiveFlowLink.gridToLoad,
    _InteractiveFlowLink.gridToBattery,
    _InteractiveFlowLink.solarToGrid,
  ];

  @override
  void initState() {
    super.initState();
    _focusNode = FocusNode(debugLabel: 'energy-flow-shortcuts');
    _controller =
        AnimationController(vsync: this, duration: const Duration(seconds: 8))
          ..repeat();
    if (widget.autofocusShortcuts) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          _focusNode.requestFocus();
        }
      });
    }
  }

  @override
  void dispose() {
    _demoTimer?.cancel();
    _focusNode.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final compact = MediaQuery.sizeOf(context).width < 430;
    final snapshot = _FlowSnapshot.fromData(widget.data);
    final batterySoc = widget.data.batterySoc.toInt();

    final isDark = Theme.of(context).brightness == Brightness.dark;
    final base = isDark ? const Color(0xFF0D172B) : const Color(0xFFF4F8FC);

    if (!_controller.isAnimating) {
      _controller.repeat();
    }
    final animation = _controller;

    // On mobile platforms disable heavy blur effects to keep rendering smooth.
    final enableBlur = !_isMobilePlatform;
    final selectedLink =
        _isLinkVisible(snapshot, _selectedLink) ? _selectedLink : null;

    return Shortcuts(
      shortcuts: <ShortcutActivator, Intent>{
        SingleActivator(LogicalKeyboardKey.arrowRight): const _NextFlowIntent(),
        SingleActivator(LogicalKeyboardKey.arrowLeft): const _PrevFlowIntent(),
        SingleActivator(LogicalKeyboardKey.escape): const _ClearFlowIntent(),
        SingleActivator(LogicalKeyboardKey.space): const _ToggleDemoIntent(),
      },
      child: Actions(
        actions: <Type, Action<Intent>>{
          _NextFlowIntent: CallbackAction<_NextFlowIntent>(
            onInvoke: (_) {
              _cycleSelection(snapshot, 1);
              return null;
            },
          ),
          _PrevFlowIntent: CallbackAction<_PrevFlowIntent>(
            onInvoke: (_) {
              _cycleSelection(snapshot, -1);
              return null;
            },
          ),
          _ClearFlowIntent: CallbackAction<_ClearFlowIntent>(
            onInvoke: (_) {
              _disableDemo();
              setState(() => _selectedLink = null);
              return null;
            },
          ),
          _ToggleDemoIntent: CallbackAction<_ToggleDemoIntent>(
            onInvoke: (_) {
              _toggleDemo(snapshot);
              return null;
            },
          ),
        },
        child: Focus(
          autofocus: widget.autofocusShortcuts,
          focusNode: _focusNode,
          child: Column(
            children: [
              ClipRRect(
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
                        boxShadow: enableBlur
                            ? [
                                BoxShadow(
                                  color: (isDark
                                          ? Colors.black
                                          : Colors.blueGrey)
                                      .withValues(alpha: isDark ? 0.35 : 0.1),
                                  blurRadius: 24,
                                  offset: const Offset(0, 8),
                                ),
                              ]
                            : null,
                      ),
                    ),
                    if (enableBlur && !compact)
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
                            snapshot,
                            batterySoc,
                            selectedLink,
                            compact: compact,
                            enableBlur: enableBlur,
                          ),
                          builder: (context, child) {
                            return CustomPaint(
                              painter: _FlowPainter(
                                animationValue: animation.value,
                                pvPower: snapshot.pvPowerW,
                                gridImportPower: snapshot.gridImportW,
                                gridExportPower: snapshot.gridExportW,
                                batteryChargePower: snapshot.batteryChargeW,
                                batteryDischargePower:
                                    snapshot.batteryDischargeW,
                                loadPower: snapshot.loadPowerW,
                                selectedLink: selectedLink,
                                isDark: isDark,
                                // Reduce extra blur strokes on mobile but keep particles.
                                reduceEffects: !enableBlur,
                              ),
                              child: child,
                            );
                          },
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),
              _FlowLegendBar(
                snapshot: snapshot,
                selectedLink: selectedLink,
                onSelectLink: (link) {
                  if (_isSelectionPinned) return;
                  _disableDemo();
                  setState(() {
                    _selectedLink = _selectedLink == link ? null : link;
                    if (_selectedLink == null) {
                      _isSelectionPinned = false;
                    }
                  });
                },
              ),
              if (widget.showInteractiveToolbar) ...[
                const SizedBox(height: 10),
                _buildInteractiveToolbar(context, snapshot),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildInteractiveToolbar(
      BuildContext context, _FlowSnapshot snapshot) {
    final theme = Theme.of(context);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        FilledButton.tonalIcon(
          onPressed: () => _toggleDemo(snapshot),
          icon: Icon(_isAutoDemoEnabled
              ? Icons.pause_circle_outline_rounded
              : Icons.play_circle_outline_rounded),
          label: Text(_isAutoDemoEnabled ? 'Auto demo: ON' : 'Auto demo: OFF'),
        ),
        OutlinedButton.icon(
          onPressed: () => _cycleSelection(snapshot, 1),
          icon: const Icon(Icons.chevron_right_rounded),
          label: const Text('Next flow'),
        ),
        OutlinedButton.icon(
          onPressed: () {
            if (_selectedLink == null) return;
            _disableDemo();
            setState(() {
              _isSelectionPinned = !_isSelectionPinned;
            });
          },
          icon: Icon(_isSelectionPinned
              ? Icons.push_pin_rounded
              : Icons.push_pin_outlined),
          label: Text(_isSelectionPinned ? 'Pinned' : 'Pin selection'),
        ),
        OutlinedButton.icon(
          onPressed: () {
            _disableDemo();
            setState(() {
              _selectedLink = null;
              _isSelectionPinned = false;
            });
          },
          icon: const Icon(Icons.clear_rounded),
          label: const Text('Reset'),
        ),
        Text(
          'Shortcuts: <-  ->  Space  Esc',
          style: theme.textTheme.labelSmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ],
    );
  }

  Widget _buildNodes(BuildContext context, _FlowSnapshot snapshot,
      int batterySoc, _InteractiveFlowLink? selectedLink,
      {required bool compact, required bool enableBlur}) {
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
              value: '${snapshot.pvPowerW.toStringAsFixed(0)} W',
              selected: selectedLink == _InteractiveFlowLink.solarToLoad ||
                  selectedLink == _InteractiveFlowLink.solarToBattery ||
                  selectedLink == _InteractiveFlowLink.solarToGrid,
              onTap: () => _selectFirstVisible(snapshot, const [
                _InteractiveFlowLink.solarToLoad,
                _InteractiveFlowLink.solarToBattery,
                _InteractiveFlowLink.solarToGrid,
              ]),
              compact: compact,
              enableBlur: enableBlur,
            ),
          ),
          Align(
            alignment: Alignment.topRight,
            child: _NodeWidget(
              icon: Icons.electric_bolt_rounded,
              color: AppTheme.gridColor,
              title: l10n.grid,
              value: _gridNodeLabel(snapshot),
              selected: selectedLink == _InteractiveFlowLink.gridToLoad ||
                  selectedLink == _InteractiveFlowLink.gridToBattery ||
                  selectedLink == _InteractiveFlowLink.solarToGrid,
              onTap: () => _selectFirstVisible(snapshot, const [
                _InteractiveFlowLink.gridToLoad,
                _InteractiveFlowLink.gridToBattery,
                _InteractiveFlowLink.solarToGrid,
              ]),
              compact: compact,
              enableBlur: enableBlur,
            ),
          ),
          Align(
            alignment: Alignment.bottomLeft,
            child: _NodeWidget(
              icon: Icons.battery_charging_full_rounded,
              color: AppTheme.batteryColor,
              title: l10n.battery,
              value: '$batterySoc% ${_batteryNodeLabel(snapshot)}',
              selected: selectedLink == _InteractiveFlowLink.solarToBattery ||
                  selectedLink == _InteractiveFlowLink.gridToBattery ||
                  selectedLink == _InteractiveFlowLink.batteryToLoad,
              onTap: () => _selectFirstVisible(snapshot, const [
                _InteractiveFlowLink.batteryToLoad,
                _InteractiveFlowLink.solarToBattery,
                _InteractiveFlowLink.gridToBattery,
              ]),
              compact: compact,
              enableBlur: enableBlur,
            ),
          ),
          Align(
            alignment: Alignment.bottomRight,
            child: _NodeWidget(
              icon: Icons.home_rounded,
              color: AppTheme.loadColor,
              title: l10n.load,
              value: '${snapshot.loadPowerW.toStringAsFixed(0)} W',
              selected: selectedLink == _InteractiveFlowLink.solarToLoad ||
                  selectedLink == _InteractiveFlowLink.batteryToLoad ||
                  selectedLink == _InteractiveFlowLink.gridToLoad,
              onTap: () => _selectFirstVisible(snapshot, const [
                _InteractiveFlowLink.solarToLoad,
                _InteractiveFlowLink.batteryToLoad,
                _InteractiveFlowLink.gridToLoad,
              ]),
              compact: compact,
              enableBlur: enableBlur,
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

  String _gridNodeLabel(_FlowSnapshot snapshot) {
    if (snapshot.gridImportW > 25.0) {
      return 'IN ${snapshot.gridImportW.toStringAsFixed(0)} W';
    }
    if (snapshot.gridExportW > 25.0) {
      return 'OUT ${snapshot.gridExportW.toStringAsFixed(0)} W';
    }
    return '0 W';
  }

  String _batteryNodeLabel(_FlowSnapshot snapshot) {
    if (snapshot.batteryChargeW > 25.0) {
      return '↑${snapshot.batteryChargeW.toStringAsFixed(0)}W';
    }
    if (snapshot.batteryDischargeW > 25.0) {
      return '↓${snapshot.batteryDischargeW.toStringAsFixed(0)}W';
    }
    return '•0W';
  }

  bool _isLinkVisible(_FlowSnapshot snapshot, _InteractiveFlowLink? link) {
    if (link == null) return true;
    const threshold = 20.0;
    return switch (link) {
      _InteractiveFlowLink.solarToLoad => snapshot.solarToLoadW > threshold,
      _InteractiveFlowLink.solarToBattery =>
        snapshot.solarToBatteryW > threshold,
      _InteractiveFlowLink.batteryToLoad => snapshot.batteryToLoadW > threshold,
      _InteractiveFlowLink.gridToLoad => snapshot.gridToLoadW > threshold,
      _InteractiveFlowLink.gridToBattery => snapshot.gridToBatteryW > threshold,
      _InteractiveFlowLink.solarToGrid => snapshot.solarToGridW > threshold,
    };
  }

  void _selectFirstVisible(
    _FlowSnapshot snapshot,
    List<_InteractiveFlowLink> candidates,
  ) {
    if (_isSelectionPinned) return;
    _disableDemo();
    final target = candidates.firstWhere(
      (link) => _isLinkVisible(snapshot, link),
      orElse: () => candidates.first,
    );
    setState(() {
      _selectedLink = _selectedLink == target ? null : target;
    });
  }

  void _toggleDemo(_FlowSnapshot snapshot) {
    if (_isSelectionPinned) return;
    if (_isAutoDemoEnabled) {
      _disableDemo();
      return;
    }
    final visible = _visibleLinks(snapshot);
    if (visible.isEmpty) return;
    _demoTimer?.cancel();
    setState(() {
      _isAutoDemoEnabled = true;
      _demoIndex = 0;
      _selectedLink = visible.first;
    });
    _demoTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (!mounted) return;
      final liveVisible = _visibleLinks(_FlowSnapshot.fromData(widget.data));
      if (liveVisible.isEmpty) {
        _disableDemo();
        return;
      }
      _demoIndex = (_demoIndex + 1) % liveVisible.length;
      setState(() {
        _selectedLink = liveVisible[_demoIndex];
      });
    });
  }

  void _disableDemo() {
    if (!_isAutoDemoEnabled && _demoTimer == null) return;
    _demoTimer?.cancel();
    _demoTimer = null;
    if (mounted) {
      setState(() {
        _isAutoDemoEnabled = false;
      });
    }
  }

  List<_InteractiveFlowLink> _visibleLinks(_FlowSnapshot snapshot) {
    return _demoOrder.where((l) => _isLinkVisible(snapshot, l)).toList();
  }

  void _cycleSelection(_FlowSnapshot snapshot, int direction) {
    if (_isSelectionPinned) return;
    _disableDemo();
    final visible = _visibleLinks(snapshot);
    if (visible.isEmpty) {
      setState(() => _selectedLink = null);
      return;
    }
    final currentIndex =
        _selectedLink == null ? -1 : visible.indexOf(_selectedLink!);
    final nextIndex = currentIndex < 0
        ? 0
        : (currentIndex + direction + visible.length) % visible.length;
    setState(() {
      _selectedLink = visible[nextIndex];
    });
  }
}

class _NextFlowIntent extends Intent {
  const _NextFlowIntent();
}

class _PrevFlowIntent extends Intent {
  const _PrevFlowIntent();
}

class _ClearFlowIntent extends Intent {
  const _ClearFlowIntent();
}

class _ToggleDemoIntent extends Intent {
  const _ToggleDemoIntent();
}

class _FlowLegendBar extends StatelessWidget {
  final _FlowSnapshot snapshot;
  final _InteractiveFlowLink? selectedLink;
  final ValueChanged<_InteractiveFlowLink> onSelectLink;

  const _FlowLegendBar({
    required this.snapshot,
    required this.selectedLink,
    required this.onSelectLink,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    const threshold = 20.0;

    final entries = <({
      IconData icon,
      Color color,
      String text,
      double power,
      _InteractiveFlowLink link,
    })>[
      (
        icon: Icons.solar_power_rounded,
        color: AppTheme.pvColor,
        text: '${l10n.solar} -> ${l10n.load}',
        power: snapshot.solarToLoadW,
        link: _InteractiveFlowLink.solarToLoad,
      ),
      (
        icon: Icons.battery_charging_full_rounded,
        color: AppTheme.batteryColor,
        text: '${l10n.solar} -> ${l10n.battery}',
        power: snapshot.solarToBatteryW,
        link: _InteractiveFlowLink.solarToBattery,
      ),
      (
        icon: Icons.home_rounded,
        color: AppTheme.loadColor,
        text: '${l10n.battery} -> ${l10n.load}',
        power: snapshot.batteryToLoadW,
        link: _InteractiveFlowLink.batteryToLoad,
      ),
      (
        icon: Icons.electric_bolt_rounded,
        color: AppTheme.gridColor,
        text: '${l10n.grid} -> ${l10n.load}',
        power: snapshot.gridToLoadW,
        link: _InteractiveFlowLink.gridToLoad,
      ),
      (
        icon: Icons.battery_full_rounded,
        color: AppTheme.gridColor,
        text: '${l10n.grid} -> ${l10n.battery}',
        power: snapshot.gridToBatteryW,
        link: _InteractiveFlowLink.gridToBattery,
      ),
      (
        icon: Icons.upload_rounded,
        color: AppTheme.gridColor,
        text: '${l10n.solar} -> ${l10n.grid}',
        power: snapshot.solarToGridW,
        link: _InteractiveFlowLink.solarToGrid,
      ),
    ];

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final e in entries)
          if (e.power > threshold)
            _FlowLegendChip(
              icon: e.icon,
              color: e.color,
              label: '${e.text}: ${e.power.toStringAsFixed(0)} W',
              selected: selectedLink == e.link,
              onTap: () => onSelectLink(e.link),
            ),
        _FlowLegendChip(
          icon: snapshot.balanceStable
              ? Icons.check_circle_rounded
              : Icons.warning_amber_rounded,
          color: snapshot.balanceStable
              ? const Color(0xFF10B981)
              : theme.colorScheme.error,
          label: 'Δ ${snapshot.balanceErrorW.abs().toStringAsFixed(0)} W',
        ),
      ],
    );
  }
}

class _FlowLegendChip extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  const _FlowLegendChip({
    required this.icon,
    required this.color,
    required this.label,
    this.selected = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: selected
                ? color.withValues(alpha: 0.18)
                : theme.colorScheme.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(999),
            border: selected
                ? Border.all(color: color.withValues(alpha: 0.65))
                : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: color),
              const SizedBox(width: 6),
              Text(
                label,
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
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
                blurRadius: isDark ? 24 : 14,
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
  final bool enableBlur;
  final bool selected;
  final VoidCallback? onTap;

  const _NodeWidget({
    required this.icon,
    required this.color,
    required this.title,
    required this.value,
    required this.compact,
    required this.enableBlur,
    this.selected = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    final inner = Container(
      width: compact ? 94 : 108,
      height: compact ? 84 : 96,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            color.withValues(alpha: isDark ? 0.22 : 0.22),
            Theme.of(context).cardColor.withValues(alpha: isDark ? 0.44 : 0.94),
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(
          color: selected
              ? color.withValues(alpha: 0.9)
              : color.withValues(alpha: isDark ? 0.5 : 0.62),
          width: selected ? 1.6 : 1.1,
        ),
        boxShadow: enableBlur
            ? [
                BoxShadow(
                  color: color.withValues(alpha: isDark ? 0.22 : 0.16),
                  blurRadius: isDark ? 20 : 14,
                  spreadRadius: -1,
                ),
              ]
            : null,
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: color, size: 24),
          SizedBox(height: compact ? 2 : 4),
          Flexible(
            child: Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    fontSize: compact ? 9 : 10,
                    color: Theme.of(context)
                        .textTheme
                        .bodySmall
                        ?.color
                        ?.withValues(alpha: 0.95),
                  ),
            ),
          ),
          const SizedBox(height: 1),
          Flexible(
            child: Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: compact ? 11 : 12,
                fontWeight: FontWeight.w700,
                color: isDark ? color : color.withValues(alpha: 0.9),
              ),
            ),
          ),
        ],
      ),
    );

    if (!enableBlur) {
      return Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(18),
          onTap: onTap,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(18),
            child: inner,
          ),
        ),
      );
    }

    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(18),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
            child: inner,
          ),
        ),
      ),
    );
  }
}

class _FlowPainter extends CustomPainter {
  final double animationValue;
  final double pvPower;
  final double gridImportPower;
  final double gridExportPower;
  final double batteryChargePower;
  final double batteryDischargePower;
  final double loadPower;
  final _InteractiveFlowLink? selectedLink;
  final bool isDark;
  final bool reduceEffects;

  _FlowPainter({
    required this.animationValue,
    required this.pvPower,
    required this.gridImportPower,
    required this.gridExportPower,
    required this.batteryChargePower,
    required this.batteryDischargePower,
    required this.loadPower,
    required this.selectedLink,
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
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.solarToLoad ||
          selectedLink == _InteractiveFlowLink.solarToBattery ||
          selectedLink == _InteractiveFlowLink.solarToGrid;
      _drawEnergy(
        canvas,
        pvPath,
        AppTheme.pvColor,
        _scaledIntensity(_intensity(pvPower), selected),
      );
    }

    if (gridImportPower > 25.0) {
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.gridToLoad ||
          selectedLink == _InteractiveFlowLink.gridToBattery;
      _drawEnergy(canvas, gridPath, AppTheme.gridColor,
          _scaledIntensity(_intensity(gridImportPower), selected));
    }

    if (gridExportPower > 25.0) {
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.solarToGrid;
      _drawEnergy(
        canvas,
        centerToGridPath,
        AppTheme.gridColor,
        _scaledIntensity(_intensity(gridExportPower), selected),
      );
    }

    if (loadPower > 0) {
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.solarToLoad ||
          selectedLink == _InteractiveFlowLink.batteryToLoad ||
          selectedLink == _InteractiveFlowLink.gridToLoad;
      _drawEnergy(canvas, loadPath, AppTheme.loadColor,
          _scaledIntensity(_intensity(loadPower), selected));
    }

    if (batteryChargePower > 25.0) {
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.solarToBattery ||
          selectedLink == _InteractiveFlowLink.gridToBattery;
      _drawEnergy(
        canvas,
        centerToBatPath,
        AppTheme.batteryColor,
        _scaledIntensity(_intensity(batteryChargePower), selected),
      );
    } else if (batteryDischargePower > 25.0) {
      final selected = selectedLink == null ||
          selectedLink == _InteractiveFlowLink.batteryToLoad;
      _drawEnergy(
        canvas,
        batPath,
        AppTheme.batteryColor,
        _scaledIntensity(_intensity(batteryDischargePower), selected),
      );
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

  double _scaledIntensity(double intensity, bool selected) {
    if (selectedLink == null) return intensity;
    final scale = selected ? 1.18 : 0.26;
    return (intensity * scale).clamp(0.08, 1.0);
  }

  void _drawEnergy(Canvas canvas, Path path, Color color, double intensity) {
    final metrics = path.computeMetrics().toList();
    if (metrics.isEmpty) return;

    final metric = metrics.first;
    final segment = metric.extractPath(0, metric.length);

    final glow = Paint()
      ..shader = LinearGradient(
        colors: [
          color.withValues(alpha: isDark ? 0.08 : 0.08),
          color.withValues(
              alpha:
                  (isDark ? 0.22 : 0.2) + intensity * (isDark ? 0.24 : 0.14)),
          color.withValues(alpha: isDark ? 0.08 : 0.08),
        ],
      ).createShader(segment.getBounds())
      ..strokeWidth =
          (reduceEffects ? 3.2 : 4) + intensity * (reduceEffects ? 1.8 : 3)
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..maskFilter = reduceEffects
          ? const MaskFilter.blur(BlurStyle.normal, 3)
          : MaskFilter.blur(BlurStyle.normal, isDark ? 6 : 3);

    canvas.drawPath(segment, glow);
    // Always draw particles; on mobile reduceEffects only limits blur/stroke.
    _drawParticles(canvas, metric, color, intensity);
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
        oldDelegate.gridImportPower != gridImportPower ||
        oldDelegate.gridExportPower != gridExportPower ||
        oldDelegate.batteryChargePower != batteryChargePower ||
        oldDelegate.batteryDischargePower != batteryDischargePower ||
        oldDelegate.loadPower != loadPower ||
        oldDelegate.selectedLink != selectedLink ||
        oldDelegate.isDark != isDark ||
        oldDelegate.reduceEffects != reduceEffects;
  }
}

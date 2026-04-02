import 'package:flutter/material.dart';
import '../providers/app_provider.dart';
import '../models/inverter_data.dart';
import '../widgets/energy_flow.dart';
import '../widgets/control_panel.dart';

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
          const SizedBox(height: 24),
          EnergyFlowDiagram(data: data),
          const SizedBox(height: 24),
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
          Expanded(child: Text(message, style: const TextStyle(color: Colors.amber, fontWeight: FontWeight.w600, fontSize: 13))),
        ],
      ),
    );
  }
}
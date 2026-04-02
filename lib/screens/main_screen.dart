import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import 'dashboard_tab.dart';
import 'automation_tab.dart';
import 'details_tab.dart';
import 'settings_tab.dart';

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});
  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      if (mounted) context.read<AppStateProvider>().startTimers();
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<AppStateProvider>();
    final data = provider.data;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final l10n = AppLocalizations.of(context)!;

    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text(l10n.appTitle,
              style: const TextStyle(fontWeight: FontWeight.bold)),
          actions: [
            IconButton(
              icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
              onPressed: provider.toggleTheme,
            ),
            IconButton(
                icon: const Icon(Icons.refresh), onPressed: provider.fetchData),
          ],
          bottom: TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            indicatorColor: Colors.amber,
            labelColor: Colors.amber,
            tabs: [
              Tab(
                  icon: const Icon(Icons.dashboard_rounded),
                  text: l10n.dashboard),
              Tab(
                  icon: const Icon(Icons.smart_toy_rounded),
                  text: l10n.automation),
              Tab(
                  icon: const Icon(Icons.list_alt_rounded),
                  text: l10n.data),
              Tab(
                  icon: const Icon(Icons.settings),
                  text: l10n.settings),
            ],
          ),
        ),
        body: data == null
            ? const Center(
                child: CircularProgressIndicator(color: Colors.amber))
            : TabBarView(
                children: [
                  DashboardTab(provider: provider, data: data),
                  AutomationTab(provider: provider),
                  DetailsTab(data: data),
                  SettingsTab(provider: provider),
                ],
              ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../theme/app_theme.dart';
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
          title: Text(
            l10n.appTitle,
            style: Theme.of(context).textTheme.titleLarge,
          ),
          actions: [
            // Tema toggle
            Tooltip(
              message: isDark ? 'Світла тема' : 'Темна тема',
              child: IconButton(
                icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
                onPressed: provider.toggleTheme,
              ),
            ),
            // Refresh
            Tooltip(
              message: 'Оновити',
              child: IconButton(
                icon: const Icon(Icons.refresh),
                onPressed: provider.fetchData,
              ),
            ),
            const SizedBox(width: AppTheme.spacingM),
          ],
          bottom: TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            indicatorColor: Theme.of(context).colorScheme.primary,
            indicatorWeight: 3,
            labelStyle: Theme.of(context).textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
            unselectedLabelStyle: Theme.of(context).textTheme.titleMedium,
            tabs: [
              _buildTab(Icons.dashboard_rounded, l10n.dashboard),
              _buildTab(Icons.smart_toy_rounded, l10n.automation),
              _buildTab(Icons.list_alt_rounded, l10n.data),
              _buildTab(Icons.settings, l10n.settings),
            ],
          ),
        ),
        body: data == null
            ? const Center(
                child: CircularProgressIndicator(),
              )
            : TabBarView(
                children: [
                  DashboardTab(provider: provider, data: data),
                  AutomationTab(provider: provider),
                  DetailsTab(data: data, provider: provider),
                  SettingsTab(provider: provider),
                ],
              ),
      ),
    );
  }

  Tab _buildTab(IconData icon, String label) {
    return Tab(
      icon: Icon(icon),
      text: label,
      iconMargin: const EdgeInsets.only(bottom: AppTheme.spacingXS),
    );
  }
}

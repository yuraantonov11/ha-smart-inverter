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
    final viewData = data ?? provider.cachedSnapshotData;
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
            const SizedBox(width: AppTheme.spacingS),
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
        body: TabBarView(
          children: [
            viewData != null
                ? DashboardTab(provider: provider, data: viewData)
                : _NoDataState(
                    isOffline: provider.isInverterOffline,
                    statusMessage: provider.statusMessage,
                    onRetry: provider.fetchData,
                    isLoading: provider.isDataLoading,
                  ),
            AutomationTab(provider: provider),
            viewData != null
                ? DetailsTab(data: viewData, provider: provider)
                : _NoDataState(
                    isOffline: provider.isInverterOffline,
                    statusMessage: provider.statusMessage,
                    onRetry: provider.fetchData,
                    isLoading: provider.isDataLoading,
                  ),
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

class _NoDataState extends StatelessWidget {
  final bool isOffline;
  final String statusMessage;
  final Future<void> Function() onRetry;
  final bool isLoading;

  const _NoDataState({
    required this.isOffline,
    required this.statusMessage,
    required this.onRetry,
    required this.isLoading,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final localeCode = Localizations.localeOf(context).languageCode;
    final offlineLabel = localeCode == 'uk' ? 'Офлайн' : 'Offline';
    final retryLabel = localeCode == 'uk' ? 'Повторити' : 'Retry';

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: isLoading
            ? const CircularProgressIndicator()
            : Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    isOffline
                        ? Icons.cloud_off_rounded
                        : Icons.wifi_tethering_error,
                    size: 44,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    isOffline ? offlineLabel : l10n.updateFailed,
                    style: Theme.of(context).textTheme.titleMedium,
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    statusMessage.isNotEmpty
                        ? statusMessage
                        : l10n.updateFailed,
                    style: Theme.of(context).textTheme.bodyMedium,
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 16),
                  ElevatedButton.icon(
                    onPressed: onRetry,
                    icon: const Icon(Icons.refresh),
                    label: Text(retryLabel),
                  ),
                ],
              ),
      ),
    );
  }
}

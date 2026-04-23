import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';
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
  int _selectedIndex = 0;

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
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;

    final pages = <Widget>[
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
    ];

    final destinations = [
      (icon: Icons.dashboard_rounded, label: l10n.dashboard),
      (icon: Icons.smart_toy_rounded, label: l10n.automation),
      (icon: Icons.list_alt_rounded, label: l10n.data),
      (icon: Icons.settings_rounded, label: l10n.settings),
    ];

    return LayoutBuilder(
      builder: (context, constraints) {
        final useRail = constraints.maxWidth >= 980;
        final extendedRail = constraints.maxWidth >= 1280;
        final compactRailWidth = 88.0;
        final langCode = provider.lang.toUpperCase();

        return Scaffold(
          extendBody: true,
          appBar: AppBar(
            title: Text(
              l10n.appTitle,
              style: Theme.of(context).textTheme.titleLarge,
            ),
            actions: [
              Tooltip(
                message: isDark ? l10n.lightTheme : l10n.darkTheme,
                child: IconButton(
                  icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
                  onPressed: provider.toggleTheme,
                ),
              ),
              const SizedBox(width: AppTheme.spacingS),
            ],
          ),
          body: useRail
              ? Row(
                  children: [
                    Padding(
                      padding: const EdgeInsets.all(AppTheme.spacingL),
                      child: AppGlassSurface(
                        isStrong: true,
                        borderRadius: 28,
                        child: SizedBox(
                          width: extendedRail ? 220 : compactRailWidth,
                          child: Column(
                            children: [
                              Expanded(
                                child: NavigationRail(
                                  backgroundColor: Colors.transparent,
                                  minWidth: 64,
                                  selectedIndex: _selectedIndex,
                                  onDestinationSelected: (index) {
                                    setState(() => _selectedIndex = index);
                                  },
                                  extended: extendedRail,
                                  scrollable: true,
                                  labelType: extendedRail
                                      ? null
                                      : NavigationRailLabelType.none,
                                  minExtendedWidth: 196,
                                  groupAlignment: extendedRail ? -0.82 : -1,
                                  useIndicator: true,
                                  indicatorColor: theme.colorScheme.primary
                                      .withValues(alpha: 0.16),
                                  selectedIconTheme: IconThemeData(
                                      color: theme.colorScheme.primary),
                                  unselectedIconTheme: IconThemeData(
                                    color: theme.colorScheme.onSurfaceVariant,
                                  ),
                                  selectedLabelTextStyle:
                                      theme.textTheme.titleSmall?.copyWith(
                                    color: theme.colorScheme.primary,
                                    fontWeight: FontWeight.w700,
                                  ),
                                  unselectedLabelTextStyle:
                                      theme.textTheme.bodyMedium?.copyWith(
                                    color: theme.colorScheme.onSurfaceVariant,
                                  ),
                                  leading: Padding(
                                    padding: EdgeInsets.fromLTRB(
                                      extendedRail
                                          ? AppTheme.spacingM
                                          : AppTheme.spacingS,
                                      AppTheme.spacingL,
                                      extendedRail
                                          ? AppTheme.spacingM
                                          : AppTheme.spacingS,
                                      AppTheme.spacingM,
                                    ),
                                    child: extendedRail
                                        ? Row(
                                            mainAxisAlignment:
                                                MainAxisAlignment.start,
                                            children: [
                                              _RailLogoIcon(theme: theme),
                                              const SizedBox(
                                                  width: AppTheme.spacingM),
                                              Expanded(
                                                child: Column(
                                                  crossAxisAlignment:
                                                      CrossAxisAlignment.start,
                                                  mainAxisSize:
                                                      MainAxisSize.min,
                                                  children: [
                                                    Text(
                                                      l10n.appTitle,
                                                      maxLines: 1,
                                                      overflow:
                                                          TextOverflow.ellipsis,
                                                      style: theme
                                                          .textTheme.titleSmall
                                                          ?.copyWith(
                                                        fontWeight:
                                                            FontWeight.w700,
                                                      ),
                                                    ),
                                                    Text(
                                                      'SMART',
                                                      style: theme
                                                          .textTheme.labelSmall,
                                                    ),
                                                  ],
                                                ),
                                              ),
                                            ],
                                          )
                                        : Center(
                                            child: _RailLogoIcon(theme: theme),
                                          ),
                                  ),
                                  destinations: destinations
                                      .map(
                                        (d) => NavigationRailDestination(
                                          icon: Icon(d.icon),
                                          selectedIcon: Icon(d.icon),
                                          label: Text(d.label),
                                        ),
                                      )
                                      .toList(),
                                ),
                              ),
                              Padding(
                                padding: const EdgeInsets.fromLTRB(
                                  AppTheme.spacingS,
                                  AppTheme.spacingS,
                                  AppTheme.spacingS,
                                  AppTheme.spacingL,
                                ),
                                child: _RailLanguageButton(
                                  label: l10n.language,
                                  langCode: langCode,
                                  extendedRail: extendedRail,
                                  onTap: () => provider.setLanguage(
                                    provider.lang == 'en' ? 'uk' : 'en',
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    const VerticalDivider(width: 1),
                    Expanded(
                      child: Stack(
                        children: [
                          Positioned.fill(
                            child: IgnorePointer(
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  gradient: LinearGradient(
                                    begin: Alignment.topLeft,
                                    end: Alignment.bottomRight,
                                    colors: [
                                      theme.colorScheme.primary
                                          .withValues(alpha: 0.04),
                                      Colors.transparent,
                                      theme.colorScheme.secondary
                                          .withValues(alpha: 0.03),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                          AnimatedSwitcher(
                            duration: const Duration(milliseconds: 220),
                            child: KeyedSubtree(
                              key: ValueKey(_selectedIndex),
                              child: pages[_selectedIndex],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                )
              : Stack(
                  children: [
                    Positioned.fill(
                      child: IgnorePointer(
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                              colors: [
                                theme.colorScheme.primary
                                    .withValues(alpha: 0.04),
                                Colors.transparent,
                                theme.colorScheme.secondary
                                    .withValues(alpha: 0.03),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                    pages[_selectedIndex],
                  ],
                ),
          bottomNavigationBar: useRail
              ? null
              : NavigationBar(
                  selectedIndex: _selectedIndex,
                  onDestinationSelected: (index) {
                    setState(() => _selectedIndex = index);
                  },
                  destinations: destinations
                      .map(
                        (d) => NavigationDestination(
                          icon: Icon(d.icon),
                          label: d.label,
                        ),
                      )
                      .toList(),
                ),
        );
      },
    );
  }
}

class _RailLanguageButton extends StatelessWidget {
  final String label;
  final String langCode;
  final bool extendedRail;
  final VoidCallback onTap;

  const _RailLanguageButton({
    required this.label,
    required this.langCode,
    required this.extendedRail,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Tooltip(
      message: label,
      child: InkWell(
        borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
        onTap: onTap,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(
            horizontal: AppTheme.spacingS,
            vertical: AppTheme.spacingS,
          ),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
            color: theme.colorScheme.primary.withValues(alpha: 0.14),
            border: Border.all(
              color: theme.colorScheme.primary.withValues(alpha: 0.35),
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                Icons.language_rounded,
                size: 16,
                color: theme.colorScheme.primary,
              ),
              if (extendedRail) ...[
                const SizedBox(width: AppTheme.spacingS),
                Text(
                  langCode,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: theme.colorScheme.primary,
                    letterSpacing: 0.8,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _RailLogoIcon extends StatelessWidget {
  final ThemeData theme;

  const _RailLogoIcon({required this.theme});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
        color: theme.colorScheme.primary.withValues(alpha: 0.14),
        border: Border.all(
          color: theme.colorScheme.primary.withValues(alpha: 0.34),
        ),
      ),
      child: Icon(
        Icons.bolt_rounded,
        color: theme.colorScheme.primary,
      ),
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
                    isOffline ? l10n.connectionOffline : l10n.updateFailed,
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
                    label: Text(l10n.retry),
                  ),
                ],
              ),
      ),
    );
  }
}

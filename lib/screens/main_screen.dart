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
  static final Tween<Offset> _switchSlideTween = Tween<Offset>(
    begin: const Offset(0.016, 0),
    end: Offset.zero,
  );

  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      if (mounted) context.read<AppStateProvider>().startTimers();
    });
  }

  Widget _buildRawPage({
    required int index,
    required AppStateProvider provider,
    required dynamic viewData,
    required bool isInverterOffline,
    required String statusMessage,
    required bool isDataLoading,
  }) {
    switch (index) {
      case 0:
        return viewData != null
            ? DashboardTab(provider: provider, data: viewData)
            : _NoDataState(
                isOffline: isInverterOffline,
                statusMessage: statusMessage,
                onRetry: provider.fetchData,
                isLoading: isDataLoading,
              );
      case 1:
        return AutomationTab(provider: provider);
      case 2:
        return viewData != null
            ? DetailsTab(data: viewData, provider: provider)
            : _NoDataState(
                isOffline: isInverterOffline,
                statusMessage: statusMessage,
                onRetry: provider.fetchData,
                isLoading: isDataLoading,
              );
      case 3:
      default:
        return SettingsTab(provider: provider);
    }
  }

  Widget _buildFramedPage({
    required int index,
    required AppStateProvider provider,
    required dynamic viewData,
    required bool isInverterOffline,
    required String statusMessage,
    required bool isDataLoading,
    required AppLocalizations l10n,
  }) {
    final child = _buildRawPage(
      index: index,
      provider: provider,
      viewData: viewData,
      isInverterOffline: isInverterOffline,
      statusMessage: statusMessage,
      isDataLoading: isDataLoading,
    );

    switch (index) {
      case 0:
        return AppScreenFrame(
          icon: Icons.dashboard_rounded,
          title: l10n.dashboard,
          subtitle: l10n.energyOverview,
          trailing: _PageStatusPill(
            isOffline: isInverterOffline,
            onlineLabel: l10n.connectionOnline,
            offlineLabel: l10n.connectionOffline,
          ),
          child: child,
        );
      case 1:
        return AppScreenFrame(
          icon: Icons.smart_toy_rounded,
          title: l10n.automation,
          subtitle: l10n.hemsSubtitle,
          child: child,
        );
      case 2:
        return AppScreenFrame(
          icon: Icons.list_alt_rounded,
          title: l10n.data,
          subtitle: l10n.realtimeReadings,
          child: child,
        );
      case 3:
      default:
        return AppScreenFrame(
          icon: Icons.settings_rounded,
          title: l10n.settings,
          subtitle: l10n.appSettings,
          child: child,
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.read<AppStateProvider>();
    final data = context.select((AppStateProvider p) => p.data);
    final cachedSnapshotData =
        context.select((AppStateProvider p) => p.cachedSnapshotData);
    final isInverterOffline =
        context.select((AppStateProvider p) => p.isInverterOffline);
    final statusMessage =
        context.select((AppStateProvider p) => p.statusMessage);
    final isDataLoading =
        context.select((AppStateProvider p) => p.isDataLoading);
    final lang = context.select((AppStateProvider p) => p.lang);
    final viewData = data ?? cachedSnapshotData;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final theme = Theme.of(context);
    final motion = context.motion;
    final expressive = context.expressive;
    final l10n = AppLocalizations.of(context)!;

    String safeLabel(String localized, String fallback) {
      final trimmed = localized.trim();
      return trimmed.isEmpty ? fallback : trimmed;
    }

    final destinations = [
      (
        icon: Icons.dashboard_rounded,
        label: safeLabel(l10n.dashboard, 'Dashboard')
      ),
      (
        icon: Icons.smart_toy_rounded,
        label: safeLabel(l10n.automation, 'Automation')
      ),
      (icon: Icons.list_alt_rounded, label: safeLabel(l10n.data, 'Data')),
      (
        icon: Icons.settings_rounded,
        label: safeLabel(l10n.settings, 'Settings')
      ),
    ];

    return LayoutBuilder(
      builder: (context, constraints) {
        final useRail = constraints.maxWidth >= 980;
        final extendedRail = constraints.maxWidth >= 1280;
        final compactRailWidth = 96.0;
        final compactRailMinWidth = 72.0;
        final safeIndex = _selectedIndex.clamp(0, destinations.length - 1);
        final langCode = lang.toUpperCase();
        final activePageLabel = destinations[safeIndex].label;

        Widget buildPageSwitcher() {
          return AnimatedSwitcher(
            duration: motion.regular,
            switchInCurve: motion.standardCurve,
            switchOutCurve: motion.standardCurve,
            layoutBuilder: (currentChild, previousChildren) {
              // Paint only the active screen to guarantee no text overlap.
              return currentChild ?? const SizedBox.shrink();
            },
            transitionBuilder: (child, animation) {
              final currentKey = ValueKey(safeIndex);
              final isIncoming = child.key == currentKey;

              if (!isIncoming) {
                return const SizedBox.shrink();
              }

              final opacity = animation.drive(
                CurveTween(
                  curve: const Interval(0.0, 1.0, curve: Curves.easeOutCubic),
                ),
              );
              final slide = _switchSlideTween
                  .chain(
                    CurveTween(
                      curve: const Interval(
                        0.0,
                        1.0,
                        curve: Curves.easeOutCubic,
                      ),
                    ),
                  )
                  .animate(animation);

              return FadeTransition(
                opacity: opacity,
                child: SlideTransition(
                  position: slide,
                  child: RepaintBoundary(child: child),
                ),
              );
            },
            child: KeyedSubtree(
              key: ValueKey(safeIndex),
              child: _buildFramedPage(
                index: safeIndex,
                provider: provider,
                viewData: viewData,
                isInverterOffline: isInverterOffline,
                statusMessage: statusMessage,
                isDataLoading: isDataLoading,
                l10n: l10n,
              ),
            ),
          );
        }

        return Scaffold(
          extendBody: useRail,
          appBar: AppBar(
            titleSpacing: AppTheme.spacingS,
            flexibleSpace: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    theme.colorScheme.primary.withValues(alpha: 0.08),
                    theme.colorScheme.surface.withValues(alpha: 0.92),
                    theme.colorScheme.tertiary.withValues(alpha: 0.07),
                  ],
                ),
              ),
            ),
            title: Row(
              children: [
                _RailLogoIcon(theme: theme),
                const SizedBox(width: AppTheme.spacingS),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        l10n.appTitle,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleMedium,
                      ),
                      Text(
                        activePageLabel,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            scrolledUnderElevation: 0,
            actions: [
              if (!useRail)
                Padding(
                  padding: const EdgeInsets.only(right: AppTheme.spacingXS),
                  child: FilledButton.tonalIcon(
                    onPressed: () => provider.setLanguage(
                      lang == 'en' ? 'uk' : 'en',
                    ),
                    icon: const Icon(Icons.language_rounded, size: 18),
                    label: Text(langCode),
                  ),
                ),
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
          body: AppShellBackground(
            child: useRail
                ? Row(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(AppTheme.spacingL),
                        child: AppGlassSurface(
                          isStrong: true,
                          borderRadius: expressive.cornerXL,
                          child: SizedBox(
                            width: extendedRail ? 220 : compactRailWidth,
                            child: Column(
                              children: [
                                Expanded(
                                  child: NavigationRail(
                                    backgroundColor: Colors.transparent,
                                    minWidth: compactRailMinWidth,
                                    selectedIndex: safeIndex,
                                    onDestinationSelected: (index) {
                                      setState(() => _selectedIndex = index);
                                    },
                                    extended: extendedRail,
                                    labelType: extendedRail
                                        ? null
                                        : NavigationRailLabelType.selected,
                                    minExtendedWidth: 208,
                                    groupAlignment:
                                        extendedRail ? -0.82 : -0.95,
                                    useIndicator: true,
                                    indicatorColor: theme.colorScheme.primary
                                        .withValues(
                                            alpha: expressive
                                                .navigationIndicatorOpacity),
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
                                                        CrossAxisAlignment
                                                            .start,
                                                    mainAxisSize:
                                                        MainAxisSize.min,
                                                    children: [
                                                      Text(
                                                        l10n.appTitle,
                                                        maxLines: 1,
                                                        overflow: TextOverflow
                                                            .ellipsis,
                                                        style: theme.textTheme
                                                            .titleSmall
                                                            ?.copyWith(
                                                          fontWeight:
                                                              FontWeight.w700,
                                                        ),
                                                      ),
                                                      Text(
                                                        activePageLabel,
                                                        maxLines: 1,
                                                        overflow: TextOverflow
                                                            .ellipsis,
                                                        style: theme.textTheme
                                                            .labelSmall,
                                                      ),
                                                    ],
                                                  ),
                                                ),
                                              ],
                                            )
                                          : Center(
                                              child:
                                                  _RailLogoIcon(theme: theme)),
                                    ),
                                    destinations: destinations
                                        .map(
                                          (d) => NavigationRailDestination(
                                            icon: Icon(d.icon),
                                            selectedIcon: Icon(d.icon),
                                            label: Text(
                                              d.label,
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              softWrap: false,
                                            ),
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
                                      lang == 'en' ? 'uk' : 'en',
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      const VerticalDivider(width: 1),
                      Expanded(child: buildPageSwitcher()),
                    ],
                  )
                : buildPageSwitcher(),
          ),
          bottomNavigationBar: useRail
              ? null
              : Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppTheme.spacingM,
                    0,
                    AppTheme.spacingM,
                    AppTheme.spacingM,
                  ),
                  child: AppGlassSurface(
                    isStrong: true,
                    borderRadius: expressive.cornerXL,
                    child: NavigationBar(
                      selectedIndex: safeIndex,
                      height: 70,
                      onDestinationSelected: (index) {
                        setState(() => _selectedIndex = index);
                      },
                      labelBehavior:
                          NavigationDestinationLabelBehavior.onlyShowSelected,
                      destinations: destinations
                          .map(
                            (d) => NavigationDestination(
                              icon: Icon(d.icon),
                              label: d.label,
                            ),
                          )
                          .toList(),
                    ),
                  ),
                ),
        );
      },
    );
  }
}

class _PageStatusPill extends StatelessWidget {
  final bool isOffline;
  final String onlineLabel;
  final String offlineLabel;

  const _PageStatusPill({
    required this.isOffline,
    required this.onlineLabel,
    required this.offlineLabel,
  });

  @override
  Widget build(BuildContext context) {
    final color =
        isOffline ? Theme.of(context).colorScheme.error : AppTheme.batteryColor;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            isOffline ? Icons.cloud_off_rounded : Icons.cloud_done_rounded,
            size: 14,
            color: color,
          ),
          const SizedBox(width: 6),
          Text(
            isOffline ? offlineLabel : onlineLabel,
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: color,
                  fontWeight: FontWeight.w700,
                ),
          ),
        ],
      ),
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

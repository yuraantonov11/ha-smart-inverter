import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../services/log_service.dart';
import '../services/update_service.dart';
import '../theme/app_theme.dart';
import '../widgets/app_components.dart';

class SettingsTab extends StatelessWidget {
  final AppStateProvider provider;

  const SettingsTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final showLanguageInSettings = MediaQuery.of(context).size.width < 980;
    final isDesktop =
        Platform.isWindows || Platform.isLinux || Platform.isMacOS;
    final isMobilePlatform = Platform.isAndroid || Platform.isIOS;
    final enableGlassBlur = !isMobilePlatform;
    final supportsInAppUpdater = Platform.isWindows;
    final updateBanner = _buildUpdateBanner(context, l10n);

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // Блок Акаунта
        _buildSectionTitle(l10n.account),
        _buildAccountCard(context, l10n),

        const SizedBox(height: 24),

        // Блок Налаштувань Додатка
        _buildSectionTitle(l10n.appSettings),
        if (supportsInAppUpdater && updateBanner != null) ...[
          updateBanner,
          const SizedBox(height: 12),
        ],
        const SizedBox(height: 16),
        HardwareSettingsSection(
          provider: provider,
          enableBlur: enableGlassBlur,
        ), // <--- Додаємо сюди
        const SizedBox(height: 16),
        AppGlassSurface(
          isStrong: true,
          enableBlur: enableGlassBlur,
          borderRadius: 24,
          child: Material(
            color: Colors.transparent,
            child: _buildSettingsControls(
              context,
              l10n,
              theme,
              showLanguageInSettings,
              isDesktop,
              supportsInAppUpdater,
              enableGlassBlur,
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSectionTitle(String title) {
    return Builder(builder: (context) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 12, left: 4),
        child: Text(
          title,
          style: Theme.of(context).textTheme.titleLarge?.copyWith(
                color: Theme.of(context).colorScheme.onSurface,
                fontSize: 16,
                fontWeight: FontWeight.w700,
              ),
        ),
      );
    });
  }

  Widget _buildSettingsControls(
    BuildContext context,
    AppLocalizations l10n,
    ThemeData theme,
    bool showLanguageInSettings,
    bool isDesktop,
    bool supportsInAppUpdater,
    bool enableGlassBlur,
  ) {
    return Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          children: [
            if (showLanguageInSettings) ...[
              _buildSettingsTile(
                context,
                icon: Icons.language,
                iconColor: theme.colorScheme.primary,
                title: l10n.language,
                trailing: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    dropdownColor: theme.cardColor,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurface,
                    ),
                    borderRadius: BorderRadius.circular(14),
                    value: provider.lang,
                    items: const [
                      DropdownMenuItem(value: 'en', child: Text('English')),
                      DropdownMenuItem(value: 'uk', child: Text('Українська')),
                    ],
                    onChanged: (val) {
                      if (val != null) provider.setLanguage(val);
                    },
                  ),
                ),
              ),
              const SizedBox(height: 10),
            ],
            _buildSettingsTile(
              context,
              icon: Icons.palette,
              iconColor: theme.colorScheme.secondary,
              title: l10n.theme,
              trailing: Switch(
                value: provider.themeMode == ThemeMode.dark,
                onChanged: (_) => provider.toggleTheme(),
              ),
            ),
            const SizedBox(height: 10),
            if (isDesktop) ...[
              _buildSettingsSwitchTile(
                context,
                icon: Icons.power_settings_new,
                title: l10n.startWithWindows,
                value: provider.isAutostartEnabled,
                onChanged: provider.toggleAutostart,
              ),
              const SizedBox(height: 10),
              _buildSettingsSwitchTile(
                context,
                icon: Icons.minimize_rounded,
                title: l10n.startInTray,
                subtitle: l10n.startInTraySubtitle,
                value: provider.isStartInTrayEnabled,
                onChanged: provider.toggleStartInTray,
              ),
              const SizedBox(height: 10),
            ],
            if (supportsInAppUpdater)
              _buildSettingsTile(
                context,
                icon: Icons.update,
                iconColor: theme.colorScheme.primary,
                title: l10n.updatesTitle,
                subtitle: _buildUpdateSubtitle(l10n),
                trailing: provider.isCheckingForUpdates
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(
                        provider.hasPendingUpdate
                            ? Icons.system_update_alt_rounded
                            : Icons.chevron_right,
                        color: provider.hasPendingUpdate
                            ? theme.colorScheme.secondary
                            : theme.colorScheme.onSurfaceVariant,
                      ),
                onTap: () => _checkForUpdates(context),
              ),
            if (provider.isDeveloperMode) ...[
              const SizedBox(height: 14),
              _buildSectionTitle(l10n.debugLogs),
              AppGlassSurface(
                isStrong: false,
                enableBlur: enableGlassBlur,
                borderRadius: 20,
                child: Material(
                  color: Colors.transparent,
                  child: _buildSettingsTile(
                    context,
                    icon: Icons.bug_report,
                    iconColor: theme.colorScheme.error,
                    title: l10n.viewSystemLogs,
                    subtitle: l10n.analyzeSystemLogs,
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => _showLogsDialog(context),
                  ),
                ),
              ),
            ],
            const SizedBox(height: 32),
            GestureDetector(
              onTap: provider.handleVersionClick,
              child: Center(
                child: Text(
                  provider.appVersionLabel,
                  style: TextStyle(
                    color: Colors.grey.withValues(alpha: 0.5),
                    fontSize: 12,
                  ),
                ),
              ),
            ),
          ],
        ));
  }

  Widget _buildSettingsTile(
    BuildContext context, {
    required IconData icon,
    required String title,
    String? subtitle,
    Color? iconColor,
    Widget? trailing,
    VoidCallback? onTap,
  }) {
    return _buildSettingsItemShell(
      context,
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        leading: Icon(
          icon,
          color: iconColor ?? Theme.of(context).colorScheme.primary,
        ),
        title: Text(title),
        subtitle: subtitle == null ? null : Text(subtitle),
        trailing: trailing,
        onTap: onTap,
      ),
    );
  }

  Widget _buildSettingsSwitchTile(
    BuildContext context, {
    required IconData icon,
    required String title,
    String? subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return _buildSettingsItemShell(
      context,
      child: SwitchListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        secondary: Icon(icon, color: Theme.of(context).colorScheme.primary),
        title: Text(title),
        subtitle: subtitle == null ? null : Text(subtitle),
        value: value,
        onChanged: onChanged,
      ),
    );
  }

  Widget _buildSettingsItemShell(
    BuildContext context, {
    required Widget child,
  }) {
    final theme = Theme.of(context);
    return Material(
      color: theme.cardColor.withValues(alpha: 0.58),
      borderRadius: BorderRadius.circular(16),
      child: Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: theme.dividerColor.withValues(alpha: 0.55),
          ),
        ),
        child: child,
      ),
    );
  }

  Widget? _buildUpdateBanner(BuildContext context, AppLocalizations l10n) {
    final theme = Theme.of(context);
    final enableGlassBlur = !(Platform.isAndroid || Platform.isIOS);
    final info = provider.updateInfo;
    final checkedAt = provider.lastUpdateCheckAt;

    if (provider.isCheckingForUpdates) {
      return AppGlassSurface(
        isStrong: false,
        enableBlur: enableGlassBlur,
        borderRadius: 14,
        backgroundColor: theme.colorScheme.primary.withValues(alpha: 0.08),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: Row(
            children: [
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              const SizedBox(width: 10),
              Expanded(child: Text(l10n.updatesCheckingBackground)),
            ],
          ),
        ),
      );
    }

    if (info == null) {
      if (checkedAt == null) return null;
      return AppGlassSurface(
        enableBlur: enableGlassBlur,
        borderRadius: 14,
        backgroundColor: theme.cardColor.withValues(alpha: 0.45),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: [
              Icon(
                Icons.check_circle_outline_rounded,
                size: 16,
                color: theme.colorScheme.secondary,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  l10n.updatesLastChecked(
                      UpdateService.formatPublishedAt(checkedAt)),
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ),
      );
    }

    final isSkipped = provider.skippedUpdateVersion != null &&
        info.hasUpdate &&
        provider.skippedUpdateVersion == info.latestVersion;

    if (!provider.hasPendingUpdate && !isSkipped) {
      return checkedAt == null
          ? null
          : AppGlassSurface(
              enableBlur: enableGlassBlur,
              borderRadius: 14,
              backgroundColor: theme.cardColor.withValues(alpha: 0.45),
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                child: Row(
                  children: [
                    Icon(
                      Icons.check_circle_outline_rounded,
                      size: 16,
                      color: theme.colorScheme.secondary,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        l10n.updatesLastChecked(
                            UpdateService.formatPublishedAt(checkedAt)),
                        style: theme.textTheme.bodySmall,
                      ),
                    ),
                  ],
                ),
              ),
            );
    }

    if (isSkipped) {
      final amberColor = Theme.of(context).colorScheme.tertiary;
      return AppGlassSurface(
        enableBlur: enableGlassBlur,
        borderRadius: 14,
        backgroundColor: amberColor.withValues(alpha: 0.1),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              Icon(Icons.history_toggle_off_rounded, color: amberColor),
              const SizedBox(width: 10),
              Expanded(
                child: Text(l10n.updatesSkippedBanner(info.latestVersion)),
              ),
              TextButton(
                onPressed: () async {
                  await provider.clearSkippedUpdate();
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text(l10n.updatesSkippedRestored)),
                    );
                  }
                },
                child: Text(l10n.updatesRestore),
              ),
            ],
          ),
        ),
      );
    }

    final successColor = Theme.of(context).colorScheme.secondary;
    return AppGlassSurface(
      enableBlur: enableGlassBlur,
      borderRadius: 14,
      backgroundColor: successColor.withValues(alpha: 0.1),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.system_update_alt_rounded, color: successColor),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    l10n.updatesBannerAvailable(info.latestVersion),
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              l10n.updatesCurrentVersion(info.currentVersion),
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                OutlinedButton.icon(
                  onPressed: () => _showUpdateAvailableDialog(context, info),
                  icon: const Icon(Icons.visibility_rounded, size: 16),
                  label: Text(l10n.updatesView),
                ),
                const SizedBox(width: 8),
                TextButton(
                  onPressed: () async {
                    await provider.skipLatestUpdate();
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content:
                              Text(l10n.updatesSkippedNow(info.latestVersion)),
                        ),
                      );
                    }
                  },
                  child: Text(l10n.updatesSkip),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAccountCard(BuildContext context, AppLocalizations l10n) {
    final theme = Theme.of(context);
    final enableGlassBlur = !(Platform.isAndroid || Platform.isIOS);

    final displayName =
        provider.displayName.trim().isNotEmpty && provider.displayName != 'N/A'
            ? provider.displayName
            : (provider.userName?.trim().isNotEmpty ?? false)
                ? provider.userName!.trim()
                : l10n.userNameDefault;

    final email = provider.displayEmail.trim().isNotEmpty
        ? provider.displayEmail.trim()
        : (provider.savedEmail?.trim().isNotEmpty ?? false)
            ? provider.savedEmail!.trim()
            : l10n.notProvided;

    final phone = provider.displayPhone.trim().isNotEmpty
        ? provider.displayPhone.trim()
        : l10n.notProvided;

    final uid = provider.userData?['uid']?.toString().trim();
    final cloudAccount = provider.displayAccount.trim().isNotEmpty &&
            provider.displayAccount != 'N/A'
        ? provider.displayAccount
        : l10n.unknownValue;

    final statusLabel = provider.userData == null
        ? l10n.accountStatusLocal
        : l10n.accountStatusSynced;

    return AppGlassSurface(
      isStrong: true,
      enableBlur: enableGlassBlur,
      borderRadius: 24,
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Stack(
                  children: [
                    const CircleAvatar(
                      radius: 32,
                      child: Icon(Icons.person, size: 30),
                    ),
                    Positioned(
                      right: -3,
                      bottom: -3,
                      child: InkWell(
                        onTap: () => _showEditProfileDialog(context),
                        borderRadius: BorderRadius.circular(20),
                        child: Container(
                          padding: const EdgeInsets.all(6),
                          decoration: BoxDecoration(
                            color: theme.colorScheme.primary,
                            shape: BoxShape.circle,
                          ),
                          child: const Icon(Icons.edit,
                              size: 14, color: Colors.white),
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        displayName,
                        style: const TextStyle(
                            fontSize: 19, fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        email,
                        style: TextStyle(
                          color: theme.textTheme.bodySmall?.color,
                          fontSize: 13,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(
                          color: provider.userData == null
                              ? theme.colorScheme.errorContainer
                              : theme.colorScheme.secondaryContainer,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          statusLabel,
                          style: TextStyle(
                            color: provider.userData == null
                                ? theme.colorScheme.onErrorContainer
                                : theme.colorScheme.onSecondaryContainer,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 18),
            AppGlassSurface(
              enableBlur: enableGlassBlur,
              borderRadius: 14,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  children: [
                    _buildAccountInfoRow(
                      context,
                      label: l10n.cloudAccount,
                      value: cloudAccount,
                      icon: Icons.cloud_done_rounded,
                    ),
                    _buildAccountInfoRow(
                      context,
                      label: l10n.phoneLabel,
                      value: phone,
                      icon: Icons.phone_outlined,
                    ),
                    _buildAccountInfoRow(
                      context,
                      label: 'UID',
                      value: (uid?.isNotEmpty ?? false) ? uid! : '...',
                      icon: Icons.badge_outlined,
                      monospace: true,
                      onCopy: () => _copyToClipboard(
                        context,
                        (uid?.isNotEmpty ?? false) ? uid! : null,
                      ),
                    ),
                    _buildAccountInfoRow(
                      context,
                      label: l10n.sessionId,
                      value: provider.userId,
                      icon: Icons.fingerprint,
                      monospace: true,
                      onCopy: () => _copyToClipboard(context, provider.userId),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            Text(
              l10n.accountProfileHint,
              style: TextStyle(
                fontSize: 12,
                color: theme.textTheme.bodySmall?.color,
              ),
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _showEditProfileDialog(context),
                    icon: const Icon(Icons.edit_outlined, size: 18),
                    label: Text(l10n.editProfile),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _confirmLogout(context),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: theme.colorScheme.error,
                      side: BorderSide(color: theme.colorScheme.error),
                    ),
                    icon: const Icon(Icons.logout, size: 18),
                    label: Text(l10n.logout),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAccountInfoRow(
    BuildContext context, {
    required String label,
    required String value,
    required IconData icon,
    bool monospace = false,
    VoidCallback? onCopy,
  }) {
    final textStyle = Theme.of(context).textTheme.bodyMedium;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(icon,
              size: 17, color: Theme.of(context).colorScheme.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              label,
              style: textStyle?.copyWith(
                color: Theme.of(context).textTheme.bodySmall?.color,
              ),
            ),
          ),
          Flexible(
            child: Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.end,
              style: textStyle?.copyWith(
                fontWeight: FontWeight.w600,
                fontFamily: monospace ? 'monospace' : null,
              ),
            ),
          ),
          if (onCopy != null) ...[
            const SizedBox(width: 4),
            IconButton(
              onPressed: onCopy,
              splashRadius: 16,
              icon: const Icon(Icons.copy_rounded, size: 16),
              color: Theme.of(context).textTheme.bodySmall?.color,
              tooltip: AppLocalizations.of(context)!.copy,
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _copyToClipboard(BuildContext context, String? value) async {
    if (value == null || value.trim().isEmpty || value.trim() == '...') return;
    await Clipboard.setData(ClipboardData(text: value.trim()));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(AppLocalizations.of(context)!.copiedToClipboard)),
    );
  }

  void _showLogsDialog(BuildContext context) {
    final logsSnapshot = List<LogEntry>.from(LogService.entries);
    showDialog(
      context: context,
      builder: (context) => _LogsDialog(entries: logsSnapshot),
    );
  }

  void _showEditProfileDialog(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: provider.userName);
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l10n.editProfile),
        content: TextField(
          controller: controller,
          decoration: InputDecoration(
            labelText: l10n.name,
            border: const OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l10n.cancel),
          ),
          ElevatedButton(
            onPressed: () {
              final name = controller.text.trim();
              if (name.isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(l10n.nameCannotBeEmpty),
                  ),
                );
                return;
              }

              provider.updateProfile(name);
              Navigator.pop(context);
            },
            child: Text(l10n.save),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmLogout(BuildContext context) async {
    final l10n = AppLocalizations.of(context)!;
    final shouldLogout = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l10n.logout),
        content: Text(l10n.logoutConfirmMessage),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l10n.cancel),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l10n.logout),
          ),
        ],
      ),
    );

    if (shouldLogout == true) {
      await provider.logout();
    }
  }

  void _checkForUpdates(BuildContext context) async {
    final l10n = AppLocalizations.of(context)!;
    final scaffoldMessenger = ScaffoldMessenger.of(context);
    final info = await provider.checkForUpdates(force: true);

    if (info.hasUpdate) {
      if (!context.mounted) return;
      await _showUpdateAvailableDialog(context, info);
    } else {
      scaffoldMessenger.showSnackBar(
        SnackBar(
          content: Text(l10n.updatesSubtitleUpToDate(info.currentVersion)),
        ),
      );
    }
  }

  Future<void> _showUpdateAvailableDialog(
      BuildContext context, UpdateInfo info) async {
    final l10n = AppLocalizations.of(context)!;
    final parentContext = context;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(l10n.updatesDialogAvailableTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.updatesDialogCurrent(info.currentVersion)),
            Text(l10n.updatesDialogLatest(info.latestVersion)),
            const SizedBox(height: 8),
            Text(l10n.updatesDialogPublished(
                UpdateService.formatPublishedAt(info.publishedAt))),
            if (info.assetName != null) ...[
              const SizedBox(height: 4),
              Text(l10n.updatesDialogPackage(info.assetName!)),
            ],
            if ((info.releaseNotes ?? '').trim().isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                (info.releaseNotes!.trim().length > 220)
                    ? '${info.releaseNotes!.trim().substring(0, 220)}...'
                    : info.releaseNotes!.trim(),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ],
        ),
        actions: [
          TextButton(
            onPressed: () async {
              await provider.skipLatestUpdate();
              if (dialogContext.mounted) Navigator.pop(dialogContext);
            },
            child: Text(l10n.updatesDialogSkipVersion),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: Text(l10n.updatesDialogLater),
          ),
          ElevatedButton.icon(
            onPressed: () async {
              Navigator.pop(dialogContext);
              await _downloadAndInstallUpdate(parentContext, info);
            },
            icon: const Icon(Icons.download_rounded),
            label: Text(l10n.updatesDialogDownload),
          ),
        ],
      ),
    );
  }

  Future<void> _downloadAndInstallUpdate(
      BuildContext context, UpdateInfo info) async {
    final l10n = AppLocalizations.of(context)!;
    final scaffoldMessenger = ScaffoldMessenger.of(context);
    if (info.downloadUrl == null || info.assetName == null) {
      scaffoldMessenger.showSnackBar(
        SnackBar(
          content: Text(l10n.updatesNoInstallerFound),
        ),
      );
      return;
    }

    LogService.log(
        '⬇️ update.ui starting download dialog: version=${info.latestVersion}, asset=${info.assetName}');

    final path = await showDialog<String?>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _UpdateDownloadDialog(info: info),
    );

    if (!context.mounted) return;

    if (path != null) {
      LogService.log('✅ update.ui download dialog completed: path=$path');
      final shouldInstall = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: Text(l10n.updatesDialogInstallTitle),
          content: Text(l10n.updatesDialogInstallPrompt(info.latestVersion)),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: Text(l10n.cancel),
            ),
            ElevatedButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: Text(l10n.updatesDialogInstall),
            ),
          ],
        ),
      );

      if (!context.mounted || shouldInstall != true) {
        LogService.log('⏸️ update.ui install canceled by user');
        return;
      }

      LogService.log(
          '🚀 update.ui install confirmed: version=${info.latestVersion}, path=$path');
      final success = await UpdateService.installUpdate(path);
      if (success) {
        LogService.log(
            '🚪 update.ui installer started successfully, exiting app');
        exit(0);
      } else {
        LogService.log('❌ update.ui install failed for path=$path');
        scaffoldMessenger.showSnackBar(
          SnackBar(content: Text(l10n.updatesDialogInstallFailed)),
        );
      }
    } else {
      LogService.log(
          '⚠️ update.ui download dialog finished without file path: version=${info.latestVersion}',
          level: LogLevel.warn);
      scaffoldMessenger.showSnackBar(
        SnackBar(content: Text(l10n.updatesDialogDownloadFailed)),
      );
    }
  }

  String _buildUpdateSubtitle(AppLocalizations l10n) {
    final info = provider.updateInfo;
    if (provider.isCheckingForUpdates) {
      return l10n.updatesChecking;
    }
    if (info == null) {
      return l10n.updatesSubtitleDefault;
    }
    if (provider.hasPendingUpdate) {
      return _withLastChecked(
          l10n.updatesSubtitleAvailable(info.latestVersion), l10n);
    }
    if (provider.skippedUpdateVersion != null &&
        info.hasUpdate &&
        info.latestVersion == provider.skippedUpdateVersion) {
      return _withLastChecked(
          l10n.updatesSubtitleSkipped(info.latestVersion), l10n);
    }
    return _withLastChecked(
        l10n.updatesSubtitleUpToDate(info.currentVersion), l10n);
  }

  String _withLastChecked(String base, AppLocalizations l10n) {
    final checkedAt = provider.lastUpdateCheckAt;
    if (checkedAt == null) return base;
    return '$base • ${l10n.updatesLastChecked(UpdateService.formatPublishedAt(checkedAt))}';
  }
}

class _UpdateDownloadDialog extends StatefulWidget {
  final UpdateInfo info;

  const _UpdateDownloadDialog({required this.info});

  @override
  State<_UpdateDownloadDialog> createState() => _UpdateDownloadDialogState();
}

class _UpdateDownloadDialogState extends State<_UpdateDownloadDialog> {
  double _progress = 0.0;
  bool _isDone = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    unawaited(_startDownload());
  }

  Future<void> _startDownload() async {
    LogService.log(
        '⬇️ update.dialog download started: url=${widget.info.downloadUrl}, file=${widget.info.assetName}');
    try {
      final path = await UpdateService.downloadUpdateAsset(
        downloadUrl: widget.info.downloadUrl!,
        fileName: widget.info.assetName!,
        onProgress: (value) {
          if (!mounted) return;
          setState(() {
            _progress = value.clamp(0.0, 1.0);
          });
        },
      );

      if (!mounted) return;

      if (path != null) {
        _isDone = true;
        LogService.log('✅ update.dialog download finished: path=$path');
        Navigator.of(context).pop(path);
        return;
      }

      setState(() {
        _isDone = true;
        _errorMessage =
            AppLocalizations.of(context)!.updatesDialogDownloadFailed;
      });
      LogService.log('⚠️ update.dialog download finished with null path',
          level: LogLevel.warn);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isDone = true;
        _errorMessage =
            '${AppLocalizations.of(context)!.updatesDialogDownloadFailed} ($e)';
      });
      LogService.log('❌ update.dialog exception during download',
          error: e, level: LogLevel.error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final canClose = _isDone;
    return PopScope(
      canPop: canClose,
      child: AlertDialog(
        title: Text(_errorMessage == null
            ? l10n.updatesDialogDownloadingTitle
            : l10n.updatesDialogDownloadFailedTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            LinearProgressIndicator(
                value: _progress > 0 && _progress < 1 ? _progress : null),
            const SizedBox(height: 12),
            Text(
              _errorMessage ??
                  (_progress > 0
                      ? '${(_progress * 100).toStringAsFixed(0)}%'
                      : l10n.updatesDialogPreparing),
            ),
            if (widget.info.assetName != null) ...[
              const SizedBox(height: 8),
              Text(
                widget.info.assetName!,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ],
        ),
        actions: [
          if (canClose)
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: Text(l10n.updatesDialogClose),
            ),
        ],
      ),
    );
  }
}

class _LogsDialog extends StatefulWidget {
  final List<LogEntry> entries;

  const _LogsDialog({required this.entries});

  @override
  State<_LogsDialog> createState() => _LogsDialogState();
}

class _LogsDialogState extends State<_LogsDialog> {
  late final ScrollController _scrollController;
  LogLevel? _selectedLevel;

  int get _infoCount =>
      widget.entries.where((e) => e.level == LogLevel.info).length;
  int get _warnCount =>
      widget.entries.where((e) => e.level == LogLevel.warn).length;
  int get _errorCount =>
      widget.entries.where((e) => e.level == LogLevel.error).length;

  List<LogEntry> get _filteredEntries {
    if (_selectedLevel == null) return widget.entries;
    return widget.entries.where((e) => e.level == _selectedLevel).toList();
  }

  String _filteredText(AppLocalizations l10n) {
    if (_filteredEntries.isEmpty) return l10n.logsNoEntries;
    return _filteredEntries.map((e) => e.toDisplayString()).join('\n');
  }

  Color _levelColor(LogLevel level, BuildContext context) {
    switch (level) {
      case LogLevel.info:
        return Theme.of(context).colorScheme.primary;
      case LogLevel.warn:
        return Colors.orange;
      case LogLevel.error:
        return Colors.redAccent;
    }
  }

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AlertDialog(
      title: Text(l10n.logsTitle),
      content: SizedBox(
        width: double.maxFinite,
        height: 400,
        child: Column(
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ChoiceChip(
                  label: Text(l10n.logsAll),
                  selected: _selectedLevel == null,
                  onSelected: (_) => setState(() => _selectedLevel = null),
                ),
                ChoiceChip(
                  label: Text(l10n.logsInfo),
                  selected: _selectedLevel == LogLevel.info,
                  onSelected: (_) =>
                      setState(() => _selectedLevel = LogLevel.info),
                ),
                ChoiceChip(
                  label: Text(l10n.logsWarn),
                  selected: _selectedLevel == LogLevel.warn,
                  onSelected: (_) =>
                      setState(() => _selectedLevel = LogLevel.warn),
                ),
                ChoiceChip(
                  label: Text(l10n.logsError),
                  selected: _selectedLevel == LogLevel.error,
                  onSelected: (_) =>
                      setState(() => _selectedLevel = LogLevel.error),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                l10n.logsSummary(
                  widget.entries.length.toString(),
                  _infoCount.toString(),
                  _warnCount.toString(),
                  _errorCount.toString(),
                ),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            const SizedBox(height: 8),
            Expanded(
              child: _filteredEntries.isEmpty
                  ? Center(child: Text(l10n.logsNoEntries))
                  : Scrollbar(
                      controller: _scrollController,
                      thumbVisibility: true,
                      child: ListView.separated(
                        controller: _scrollController,
                        itemCount: _filteredEntries.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 8),
                        itemBuilder: (context, index) {
                          final entry = _filteredEntries[index];
                          final color = _levelColor(entry.level, context);
                          return Container(
                            padding: const EdgeInsets.all(10),
                            decoration: BoxDecoration(
                              color: color.withValues(alpha: 0.08),
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(
                                  color: color.withValues(alpha: 0.3)),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 8, vertical: 2),
                                      decoration: BoxDecoration(
                                        color: color.withValues(alpha: 0.18),
                                        borderRadius:
                                            BorderRadius.circular(999),
                                      ),
                                      child: Text(
                                        '${entry.levelIcon} ${entry.levelLabel}',
                                        style: TextStyle(
                                          color: color,
                                          fontWeight: FontWeight.w700,
                                          fontSize: 11,
                                        ),
                                      ),
                                    ),
                                    const Spacer(),
                                    Text(
                                      entry.formattedTime,
                                      style: Theme.of(context)
                                          .textTheme
                                          .bodySmall
                                          ?.copyWith(fontFamily: 'monospace'),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                SelectableText(
                                  entry.message,
                                  style: Theme.of(context).textTheme.bodySmall,
                                ),
                                if (entry.errorText != null &&
                                    entry.errorText!.trim().isNotEmpty) ...[
                                  const SizedBox(height: 6),
                                  SelectableText(
                                    '${l10n.logsErrorPrefix}: ${entry.errorText}',
                                    style: Theme.of(context)
                                        .textTheme
                                        .bodySmall
                                        ?.copyWith(
                                          color: Theme.of(context)
                                              .colorScheme
                                              .error,
                                          fontFamily: 'monospace',
                                        ),
                                  ),
                                ],
                              ],
                            ),
                          );
                        },
                      ),
                    ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () async {
            await Clipboard.setData(
              ClipboardData(text: _filteredText(l10n)),
            );
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text(l10n.logsCopied)),
              );
            }
          },
          child: Text(l10n.logsCopyFiltered),
        ),
        TextButton(
          onPressed: () {
            LogService.clear();
            Navigator.pop(context);
          },
          child: Text(l10n.clear),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l10n.updatesDialogClose),
        ),
      ],
    );
  }
}

class HardwareSettingsSection extends StatelessWidget {
  final AppStateProvider provider;
  final bool enableBlur;

  const HardwareSettingsSection({
    super.key,
    required this.provider,
    this.enableBlur = true,
  });

  void _showEditDialog(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    // Ініціалізуємо контролери поточними значеннями з провайдера
    final batteryCtrl = TextEditingController(
        text: provider.batteryCapacityAh.toStringAsFixed(0));
    final pvCtrl = TextEditingController(
        text: provider.pvTotalCapacityW.toStringAsFixed(0));
    final inverterCtrl = TextEditingController(
        text: provider.inverterMaxPowerW.toStringAsFixed(0));

    final isDark = Theme.of(context).brightness == Brightness.dark;

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Row(
          children: [
            Icon(Icons.solar_power_rounded,
                color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 12),
            Text(l10n.stationParameters, style: const TextStyle(fontSize: 20)),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                l10n.stationParametersHint,
                style: TextStyle(
                    fontSize: 13,
                    color: Theme.of(context).colorScheme.onSurfaceVariant),
              ),
              const SizedBox(height: 20),
              _buildTextField(context, batteryCtrl, l10n.batteryCapacityLabel,
                  'Ah', Icons.battery_charging_full_rounded),
              const SizedBox(height: 16),
              _buildTextField(context, pvCtrl, l10n.panelPowerLabel, 'W',
                  Icons.grid_4x4_rounded),
              const SizedBox(height: 16),
              _buildTextField(context, inverterCtrl, l10n.inverterPowerLabel,
                  'W', Icons.bolt_rounded),
            ],
          ),
        ),
        actionsPadding: const EdgeInsets.only(right: 16, bottom: 16),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l10n.cancel,
                style:
                    TextStyle(color: isDark ? Colors.white70 : Colors.black54)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.primary,
              foregroundColor: Theme.of(context).colorScheme.onPrimary,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12)),
            ),
            onPressed: () {
              // Парсимо значення, якщо поле пусте або з помилкою - беремо старе значення
              final bat = double.tryParse(batteryCtrl.text) ??
                  provider.batteryCapacityAh;
              final pv =
                  double.tryParse(pvCtrl.text) ?? provider.pvTotalCapacityW;
              final inv = double.tryParse(inverterCtrl.text) ??
                  provider.inverterMaxPowerW;

              provider.saveHardwareSettings(bat, pv, inv);
              Navigator.pop(context); // Закриваємо діалог

              // Візуальний фідбек для користувача
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text(l10n.hardwareSettingsSaved),
                  duration: Duration(seconds: 2),
                ),
              );
            },
            child:
                Text(l10n.save, style: TextStyle(fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  Widget _buildTextField(BuildContext context, TextEditingController controller,
      String label, String suffix, IconData icon) {
    return TextField(
      controller: controller,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      // Дозволяємо вводити тільки цифри та крапку
      inputFormatters: [
        FilteringTextInputFormatter.allow(RegExp(r'^\d+\.?\d*'))
      ],
      decoration: InputDecoration(
        labelText: label,
        suffixText: suffix,
        prefixIcon:
            Icon(icon, color: Theme.of(context).colorScheme.onSurfaceVariant),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AppCard(
      borderRadius: AppTheme.radiusLarge,
      enableBlur: enableBlur,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => _showEditDialog(context),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Row(
            children: [
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  shape: BoxShape.circle,
                ),
                child: Icon(Icons.solar_power_rounded,
                    color: Theme.of(context).colorScheme.onPrimaryContainer),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(l10n.stationParameters,
                        style: const TextStyle(
                            fontSize: 16, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 4),
                    Text(
                      l10n.hardwareSummary(
                        provider.batteryCapacityAh.toInt().toString(),
                        provider.pvTotalCapacityW.toInt().toString(),
                        provider.inverterMaxPowerW.toInt().toString(),
                      ),
                      style: TextStyle(
                          fontSize: 13,
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                          height: 1.4),
                    ),
                  ],
                ),
              ),
              Icon(Icons.chevron_right_rounded,
                  color: Theme.of(context).colorScheme.onSurfaceVariant),
            ],
          ),
        ),
      ),
    );
  }
}

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../services/log_service.dart';
import '../services/update_service.dart';

class SettingsTab extends StatelessWidget {
  final AppStateProvider provider;

  const SettingsTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // Блок Акаунта
        _buildSectionTitle(l10n.account),
        _buildAccountCard(context, l10n),

        const SizedBox(height: 24),

        // Блок Налаштувань Додатка
        _buildSectionTitle(l10n.appSettings),
        const SizedBox(height: 16),
        HardwareSettingsSection(provider: provider), // <--- Додаємо сюди
        const SizedBox(height: 16),
        Container(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Material(
            color: Colors.transparent,
            child: Column(
              children: [
                ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  leading: const Icon(Icons.language, color: Colors.blueAccent),
                  title: Text(l10n.language),
                  trailing: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      value: provider.lang,
                      items: const [
                        DropdownMenuItem(value: 'en', child: Text('English')),
                        DropdownMenuItem(
                            value: 'uk', child: Text('Українська')),
                      ],
                      onChanged: (val) {
                        if (val != null) provider.setLanguage(val);
                      },
                    ),
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  leading:
                      const Icon(Icons.palette, color: Colors.orangeAccent),
                  title: Text(l10n.theme),
                  trailing: Switch(
                    value: provider.themeMode == ThemeMode.dark,
                    activeThumbColor: Colors.amber,
                    onChanged: (val) => provider.toggleTheme(),
                  ),
                ),
                const Divider(height: 1),
                SwitchListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  secondary: const Icon(Icons.power_settings_new,
                      color: Colors.greenAccent),
                  title: Text(l10n.startWithWindows),
                  value: provider.isAutostartEnabled,
                  activeThumbColor: Colors.greenAccent,
                  activeTrackColor: Colors.greenAccent.withValues(alpha: 0.3),
                  onChanged: provider.toggleAutostart,
                ),
                const Divider(height: 1),
                ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  leading: const Icon(Icons.update, color: Colors.blueAccent),
                  title: const Text('Check for Updates'),
                  subtitle: const Text('Check and install latest version'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _checkForUpdates(context),
                ),
                if (provider.isDeveloperMode) ...[
                  _buildSectionTitle('Debug Logs'),
                  Container(
                    decoration: BoxDecoration(
                      color: Theme.of(context).cardColor,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Material(
                      color: Colors.transparent,
                      child: ListTile(
                        leading: const Icon(Icons.bug_report,
                            color: Colors.redAccent),
                        title: const Text('View System Logs'),
                        subtitle:
                            const Text('Analyze app errors and API calls'),
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
                          fontSize: 12),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSectionTitle(String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12, left: 4),
      child: Text(title,
          style: const TextStyle(
              fontSize: 16, fontWeight: FontWeight.bold, color: Colors.grey)),
    );
  }

  Widget _buildAccountCard(BuildContext context, AppLocalizations l10n) {
    final theme = Theme.of(context);

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

    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: theme.cardColor,
        borderRadius: BorderRadius.circular(24),
      ),
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
                    backgroundColor: Colors.amber,
                    child: Icon(Icons.person, size: 30, color: Colors.black),
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
                            ? Colors.orange.withValues(alpha: 0.18)
                            : Colors.green.withValues(alpha: 0.14),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Text(
                        statusLabel,
                        style: TextStyle(
                          color: provider.userData == null
                              ? Colors.orange[800]
                              : Colors.green[700],
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
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              color: theme.brightness == Brightness.dark
                  ? Colors.white.withValues(alpha: 0.03)
                  : Colors.black.withValues(alpha: 0.03),
            ),
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
                    foregroundColor: Colors.redAccent,
                    side: const BorderSide(color: Colors.redAccent),
                  ),
                  icon: const Icon(Icons.logout, size: 18),
                  label: Text(l10n.logout),
                ),
              ),
            ],
          ),
        ],
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
          Icon(icon, size: 17, color: Colors.grey),
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
    final scaffoldMessenger = ScaffoldMessenger.of(context);

    unawaited(showDialog(
      // ignore: unawaited_futures
      context: context,
      barrierDismissible: false,
      builder: (context) => const AlertDialog(
        content: Row(
          children: [
            CircularProgressIndicator(),
            SizedBox(width: 16),
            Text('Checking for updates...'),
          ],
        ),
      ),
    ));

    final info = await UpdateService.fetchUpdateInfo();
    if (!context.mounted) return;
    Navigator.pop(context); // Close loading dialog

    if (info.hasUpdate) {
      if (!context.mounted) return;
      await _showUpdateAvailableDialog(context, info);
    } else {
      scaffoldMessenger.showSnackBar(
        SnackBar(
          content: Text('You are up to date (${info.currentVersion}).'),
        ),
      );
    }
  }

  Future<void> _showUpdateAvailableDialog(
      BuildContext context, UpdateInfo info) async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Update Available'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Current: ${info.currentVersion}'),
            Text('Latest: ${info.latestVersion}'),
            const SizedBox(height: 8),
            Text(
                'Published: ${UpdateService.formatPublishedAt(info.publishedAt)}'),
            if (info.assetName != null) ...[
              const SizedBox(height: 4),
              Text('Package: ${info.assetName}'),
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
            onPressed: () => Navigator.pop(context),
            child: const Text('Later'),
          ),
          ElevatedButton.icon(
            onPressed: () async {
              Navigator.pop(context);
              await _downloadAndInstallUpdate(context, info);
            },
            icon: const Icon(Icons.download_rounded),
            label: const Text('Download'),
          ),
        ],
      ),
    );
  }

  Future<void> _downloadAndInstallUpdate(
      BuildContext context, UpdateInfo info) async {
    final scaffoldMessenger = ScaffoldMessenger.of(context);
    if (info.downloadUrl == null || info.assetName == null) {
      scaffoldMessenger.showSnackBar(
        const SnackBar(
          content: Text('No compatible installer was found in the release.'),
        ),
      );
      return;
    }

    final progressNotifier = ValueNotifier<double>(0.0);

    unawaited(showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Downloading update'),
        content: ValueListenableBuilder<double>(
          valueListenable: progressNotifier,
          builder: (context, progress, _) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LinearProgressIndicator(value: progress > 0 ? progress : null),
              const SizedBox(height: 12),
              Text(progress > 0
                  ? '${(progress * 100).toStringAsFixed(0)}%'
                  : 'Preparing download...'),
            ],
          ),
        ),
      ),
    ));

    final path = await UpdateService.downloadUpdateAsset(
      downloadUrl: info.downloadUrl!,
      fileName: info.assetName!,
      onProgress: (value) {
        progressNotifier.value = value;
      },
    );
    progressNotifier.dispose();
    if (!context.mounted) return;
    Navigator.pop(context); // Close downloading dialog

    if (path != null) {
      if (!context.mounted) return;
      unawaited(showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Install Update'),
          content: Text(
              'Update downloaded (${info.latestVersion}). Install now? The app will close during installation.'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            ElevatedButton(
              onPressed: () async {
                Navigator.pop(context);
                final success = await UpdateService.installUpdate(path);
                if (success) {
                  // Exit app
                  exit(0);
                } else {
                  scaffoldMessenger.showSnackBar(
                    const SnackBar(
                        content: Text(
                            'Installation failed. Please run installer manually.')),
                  );
                }
              },
              child: const Text('Install'),
            ),
          ],
        ),
      ));
    } else {
      scaffoldMessenger.showSnackBar(
        const SnackBar(
            content:
                Text('Download failed. Check internet or release assets.')),
      );
    }
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

  String get _filteredText {
    if (_filteredEntries.isEmpty) return 'No logs yet.';
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
    return AlertDialog(
      title: const Text('App Logs'),
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
                  label: const Text('All'),
                  selected: _selectedLevel == null,
                  onSelected: (_) => setState(() => _selectedLevel = null),
                ),
                ChoiceChip(
                  label: const Text('Info'),
                  selected: _selectedLevel == LogLevel.info,
                  onSelected: (_) =>
                      setState(() => _selectedLevel = LogLevel.info),
                ),
                ChoiceChip(
                  label: const Text('Warn'),
                  selected: _selectedLevel == LogLevel.warn,
                  onSelected: (_) =>
                      setState(() => _selectedLevel = LogLevel.warn),
                ),
                ChoiceChip(
                  label: const Text('Error'),
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
                'Total: ${widget.entries.length}  |  Info: $_infoCount  Warn: $_warnCount  Error: $_errorCount',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            const SizedBox(height: 8),
            Expanded(
              child: _filteredEntries.isEmpty
                  ? const Center(child: Text('No logs yet.'))
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
                                    'Error: ${entry.errorText}',
                                    style: Theme.of(context)
                                        .textTheme
                                        .bodySmall
                                        ?.copyWith(
                                          color: Colors.redAccent,
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
            await Clipboard.setData(ClipboardData(text: _filteredText));
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Logs copied to clipboard')),
              );
            }
          },
          child: const Text('Copy filtered'),
        ),
        TextButton(
          onPressed: () {
            LogService.clear();
            Navigator.pop(context);
          },
          child: const Text('Clear'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    );
  }
}

class HardwareSettingsSection extends StatelessWidget {
  final AppStateProvider provider;

  const HardwareSettingsSection({super.key, required this.provider});

  void _showEditDialog(BuildContext context) {
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
                color: isDark ? Colors.amber : Colors.orange),
            const SizedBox(width: 12),
            const Text('Параметри станції', style: TextStyle(fontSize: 20)),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Ці дані потрібні інтелектуальному алгоритму для точного розрахунку енергії та прогнозу погоди.',
                style: TextStyle(fontSize: 13, color: Colors.grey),
              ),
              const SizedBox(height: 20),
              _buildTextField(batteryCtrl, 'Ємність АКБ', 'Ah',
                  Icons.battery_charging_full_rounded),
              const SizedBox(height: 16),
              _buildTextField(
                  pvCtrl, 'Потужність панелей', 'W', Icons.grid_4x4_rounded),
              const SizedBox(height: 16),
              _buildTextField(inverterCtrl, 'Потужність інвертора', 'W',
                  Icons.bolt_rounded),
            ],
          ),
        ),
        actionsPadding: const EdgeInsets.only(right: 16, bottom: 16),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text('Скасувати',
                style:
                    TextStyle(color: isDark ? Colors.white70 : Colors.black54)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: isDark ? Colors.amber : Colors.orange,
              foregroundColor: Colors.black,
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
                const SnackBar(
                  content: Text('Параметри обладнання збережено!'),
                  backgroundColor: Colors.green,
                  duration: Duration(seconds: 2),
                ),
              );
            },
            child: const Text('Зберегти',
                style: TextStyle(fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  Widget _buildTextField(TextEditingController controller, String label,
      String suffix, IconData icon) {
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
        prefixIcon: Icon(icon, color: Colors.grey),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Card(
      elevation: 0,
      color: isDark ? Colors.grey[900] : Colors.grey[100],
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(
            color: isDark ? Colors.grey[800]! : Colors.grey[300]!, width: 1),
      ),
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
                  color: (isDark ? Colors.amber : Colors.orange)
                      .withValues(alpha: 0.2),
                  shape: BoxShape.circle,
                ),
                child: Icon(Icons.solar_power_rounded,
                    color: isDark ? Colors.amber : Colors.orange),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Параметри обладнання',
                        style: TextStyle(
                            fontSize: 16, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 4),
                    Text(
                      'АКБ: ${provider.batteryCapacityAh.toInt()} Ah • PV: ${provider.pvTotalCapacityW.toInt()} W\nІнвертор: ${provider.inverterMaxPowerW.toInt()} W',
                      style: const TextStyle(
                          fontSize: 13, color: Colors.grey, height: 1.4),
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: Colors.grey),
            ],
          ),
        ),
      ),
    );
  }
}

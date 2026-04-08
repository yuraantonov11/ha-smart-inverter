import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
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
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Column(
            children: [
              Stack(
                children: [
                  const CircleAvatar(
                    radius: 40,
                    backgroundColor: Colors.amber,
                    child: Icon(Icons.person, size: 40, color: Colors.black),
                  ),
                  const SizedBox(height: 16),
                  Positioned(
                    right: 0,
                    bottom: 0,
                    child: Container(
                      padding: const EdgeInsets.all(4),
                      decoration: const BoxDecoration(
                          color: Colors.blue, shape: BoxShape.circle),
                      child: InkWell(
                        onTap: () => _showEditProfileDialog(context),
                        child: const Icon(Icons.edit,
                            size: 16, color: Colors.white),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    provider.displayName, // "yuraantonov11" з профілю
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  Text(
                    provider.displayEmail, // "y************@gmail.com"
                    style: const TextStyle(color: Colors.grey),
                  ),
                  if (provider.displayPhone.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        'Тел: ${provider.displayPhone}', // "380*****8414"
                        style:
                            const TextStyle(color: Colors.grey, fontSize: 13),
                      ),
                    ),
                  const SizedBox(height: 12),
                  // Вивід ID користувача з HAR
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.blueGrey.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      "UID: ${provider.userData?['uid'] ?? '...'}", // "5xjt6zu9gq"
                      style: const TextStyle(
                          fontSize: 11, fontFamily: 'monospace'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text(
                  provider.userName ??
                      l10n.userNameDefault, // Використання динамічного імені
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.bold)),
              Text(provider.savedEmail ?? '',
                  style: const TextStyle(color: Colors.grey, fontSize: 14)),
              const SizedBox(height: 8),
              Text(
                  l10n.userId(provider.userId
                      .toString()), // Відображення ID з провайдера
                  style: const TextStyle(color: Colors.grey, fontSize: 12)),
              const SizedBox(height: 20),
              // Кнопка Виходу
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: provider.logout,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.redAccent,
                    side: const BorderSide(color: Colors.redAccent),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12)),
                  ),
                  icon: const Icon(Icons.logout, size: 18),
                  label: Text(l10n.logout),
                ),
              ),
            ],
          ),
        ),

        const SizedBox(height: 24),

        // Блок Налаштувань Додатка
        _buildSectionTitle(l10n.appSettings),
        Container(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Column(
            children: [
              _buildSettingTile(
                context: context,
                title: 'Звуковий сигнал',
                subtitle: 'Увімкнути/вимкнути системну пищалку',
                // Перевіряємо ключ (в Siseli зазвичай 'buzzerSwitchSetting' або 'buzzerSwitch')
                currentValue: provider.data?.rawFields['fullConfigs']
                        ?['buzzerSwitchSetting'] ==
                    '1',
                onChanged: (val) {
                  provider.changeInverterSetting(
                      'buzzerSwitchSetting', val ? '1' : '0');
                },
              ),
              const Divider(height: 1),
            ],
          ),
        ),
        const SizedBox(height: 24),
        Container(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          // Додано Material з прозорим фоном для уникнення помилки ListTile
          child: Material(
            color: Colors.transparent,
            child: Column(
              children: [
                // Вибір мови
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
                // Вибір Теми
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
                // Автозапуск
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
                // Перевірка оновлень
                ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  leading: const Icon(Icons.update, color: Colors.blueAccent),
                  title: const Text('Check for Updates'),
                  subtitle: const Text('Check and install latest version'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _checkForUpdates(context),
                ),
                // 1. Секція внизу списку
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
                  onTap: provider.handleVersionClick, // Ті самі 7 кліків
                  child: Center(
                    child: Text(
                      'Version 1.0.4+26',
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

  Widget _buildSettingTile({
    required String title,
    required String subtitle,
    required bool currentValue,
    required Function(bool) onChanged,
    required BuildContext context,
  }) {
    return ListTile(
      title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(subtitle,
          style: const TextStyle(fontSize: 12, color: Colors.grey)),
      trailing: provider.isSettingChanging
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: Colors.amber),
            )
          : Switch(
              value: currentValue,
              activeThumbColor: Colors.amber,
              onChanged: onChanged,
            ),
    );
  }

  void _showLogsDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('App Logs'),
        content: SizedBox(
          width: double.maxFinite,
          height: 400,
          child: ListView.builder(
            itemCount: LogService.allLogs.length,
            itemBuilder: (context, i) => Text(
              LogService.allLogs[i],
              style: const TextStyle(fontSize: 10, fontFamily: 'monospace'),
            ),
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => LogService.clear(), child: const Text('Clear')),
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close')),
        ],
      ),
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
              provider.updateProfile(controller.text);
              Navigator.pop(context);
            },
            child: Text(l10n.save),
          ),
        ],
      ),
    );
  }

  void _checkForUpdates(BuildContext context) async {
    final scaffoldMessenger = ScaffoldMessenger.of(context);

    unawaited(showDialog(
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

    final hasUpdate = await UpdateService.checkForUpdate();
    if (!context.mounted) return;
    Navigator.pop(context); // Close loading dialog

    if (hasUpdate) {
      if (!context.mounted) return;
      unawaited(showDialog(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Update Available'),
          content: const Text(
              'A new version is available. Do you want to download and install it?'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            ElevatedButton(
              onPressed: () async {
                Navigator.pop(context); // Close update dialog
                await _downloadAndInstallUpdate(context);
              },
              child: const Text('Update'),
            ),
          ],
        ),
      ));
    } else {
      scaffoldMessenger.showSnackBar(
        const SnackBar(content: Text('You are up to date!')),
      );
    }
  }

  Future<void> _downloadAndInstallUpdate(BuildContext context) async {
    final scaffoldMessenger = ScaffoldMessenger.of(context);

    unawaited(showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => const AlertDialog(
        content: Row(
          children: [
            CircularProgressIndicator(),
            SizedBox(width: 16),
            Text('Downloading update...'),
          ],
        ),
      ),
    ));

    final path = await UpdateService.downloadUpdate();
    if (!context.mounted) return;
    Navigator.pop(context); // Close downloading dialog

    if (path != null) {
      if (!context.mounted) return;
      unawaited(showDialog(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Install Update'),
          content: const Text(
              'Update downloaded. Install now? The app will close during installation.'),
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
                    const SnackBar(content: Text('Installation failed.')),
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
        const SnackBar(content: Text('Download failed.')),
      );
    }
  }
}

import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';

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
              Text(l10n.userId(provider.userId), // Відображення ID з провайдера
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
}

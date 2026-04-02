import 'package:flutter/material.dart';
import '../providers/app_provider.dart';

class SettingsTab extends StatelessWidget {
  final AppStateProvider provider;

  const SettingsTab({super.key, required this.provider});

  @override
  Widget build(BuildContext context) {
    final isEn = provider.isEn;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // Блок Акаунта
        _buildSectionTitle(isEn ? "Account" : "Акаунт"),
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(24),
          ),
          child: Column(
            children: [
              const CircleAvatar(
                radius: 40,
                backgroundColor: Colors.amber,
                child: Icon(Icons.person, size: 40, color: Colors.black),
              ),
              const SizedBox(height: 16),
              Text(provider.savedEmail ?? "User",
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: () => provider.logout(),
                  icon: const Icon(Icons.logout, color: Colors.redAccent),
                  label: Text(isEn ? "Log Out" : "Вийти з акаунту",
                      style: const TextStyle(color: Colors.redAccent)),
                  style: OutlinedButton.styleFrom(
                    side: const BorderSide(color: Colors.redAccent),
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(16)),
                  ),
                ),
              )
            ],
          ),
        ),

        const SizedBox(height: 24),

        // Блок Налаштувань Додатка
        _buildSectionTitle(
            isEn ? "Application Settings" : "Налаштування застосунку"),
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
                  title: Text(isEn ? "Language" : "Мова"),
                  trailing: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      value: provider.lang,
                      items: const [
                        DropdownMenuItem(value: 'en', child: Text("English")),
                        DropdownMenuItem(
                            value: 'uk', child: Text("Українська")),
                      ],
                      onChanged: (val) {
                        if (val != null) provider.setLanguage(val);
                      },
                    ),
                  ),
                ),
                const Divider(height: 1),
                // Автозапуск
                SwitchListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  secondary: const Icon(Icons.power_settings_new,
                      color: Colors.greenAccent),
                  title: Text(
                      isEn ? "Start with Windows" : "Автозапуск з Windows"),
                  value: provider.isAutostartEnabled,
                  activeThumbColor: Colors.greenAccent,
                  activeTrackColor: Colors.greenAccent.withValues(alpha: 0.3),
                  onChanged: provider.toggleAutostart,
                ),
              ],
            ),
          ),
        ),

        const SizedBox(height: 40),

        // Версія застосунку
        Center(
          child: Column(
            children: [
              Text(
                "Smart Inverter Desktop",
                style: TextStyle(
                  color: Colors.grey.withValues(alpha: 0.5),
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                "v${AppStateProvider.appVersion}",
                style: TextStyle(
                  color: Colors.grey.withValues(alpha: 0.5),
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
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
}

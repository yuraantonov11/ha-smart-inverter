import 'package:flutter/material.dart';
import '../l10n/app_localizations.dart';
import '../providers/app_provider.dart';
import '../theme/app_theme.dart';
import './app_components.dart';

class ControlPanel extends StatelessWidget {
  final AppStateProvider provider;

  const ControlPanel({super.key, required this.provider});

  void _showSettingsModal(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).cardColor,
      shape: const RoundedRectangleBorder(
          borderRadius:
              BorderRadius.vertical(top: Radius.circular(AppTheme.radiusXL))),
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: EdgeInsets.only(
                bottom: MediaQuery.of(context).viewInsets.bottom),
            child: _SettingsModal(provider: provider),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final currentOutputPriority = provider
            .data?.rawFields['outputSourcePriority']?['value']
            ?.toString() ??
        '2';

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.inverterMode,
                  style: Theme.of(context).textTheme.titleLarge),
              Tooltip(
                message: l10n.advancedSettings,
                child: IconButton(
                  icon: const Icon(Icons.settings_outlined),
                  onPressed: () => _showSettingsModal(context),
                ),
              )
            ],
          ),
          const SizedBox(height: AppTheme.spacingL),
          Row(
            children: [
              Expanded(
                child: AppModeButton(
                  title: l10n.solarSbu,
                  subtitle: l10n.modeFromSolar,
                  icon: Icons.wb_sunny_rounded,
                  isActive: currentOutputPriority == '2',
                  activeColor: const Color(0xFFF59E0B),
                  onTap: () {
                    provider.hemsService.armManualOverride();
                    provider.setMode(2);
                  },
                ),
              ),
              const SizedBox(width: AppTheme.spacingL),
              Expanded(
                child: AppModeButton(
                  title: l10n.gridUsb,
                  subtitle: l10n.modeFromGrid,
                  icon: Icons.power_rounded,
                  isActive: currentOutputPriority == '0',
                  activeColor: const Color(0xFF06B6D4),
                  onTap: () {
                    provider.hemsService.armManualOverride();
                    provider.setMode(0);
                  },
                ),
              ),
            ],
          )
        ],
      ),
    );
  }
}

class _SettingsModal extends StatelessWidget {
  final AppStateProvider provider;

  const _SettingsModal({required this.provider});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final fields = provider.data?.rawFields ?? {};
    final outputPriority =
        fields['outputSourcePriority']?['value']?.toString() ?? '2';
    final chargerPriority =
        fields['chargerSourcePriority']?['value']?.toString() ?? '0';

    return Padding(
      padding: const EdgeInsets.all(AppTheme.spacingXL),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.advancedSettings,
              style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: AppTheme.spacing2XL),
          AppSectionTitle(
            title: l10n.outputSourcePriority,
            icon: Icons.power_rounded,
          ),
          const SizedBox(height: AppTheme.spacingM),
          _buildDropdown(
            context,
            value: outputPriority,
            items: [
              DropdownMenuItem(value: '0', child: Text(l10n.utilityFirstUsb)),
              DropdownMenuItem(value: '1', child: Text(l10n.solarFirstSub)),
              DropdownMenuItem(value: '2', child: Text(l10n.sbuPriority)),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.hemsService.armManualOverride();
                provider.changeSetting('outputSourcePrioritySetting', val);
                Navigator.pop(context);
              }
            },
          ),
          const SizedBox(height: AppTheme.spacing2XL),
          AppSectionTitle(
            title: l10n.chargerSourcePriority,
            icon: Icons.bolt_rounded,
          ),
          const SizedBox(height: AppTheme.spacingM),
          _buildDropdown(
            context,
            value: chargerPriority,
            items: [
              DropdownMenuItem(value: '0', child: Text(l10n.solarFirst)),
              DropdownMenuItem(value: '1', child: Text(l10n.solarUtilitySnu)),
              DropdownMenuItem(value: '2', child: Text(l10n.onlySolar)),
            ],
            onChanged: (val) {
              if (val != null) {
                provider.changeSetting('chargerSourcePrioritySetting', val);
                Navigator.pop(context);
              }
            },
          ),
          const SizedBox(height: AppTheme.spacingL),
        ],
      ),
    );
  }

  Widget _buildDropdown(BuildContext context,
      {required String value,
      required List<DropdownMenuItem<String>> items,
      required void Function(String?) onChanged}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTheme.spacingL),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
        border: Border.all(color: Theme.of(context).dividerColor),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: items.any((item) => item.value == value)
              ? value
              : items.first.value,
          isExpanded: true,
          icon: Icon(Icons.arrow_drop_down_rounded,
              color: Theme.of(context).colorScheme.primary),
          items: items,
          onChanged: onChanged,
        ),
      ),
    );
  }
}

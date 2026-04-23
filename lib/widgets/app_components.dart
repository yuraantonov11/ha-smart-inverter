import 'dart:ui';

import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

// ======================== КАРТОЧКИ ========================

/// Базова карточка з тінню та радіусом
class AppCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final Color? backgroundColor;
  final double borderRadius;

  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppTheme.spacingL),
    this.onTap,
    this.backgroundColor,
    this.borderRadius = AppTheme.radiusLarge,
  });

  @override
  Widget build(BuildContext context) {
    Widget content = AppGlassSurface(
      borderRadius: borderRadius,
      backgroundColor: backgroundColor,
      child: Padding(
        padding: padding,
        child: child,
      ),
    );

    if (onTap != null) {
      content = Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(borderRadius),
          child: content,
        ),
      );
    }

    return content;
  }
}

class AppGlassSurface extends StatelessWidget {
  final Widget child;
  final double borderRadius;
  final Color? backgroundColor;
  final bool isStrong;

  const AppGlassSurface({
    super.key,
    required this.child,
    this.borderRadius = AppTheme.radiusLarge,
    this.backgroundColor,
    this.isStrong = false,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return ClipRRect(
      borderRadius: BorderRadius.circular(borderRadius),
      child: BackdropFilter(
        filter: ImageFilter.blur(
          sigmaX: isStrong ? 18 : 10,
          sigmaY: isStrong ? 18 : 10,
        ),
        child: Container(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                (backgroundColor ?? theme.cardColor).withValues(
                    alpha: isDark
                        ? (isStrong ? 0.84 : 0.74)
                        : (isStrong ? 1.0 : 0.98)),
                theme.colorScheme.surface.withValues(
                    alpha: isDark
                        ? (isStrong ? 0.72 : 0.58)
                        : (isStrong ? 0.98 : 0.94)),
              ],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            borderRadius: BorderRadius.circular(borderRadius),
            border: Border.all(
              color: isDark
                  ? Colors.white.withValues(alpha: isStrong ? 0.18 : 0.14)
                  : theme.dividerColor.withValues(alpha: isStrong ? 1 : 0.9),
              width: 1,
            ),
            boxShadow: [
              BoxShadow(
                color: (isDark ? Colors.black : Colors.blueGrey).withValues(
                    alpha: isDark
                        ? (isStrong ? 0.36 : 0.3)
                        : (isStrong ? 0.16 : 0.12)),
                blurRadius: isStrong ? 28 : 20,
                offset: Offset(0, isStrong ? 12 : 8),
              ),
            ],
          ),
          child: child,
        ),
      ),
    );
  }
}

// ======================== СТАТУС БАНЕР ========================

class AppStatusBanner extends StatelessWidget {
  final String message;
  final IconData icon;
  final Color? color;
  final String? meta;

  const AppStatusBanner({
    super.key,
    required this.message,
    required this.icon,
    this.color,
    this.meta,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final bannerColor = color ?? theme.colorScheme.primary;

    return AppGlassSurface(
      borderRadius: AppTheme.radiusLarge,
      backgroundColor: theme.cardColor.withValues(alpha: isDark ? 0.36 : 0.62),
      child: Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AppTheme.radiusLarge),
          gradient: LinearGradient(
            colors: [
              bannerColor.withValues(alpha: isDark ? 0.12 : 0.08),
              Colors.transparent,
            ],
            begin: Alignment.centerLeft,
            end: Alignment.centerRight,
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            vertical: AppTheme.spacingS,
            horizontal: AppTheme.spacingM,
          ),
          child: Row(
            children: [
              Container(
                width: 30,
                height: 30,
                decoration: BoxDecoration(
                  color: bannerColor.withValues(alpha: 0.16),
                  borderRadius: BorderRadius.circular(999),
                  border:
                      Border.all(color: bannerColor.withValues(alpha: 0.42)),
                  boxShadow: [
                    BoxShadow(
                      color: bannerColor.withValues(alpha: isDark ? 0.28 : 0.2),
                      blurRadius: 12,
                      spreadRadius: -2,
                    ),
                  ],
                ),
                child: Icon(icon, color: bannerColor, size: 16),
              ),
              const SizedBox(width: AppTheme.spacingS),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      message,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        color: theme.colorScheme.onSurface,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (meta != null && meta!.trim().isNotEmpty)
                      Text(
                        meta!,
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
        ),
      ),
    );
  }
}

// ======================== SECTION TITLE ========================

class AppSectionTitle extends StatelessWidget {
  final String title;
  final String? subtitle;
  final IconData? icon;

  const AppSectionTitle({
    super.key,
    required this.title,
    this.subtitle,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(
        bottom: AppTheme.spacingM,
        left: AppTheme.spacingXS,
        top: AppTheme.spacingS,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (icon != null) ...[
                Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(AppTheme.radiusSmall),
                    color: Theme.of(context)
                        .colorScheme
                        .primary
                        .withValues(alpha: 0.12),
                  ),
                  child: Icon(
                    icon,
                    color: Theme.of(context).colorScheme.primary,
                    size: 16,
                  ),
                ),
                const SizedBox(width: AppTheme.spacingS),
              ],
              Text(
                title,
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      letterSpacing: 0.9,
                    ),
              ),
            ],
          ),
          if (subtitle != null) ...[
            const SizedBox(height: AppTheme.spacingXS),
            Text(
              subtitle!,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

// ======================== STAT CARD ========================

class AppStatCard extends StatelessWidget {
  final String label;
  final String value;
  final IconData icon;
  final Color color;
  final String? unit;
  final String? tooltip;

  const AppStatCard({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
    this.unit,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    final card = AppCard(
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(AppTheme.spacingM),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              border: Border.all(color: color.withValues(alpha: 0.35)),
              boxShadow: [
                BoxShadow(
                  color: color.withValues(alpha: 0.18),
                  blurRadius: 16,
                ),
              ],
            ),
            child: Icon(icon, color: color, size: 24),
          ),
          const SizedBox(height: AppTheme.spacingM),
          Text(
            label,
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: color,
                  letterSpacing: 0.8,
                ),
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(
                value,
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
              ),
              if (unit != null) ...[
                const SizedBox(width: AppTheme.spacingXS),
                Text(
                  unit!,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context)
                            .textTheme
                            .bodySmall
                            ?.color
                            ?.withValues(alpha: 0.85),
                      ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
    if (tooltip != null) {
      return Tooltip(
        message: tooltip!,
        preferBelow: false,
        waitDuration: const Duration(milliseconds: 600),
        child: card,
      );
    }
    return card;
  }
}

// ======================== INFO ROW ========================

class AppInfoRow extends StatelessWidget {
  final String label;
  final String value;
  final IconData? icon;
  final Color? valueColor;

  const AppInfoRow({
    super.key,
    required this.label,
    required this.value,
    this.icon,
    this.valueColor,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTheme.spacingS),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              if (icon != null) ...[
                Icon(icon,
                    size: 18,
                    color: Theme.of(context).textTheme.bodySmall?.color),
                const SizedBox(width: AppTheme.spacingS),
              ],
              Text(
                label,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
          Text(
            value,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: valueColor,
                  fontWeight: FontWeight.w600,
                ),
          ),
        ],
      ),
    );
  }
}

// ======================== PROGRESS BAR ========================

class AppProgressBar extends StatelessWidget {
  final String label;
  final double value;
  final double maxValue;
  final Color color;
  final String? suffix;
  final bool showPercentage;
  final String? tooltip;

  const AppProgressBar({
    super.key,
    required this.label,
    required this.value,
    required this.maxValue,
    required this.color,
    this.suffix,
    this.showPercentage = true,
    this.tooltip,
  });

  @override
  Widget build(BuildContext context) {
    final percent = (value / maxValue).clamp(0.0, 1.0);

    final content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: Theme.of(context).textTheme.bodyMedium),
            Text(
              '${value.toInt()} ${suffix ?? ''} / ${maxValue.toInt()}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
        const SizedBox(height: AppTheme.spacingS),
        ClipRRect(
          borderRadius: BorderRadius.circular(AppTheme.radiusSmall),
          child: LinearProgressIndicator(
            value: percent,
            backgroundColor: color.withValues(alpha: 0.1),
            valueColor: AlwaysStoppedAnimation(color),
            minHeight: 8,
          ),
        ),
        if (showPercentage) ...[
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            '${(percent * 100).toInt()}%',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: color,
                  fontWeight: FontWeight.w600,
                ),
          ),
        ],
      ],
    );
    if (tooltip != null) {
      return Tooltip(
        message: tooltip!,
        preferBelow: false,
        waitDuration: const Duration(milliseconds: 600),
        child: content,
      );
    }
    return content;
  }
}

// ======================== LEGEND ITEM ========================

class AppLegendItem extends StatelessWidget {
  final String label;
  final Color color;
  final bool isActive;
  final VoidCallback? onTap;
  final double size;

  const AppLegendItem({
    super.key,
    required this.label,
    required this.color,
    this.isActive = true,
    this.onTap,
    this.size = 12,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(AppTheme.radiusSmall),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppTheme.spacingXS,
          vertical: AppTheme.spacingXS,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              width: size,
              height: size,
              decoration: BoxDecoration(
                color: isActive ? color : Colors.grey.withValues(alpha: 0.3),
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: AppTheme.spacingS),
            Text(
              label,
              style: TextStyle(
                fontSize: 13,
                color: isActive ? null : Colors.grey,
                decoration: isActive ? null : TextDecoration.lineThrough,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ======================== MODE SELECTOR BUTTON ========================

class AppModeButton extends StatelessWidget {
  final String title;
  final String subtitle;
  final IconData icon;
  final bool isActive;
  final VoidCallback onTap;
  final Color activeColor;

  const AppModeButton({
    super.key,
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.isActive,
    required this.onTap,
    required this.activeColor,
  });

  @override
  Widget build(BuildContext context) {
    return AppCard(
      onTap: onTap,
      backgroundColor: isActive
          ? activeColor.withValues(alpha: 0.15)
          : Theme.of(context).cardColor,
      padding: const EdgeInsets.all(AppTheme.spacingL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(AppTheme.spacingM),
            decoration: BoxDecoration(
              color: activeColor.withValues(alpha: 0.2),
              borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              border: Border.all(
                color: activeColor,
                width: isActive ? 2 : 0,
              ),
            ),
            child: Icon(icon, color: activeColor, size: 32),
          ),
          const SizedBox(height: AppTheme.spacingM),
          Text(
            title,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: isActive ? activeColor : null,
                  fontWeight: isActive ? FontWeight.bold : FontWeight.w500,
                ),
          ),
          const SizedBox(height: AppTheme.spacingXS),
          Text(
            subtitle,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

// ======================== EMPTY STATE ========================

class AppEmptyState extends StatelessWidget {
  final String title;
  final String? message;
  final IconData icon;
  final VoidCallback? onRetry;

  const AppEmptyState({
    super.key,
    required this.title,
    this.message,
    required this.icon,
    this.onRetry,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.spacingXL),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon,
              size: 64,
              color: Theme.of(context).textTheme.bodySmall?.color,
            ),
            const SizedBox(height: AppTheme.spacingL),
            Text(
              title,
              style: Theme.of(context).textTheme.titleLarge,
              textAlign: TextAlign.center,
            ),
            if (message != null) ...[
              const SizedBox(height: AppTheme.spacingS),
              Text(
                message!,
                style: Theme.of(context).textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
            ],
            if (onRetry != null) ...[
              const SizedBox(height: AppTheme.spacingL),
              ElevatedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('Спробувати знову'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import '../services/notification_service.dart';
import '../l10n/app_localizations.dart';
import '../theme/app_theme.dart';

// ---------------------------------------------------------------------------
// Bell button with badge + popup panel
// ---------------------------------------------------------------------------

class NotificationBell extends StatefulWidget {
  const NotificationBell({super.key});

  @override
  State<NotificationBell> createState() => _NotificationBellState();
}

class _NotificationBellState extends State<NotificationBell>
    with SingleTickerProviderStateMixin {
  final OverlayPortalController _controller = OverlayPortalController();
  final LayerLink _link = LayerLink();
  late final AnimationController _shakeCtrl;
  late final Animation<double> _shakeAnim;
  int _prevUnread = 0;

  @override
  void initState() {
    super.initState();
    _shakeCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 450),
    );
    _shakeAnim = TweenSequence<double>([
      TweenSequenceItem(tween: Tween(begin: 0.0, end: -0.08), weight: 1),
      TweenSequenceItem(tween: Tween(begin: -0.08, end: 0.08), weight: 2),
      TweenSequenceItem(tween: Tween(begin: 0.08, end: -0.06), weight: 2),
      TweenSequenceItem(tween: Tween(begin: -0.06, end: 0.06), weight: 2),
      TweenSequenceItem(tween: Tween(begin: 0.06, end: 0.0), weight: 1),
    ]).animate(_shakeCtrl);
    NotificationService.instance.addListener(_onServiceUpdate);
  }

  void _onServiceUpdate() {
    final unread = NotificationService.instance.unreadCount;
    if (unread > _prevUnread && mounted) {
      _shakeCtrl.forward(from: 0.0);
    }
    _prevUnread = unread;
  }

  @override
  void dispose() {
    _shakeCtrl.dispose();
    NotificationService.instance.removeListener(_onServiceUpdate);
    super.dispose();
  }

  void _toggle() {
    if (_controller.isShowing) {
      _controller.hide();
    } else {
      NotificationService.instance.markAllRead();
      _controller.show();
    }
  }

  @override
  Widget build(BuildContext context) {
    return CompositedTransformTarget(
      link: _link,
      child: OverlayPortal(
        controller: _controller,
        overlayChildBuilder: (ctx) {
          return Stack(
            children: [
              Positioned.fill(
                child: GestureDetector(
                  behavior: HitTestBehavior.translucent,
                  onTap: _controller.hide,
                  child: const SizedBox.expand(),
                ),
              ),
              CompositedTransformFollower(
                link: _link,
                targetAnchor: Alignment.bottomRight,
                followerAnchor: Alignment.topRight,
                offset: const Offset(0, 8),
                child: Align(
                  alignment: Alignment.topRight,
                  child: _NotificationDropdown(onClose: _controller.hide),
                ),
              ),
            ],
          );
        },
        child: _BellIconButton(shakeAnim: _shakeAnim, onTap: _toggle),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Internal bell icon with animated badge
// ---------------------------------------------------------------------------

class _BellIconButton extends StatelessWidget {
  final Animation<double> shakeAnim;
  final VoidCallback onTap;

  const _BellIconButton({required this.shakeAnim, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: NotificationService.instance,
      builder: (ctx, _) {
        final count = NotificationService.instance.unreadCount;
        final theme = Theme.of(ctx);

        return AnimatedBuilder(
          animation: shakeAnim,
          builder: (_, child) =>
              Transform.rotate(angle: shakeAnim.value, child: child),
          child: Tooltip(
            message: count > 0 ? 'Notifications ($count)' : 'Notifications',
            child: InkWell(
              borderRadius: BorderRadius.circular(AppTheme.radiusMedium),
              onTap: onTap,
              child: Padding(
                padding: const EdgeInsets.all(8.0),
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Icon(
                      count > 0
                          ? Icons.notifications_rounded
                          : Icons.notifications_none_rounded,
                      size: 22,
                    ),
                    if (count > 0)
                      Positioned(
                        top: -4,
                        right: -4,
                        child: Container(
                          constraints: const BoxConstraints(
                            minWidth: 16,
                            minHeight: 16,
                          ),
                          padding: const EdgeInsets.symmetric(horizontal: 4),
                          decoration: BoxDecoration(
                            color: theme.colorScheme.error,
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Text(
                            count > 99 ? '99+' : '$count',
                            style: TextStyle(
                              color: theme.colorScheme.onError,
                              fontSize: 9,
                              fontWeight: FontWeight.w700,
                              height: 1.6,
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Dropdown notification panel
// ---------------------------------------------------------------------------

class _NotificationDropdown extends StatelessWidget {
  final VoidCallback onClose;

  const _NotificationDropdown({required this.onClose});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;

    return Material(
      elevation: 12,
      borderRadius: BorderRadius.circular(AppTheme.radiusLarge),
      color: Colors.transparent,
      child: SizedBox(
        width: 360,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(AppTheme.radiusLarge),
          child: Container(
            decoration: BoxDecoration(
              color: theme.colorScheme.surface,
              borderRadius: BorderRadius.circular(AppTheme.radiusLarge),
              border: Border.all(
                color: theme.colorScheme.outlineVariant.withValues(alpha: 0.4),
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.15),
                  blurRadius: 20,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Header
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 8, 8),
                  child: Row(
                    children: [
                      Icon(Icons.notifications_rounded,
                          size: 18, color: theme.colorScheme.primary),
                      const SizedBox(width: 8),
                      Text(
                        l10n.notificationsTitle,
                        style: theme.textTheme.titleSmall
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      const Spacer(),
                      ListenableBuilder(
                        listenable: NotificationService.instance,
                        builder: (ctx, _) {
                          if (NotificationService
                              .instance.notifications.isEmpty) {
                            return const SizedBox.shrink();
                          }
                          return TextButton(
                            style: TextButton.styleFrom(
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 8),
                              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              minimumSize: Size.zero,
                            ),
                            onPressed: NotificationService.instance.clearAll,
                            child: Text(
                              l10n.notificationsClear,
                              style: theme.textTheme.labelSmall
                                  ?.copyWith(color: theme.colorScheme.error),
                            ),
                          );
                        },
                      ),
                    ],
                  ),
                ),
                const Divider(height: 1),
                // Notification list
                ListenableBuilder(
                  listenable: NotificationService.instance,
                  builder: (ctx, _) {
                    final items = NotificationService.instance.notifications;
                    if (items.isEmpty) {
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 32),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.notifications_off_outlined,
                              size: 36,
                              color: theme.colorScheme.onSurfaceVariant
                                  .withValues(alpha: 0.4),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              l10n.notificationsEmpty,
                              style: theme.textTheme.bodySmall?.copyWith(
                                  color: theme.colorScheme.onSurfaceVariant),
                            ),
                          ],
                        ),
                      );
                    }

                    final capped =
                        items.length > 30 ? items.sublist(0, 30) : items;
                    return ConstrainedBox(
                      constraints: const BoxConstraints(maxHeight: 400),
                      child: ListView.separated(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        shrinkWrap: true,
                        itemCount: capped.length,
                        separatorBuilder: (_, __) => const Divider(height: 1),
                        itemBuilder: (ctx, i) =>
                            _NotificationTile(notification: capped[i]),
                      ),
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Single notification tile
// ---------------------------------------------------------------------------

class _NotificationTile extends StatelessWidget {
  final AppNotification notification;

  const _NotificationTile({required this.notification});

  Color _accent(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    switch (notification.type) {
      case AppNotificationType.gridOutage:
      case AppNotificationType.lowBattery:
        return cs.error;
      case AppNotificationType.gridRestored:
      case AppNotificationType.batteryRecovered:
        return AppTheme.batteryColor;
      case AppNotificationType.modeChanged:
        return cs.primary;
      case AppNotificationType.updateAvailable:
        return cs.tertiary;
      case AppNotificationType.gridInstability:
        return AppTheme.pvColor;
      case AppNotificationType.custom:
        return cs.onSurfaceVariant;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final accent = _accent(context);

    return Container(
      color: notification.isRead
          ? Colors.transparent
          : accent.withValues(alpha: 0.06),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: accent.withValues(alpha: 0.12),
              shape: BoxShape.circle,
            ),
            child: Center(
              child: Text(
                notification.type.icon,
                style: const TextStyle(fontSize: 15),
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        notification.title,
                        style: theme.textTheme.labelMedium?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: accent,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 4),
                    if (!notification.isRead)
                      Container(
                        width: 6,
                        height: 6,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: accent,
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  notification.body,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  _formatTime(notification.timestamp),
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant
                        .withValues(alpha: 0.55),
                    fontSize: 10,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _formatTime(DateTime dt) {
    final now = DateTime.now();
    final diff = now.difference(dt);
    if (diff.inMinutes < 1) return '< 1 min';
    if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
    if (diff.inHours < 24) return '${diff.inHours} hr ago';
    final hh = dt.hour.toString().padLeft(2, '0');
    final mm = dt.minute.toString().padLeft(2, '0');
    return '${dt.day}.${dt.month} $hh:$mm';
  }
}

// lib/services/notification_service.dart
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:local_notifier/local_notifier.dart';
import 'log_service.dart';

// ---------------------------------------------------------------------------
// Notification type
// ---------------------------------------------------------------------------

enum AppNotificationType {
  gridOutage,
  gridRestored,
  lowBattery,
  batteryRecovered,
  modeChanged,
  updateAvailable,
  gridInstability,
  custom,
}

extension AppNotificationTypeExt on AppNotificationType {
  String get icon {
    switch (this) {
      case AppNotificationType.gridOutage:
        return '⚡';
      case AppNotificationType.gridRestored:
        return '🔌';
      case AppNotificationType.lowBattery:
        return '🪫';
      case AppNotificationType.batteryRecovered:
        return '🔋';
      case AppNotificationType.modeChanged:
        return '🔄';
      case AppNotificationType.updateAvailable:
        return '🆕';
      case AppNotificationType.gridInstability:
        return '⚠️';
      case AppNotificationType.custom:
        return 'ℹ️';
    }
  }
}

// ---------------------------------------------------------------------------
// Notification model
// ---------------------------------------------------------------------------

class AppNotification {
  final String id;
  final AppNotificationType type;
  final String title;
  final String body;
  final DateTime timestamp;
  bool isRead;

  AppNotification({
    required this.id,
    required this.type,
    required this.title,
    required this.body,
    required this.timestamp,
    this.isRead = false,
  });
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

class NotificationService extends ChangeNotifier {
  static NotificationService? _instance;

  static NotificationService get instance {
    _instance ??= NotificationService._();
    return _instance!;
  }

  NotificationService._();

  static const int _maxNotifications = 100;

  final List<AppNotification> _notifications = [];
  bool _osToastsEnabled = true;
  bool _initialized = false;

  List<AppNotification> get notifications => List.unmodifiable(_notifications);
  int get unreadCount => _notifications.where((n) => !n.isRead).length;
  bool get hasUnread => unreadCount > 0;

  /// Call on app startup (before any show() calls).
  Future<void> initialize() async {
    if (_initialized) return;
    try {
      if (!kIsWeb &&
          (Platform.isWindows || Platform.isLinux || Platform.isMacOS)) {
        await localNotifier.setup(appName: 'Smart Inverter');
      }
      _initialized = true;
      LogService.log('🔔 NotificationService initialized');
    } catch (e) {
      LogService.log(
          '⚠️ NotificationService.initialize: OS toasts unavailable: $e');
      _osToastsEnabled = false;
      _initialized = true;
    }
  }

  /// Show a notification. Adds to the in-app list and optionally fires an OS toast.
  Future<void> show({
    required AppNotificationType type,
    required String title,
    required String body,
    bool showOsToast = true,
  }) async {
    final notification = AppNotification(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
      type: type,
      title: title,
      body: body,
      timestamp: DateTime.now(),
    );

    _notifications.insert(0, notification);
    if (_notifications.length > _maxNotifications) {
      _notifications.removeLast();
    }

    LogService.log('🔔 ${notification.type.icon} $title — $body');
    notifyListeners();

    // OS-level toast (desktop only)
    if (showOsToast &&
        _initialized &&
        _osToastsEnabled &&
        !kIsWeb &&
        (Platform.isWindows || Platform.isLinux || Platform.isMacOS)) {
      try {
        final toast = LocalNotification(
          identifier: notification.id,
          title: title,
          body: body,
        );
        await localNotifier.notify(toast);
      } catch (e) {
        LogService.log('⚠️ OS toast error: $e');
      }
    }
  }

  void markAllRead() {
    var changed = false;
    for (final n in _notifications) {
      if (!n.isRead) {
        n.isRead = true;
        changed = true;
      }
    }
    if (changed) notifyListeners();
  }

  void markRead(String id) {
    final idx = _notifications.indexWhere((n) => n.id == id);
    if (idx >= 0 && !_notifications[idx].isRead) {
      _notifications[idx].isRead = true;
      notifyListeners();
    }
  }

  void clearAll() {
    if (_notifications.isNotEmpty) {
      _notifications.clear();
      notifyListeners();
    }
  }
}

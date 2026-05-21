# Debug Logging System - Критичні HEMS Events

## 📋 Огляд

Приложение має **двохрівневу систему логування**:

1. **In-Memory Logs** — буффер на 1000 записів для швидкого доступу в UI
2. **File-Based Logs** — критичні события записуються на диск для анализу

## 🔴 Критичні Категорії Логування

| Категорія | Опис | Де логується |
|-----------|------|-------------|
| `BATTERY_SAFETY` | Hard floor SOC protection (≤22%) | `hems_algorithm.dart:490` |
| `BATTERY_RECOVERY` | Критична розрядка (SOC < 35%) та гістерезис (< 45%) | `hems_algorithm.dart:614-629` |
| `EVENING_PROTECTION` | Вечірня захист батареї (18:00-23:00) | `hems_algorithm.dart:673-695` |
| `MODE_CONFLICT` | Конфлікти режимів між ПК та телефоном | `hems_algorithm.dart:251` |
| `STALE_DATA` | Дані застарілі >30 хв, примусове USB | `app_provider.dart:1376-1380` |
| `EMERGENCY_CHARGE` | Срочна зарядка при SOC < 25% | `app_provider.dart:1397-1405` |

## 📂 Розташування Файлів

```
~/Documents/siseli_debug_logs/
├── hems_critical_events.log-2026-05-12
├── hems_critical_events.log-2026-05-11.01
├── hems_critical_events.log-2026-05-11.02
└── ... (максимум 10 файлів, ротація після 5MB)
```

**Platform:**
- **Windows**: `C:\Users\<user>\Documents\siseli_debug_logs\`
- **Android**: `/storage/emulated/0/Documents/siseli_debug_logs/` (чи `/sdcard/...`)
- **iOS**: `~/Documents/siseli_debug_logs/`

## 🚀 Як Використовувати

### 1️⃣ **Увімкнути Developer Mode**
- Натисніть на версію додатку 7 разів у Settings → Developer Mode включиться

### 2️⃣ **Переглянути Логи**
Settings → 🐛 Debug Logs → відкриється повноекранний экран з:
- 📁 Информацією про файли
- 🏷️ Категоріями які логуються
- 📝 Вмістом файлу критичних подій
- 🔄 Кнопками для оновлення/видалення

### 3️⃣ **Експортувати Логи**
Скопіюйте весь вміст з UI або вручну перейдіть до папки `Documents/siseli_debug_logs/`

## 📊 Формати Записів

Кожен лог-запис має такий формат:
```
[HH:MM:SS] [CATEGORY] MESSAGE
```

**Приклади:**
```
[18:45:33] [BATTERY_RECOVERY] CRITICAL RECOVERY: SOC=34.2% < 35% → Force USB+SNU until 45%+
[18:47:15] [MODE_CONFLICT] EXTERNAL MODE CHANGE: sent=SBU, device shows=USB → sync conflict detected!
[18:50:00] [EVENING_PROTECTION] EVENING CRITICAL: SOC=21.5% ≤ 22.0% at hour=18 → Emergency USB+SNU
[19:12:44] [STALE_DATA] STALE DATA EMERGENCY: no realtime for 31m, SOC=28% (day) → Force USB for protection
```

## 🔧 API для Розробників

```dart
// Initialize file logging at app startup
await LogService.initializeFileLogging();

// Log critical event
LogService.logCritical(
  'My critical message',
  category: 'MY_CATEGORY'
);

// Read all critical logs from file
final logs = await LogService.readCriticalLog();

// Get file path
final path = await LogService.getDebugLogPath();

// List all log files
final files = await LogService.listDebugLogFiles();

// Clear all logs
await LogService.clearDebugLogs();
```

## 📝 Приклади Аналізу

### Сценарій 1: Розрядка вечером (18:00)

```log
[18:45:33] [BATTERY_RECOVERY] HYSTERESIS: SOC=42.1% < 45% on USB → maintain SNU charging
[18:47:15] [EVENING_PROTECTION] EVENING LOW ENERGY: available=119.2Wh ≤ safety=150.0Wh at hour=18 → USB
[18:50:00] [EVENING_PROTECTION] EVENING CRITICAL: SOC=21.5% ≤ 22.0% at hour=18 → Emergency USB+SNU
[19:12:44] [STALE_DATA] STALE DATA EMERGENCY: no realtime for 31m, SOC=28% (day) → Force USB for protection
```

**Аналіз:** Система коректно:
1. ✅ Залишилась у режимі USB при низькому зарядженні
2. ✅ Активувала зарядку від мережі (SNU)
3. ✅ При втраті зв'язку примусово перейшла на USB для захисту

### Сценарій 2: Конфлікти між ПК та телефоном

```log
[14:22:10] [MODE_CONFLICT] EXTERNAL MODE CHANGE: sent=SBU, device shows=USB → sync conflict detected!
[14:22:11] [MODE_CONFLICT] EXTERNAL MODE CHANGE: sent=USB, device shows=SBU → sync conflict detected!
```

**Аналіз:** Обидва приложення намагаються перемикати режим одночасно → тиск на "HOLD" після першої зміни

## 🛠️ Дебагування

### Проблема: "Світло пропало вечором"
1. Відкрийте Debug Logs
2. Шукайте `EVENING_PROTECTION` -> `CRITICAL` записи
3. Перевірте `SOC%` та `available energy`
4. Перевірте, чи був `STALE_DATA` - сигнал тому що дані застарілі

### Проблема: "Конфлікти режимів"
1. Шукайте `MODE_CONFLICT` записи
2. Проаналізуйте часи: `sent=X, device shows=Y`
3. Дивіться на час синхронізації (500ms затримка)

### Проблема: "Система повільна реагує"
1. Перевірте `STALE_DATA` -> дані застарілі >30 хв
2. Перевірте розташування API серверу
3. Перевірте мережеве з'єднання

## 🔐 Конфіденційність

**Користувачеві дані, які НЕ логуються:**
- Токени, паролі, ключі (автоматично маскуються)
- Email адреси
- URL параметри з чутливими даними

**Що логується:**
- SOC%, режими управління, часи переходів
- Прогнози дефіциту енергії
- Номери версій, типи помилок

## 📞 Звіти про Проблеми

При звіті про проблему додайте:
1. **Файл логу** з `Documents/siseli_debug_logs/`
2. **Часовий діапазон** проблеми
3. **Описання** що сапсувало (світло, конфлікт, і т.д.)

Це допоможе швидко ідентифікувати проблему!

---

**Остання оновлення:** 2026-05-12  
**Версія:** 2.0.4+


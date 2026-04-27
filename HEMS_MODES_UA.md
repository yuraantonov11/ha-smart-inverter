# HEMS режими — Детальна довідка (UA)

> **Smart Inverter App** — Home Energy Management System  
> Файл алгоритму: `lib/services/hems_algorithm.dart`  
> Параметри тюнінгу: `HemsTunables`

---

## Зміст

1. [Огляд архітектури](#1-огляд-архітектури)
2. [Спільні правила безпеки (завжди активні)](#2-спільні-правила-безпеки-завжди-активні)
3. [Режим A — Adaptive (Smart)](#3-режим-a--adaptive-smart)
4. [Режим B — Night Arbitrage](#4-режим-b--night-arbitrage)
5. [Режим C — Storm / Reserve](#5-режим-c--storm--reserve)
6. [Наскрізно: Battery Keepalive](#6-наскрізно-battery-keepalive)
7. [Наскрізно: Acoustic Comfort (нічна тиша)](#7-наскрізно-acoustic-comfort-нічна-тиша)
8. [Anti-flapping і ручний override](#8-anti-flapping-і-ручний-override)
9. [Швидка таблиця параметрів `HemsTunables`](#9-швидка-таблиця-параметрів-hemstunables)
10. [Схеми прийняття рішень](#10-схеми-прийняття-рішень)
11. [Практичні сценарії з цифрами](#11-практичні-сценарії-з-цифрами)
12. [FAQ / Діагностика](#12-faq--діагностика)

---

## 1. Огляд архітектури

Кожен тик HEMS (періодичний виклик із `AppStateProvider`) проходить пріоритетний ланцюжок:

```text
[Safety floor: SOC ≤ 22%?]
       ↓ НІ
[Manual override hold активний?]
       ↓ НІ
[Battery keepalive спрацював?]
       ↓ НІ
[Нічне вікно (23:00–07:00)?]
       ↓ НІ
[Realtime PV surplus ≥ 250W і SOC ≥ 30%?]
       ↓ НІ
[Forecast simulation → дефіцит?]
       ↓
[Рішення: SBU або USB]
```

### Пріоритети виходу (`outputSourcePriority`)

| Значення | Назва | Значення для системи |
|---|---|---|
| `'0'` | USB | Пріоритет мережі (Grid First) |
| `'2'` | SBU | Пріоритет сонця/АКБ (Solar/Battery First) |

### Пріоритети зарядки (`chargerSourcePriority`)

| Значення | Назва | Значення для системи |
|---|---|---|
| `'1'` | SNU | Заряд від сонця + мережі |
| `'2'` | OSO | Заряд тільки від сонця |

---

## 2. Спільні правила безпеки (завжди активні)

### 2.1 Жорсткий поріг SOC

**Тригер:** `batterySoc <= reserveSoc + 2` (за замовчуванням `<= 22%`)  
**Дія:**
- `output -> USB`
- `charger -> SNU`
- лог: `reason=safety_low_soc`

**Приклад:**
```text
SOC=21%, PV=1500W, 13:00
→ безпека має пріоритет над усім
→ USB + SNU
```

Причина: на низькому SOC не можна агресивно розряджати батарею, навіть якщо зараз є сонце.

---

## 3. Режим A — Adaptive (Smart)

Гібридний режим: realtime + прогноз.

- realtime шар: реагує на поточні `pvPower`, `loadPower`, `surplus`
- прогнозний шар: оцінює ризик дефіциту до 23:00

### 3.1 Ніч (23:00–07:00)

- `output -> USB` (стабільне правило)
- `charger` обирається прогнозом на завтра:
  - якщо дефіцит `> 0` -> `SNU`
  - якщо дефіцит `== 0` -> `OSO`

**Приклад:**
```text
02:00, SOC=65%, завтра хмарно -> дефіцит>0
→ USB + SNU (нічний дешевий тариф)
```

### 3.2 День (07:00–17:00)

Спочатку завжди `charger -> OSO`.

#### Крок 1: Realtime surplus (ключове покращення)

Обчислення:
```text
surplus = pvPower - loadPower
```

Якщо `pvPower > 80W`, `surplus >= 250W`, `SOC >= 30%` -> негайно `SBU`.

**Приклад:**
```text
13:00, PV=2500W, Load=900W, SOC=72%
surplus=1600W >= 250W
→ SBU (reason=pv_surplus_1600W_soc_72)
```

#### Крок 2: Forecast fallback

Якщо realtime не дав чіткого рішення, запускається симуляція до 23:00:
- `deficit == 0` -> `SBU`
- `deficit > 0` і `surplus <= 50W` і `SOC < 50%` -> `USB`
- інакше -> **утримати поточний режим** (без зайвого перемикання)

### 3.3 Вечір (17:00–23:00)

Вечір більш консервативний для захисту резерву:
- `charger -> OSO`
- якщо є ризик просідання (дефіцит/SOC/низький запас) -> `USB`
- якщо запас достатній -> `SBU`

**Приклад (захист резерву):**
```text
20:00, SOC=28%, сонця майже немає, дефіцит>0
→ USB (reason=evening_reserve_def_...)
```

---

## 4. Режим B — Night Arbitrage

Простий тарифний режим.

### Ніч (23:00–07:00)
- `output -> USB`
- `charger -> SNU`

### День (07:00–23:00)
- `charger -> OSO`
- `output -> SBU` лише коли є реальний surplus (`PV>80`, `surplus>=250`, `SOC>=30`)
- інакше режим не форсується (hold)

**Коли обирати:** якщо хочеш передбачувану поведінку без складної прогнозної логіки.

---

## 5. Режим C — Storm / Reserve

Режим максимальної готовності до блекаутів:
- `output -> USB` (`force=true`)
- `charger -> SNU` (`force=true`)

Мета: швидко набрати 100% SOC із PV + мережі.

**Коли вмикати:** перед штормом/відключенням.  
**Коли не треба:** у звичайні сонячні дні (може збільшити вартість енергії).

---

## 6. Наскрізно: Battery Keepalive

Проблема: деякі BMS "засинають", коли струм довго нульовий.

**Тригер:**
- активність батареї < 50W протягом 2 годин
- SOC > 22%
- режим не SBU

**Дія:**
1. Коротко `SBU` на 90 секунд
2. Повернення в `USB`
3. Скидання таймера неактивності

---

## 7. Наскрізно: Acoustic Comfort (нічна тиша)

- 22:00–07:00 -> `buzzerAlarmSetting=0`
- 07:00–22:00 -> `buzzerAlarmSetting=1`

Є dedup через `_lastAppliedBuzzer`, щоб не слати однакові команди щотик.

---

## 8. Anti-flapping і ручний override

### 8.1 Dwell (`minModeHold=20m`)

Після перемикання `USB<->SBU` нове протилежне перемикання блокується 20 хв, крім `force=true` (безпека/шторм).

### 8.2 Dedup (`commandDedupWindow=30s`)

Повторні однакові команди в межах 30с не надсилаються.

### 8.3 Manual override (`manualOverrideHold=30m`)

Якщо користувач змінив режим вручну (або натиснув кнопки в UI, які викликають `armManualOverride()`), алгоритм 30 хв не втручається в `output`.

---

## 9. Швидка таблиця параметрів `HemsTunables`

| Параметр | Default | Що робить |
|---|---:|---|
| `reserveSoc` | 20% | Нижній резерв батареї |
| `minOperatingSoc` | 30% | Нижче цього SBU менш бажаний |
| `midSoc` | 50% | Межа для рішень у дефіцитних сценаріях |
| `pvSurplusEnterW` | 250W | Поріг входу в SBU |
| `pvSurplusExitW` | 50W | Поріг виходу/умови USB при дефіциті |
| `minModeHold` | 20m | Захист від частих перемикань |
| `manualOverrideHold` | 30m | Скільки поважати ручний режим |
| `commandDedupWindow` | 30s | Придушення дубль-команд |

---

## 10. Схеми прийняття рішень

### Adaptive (день)

```text
SOC <= 22% ?
  YES -> USB + SNU (force)
  NO  -> manual hold active ?
            YES -> charger only / return
            NO  -> set charger OSO
                    -> realtime surplus strong ?
                        YES -> SBU
                        NO  -> forecast deficit to 23:00
                               deficit==0 -> SBU
                               deficit>0 && low surplus && low-mid SOC -> USB
                               else -> HOLD
```

### Adaptive (вечір)

```text
set charger OSO
simulate deficit to 23:00
if reserve risk -> USB
else if battery enough -> SBU
else HOLD
```

---

## 11. Практичні сценарії з цифрами

### Сценарій 1: сонячний полудень

```text
12:30, PV=2800W, Load=1000W, SOC=68%
surplus=1800W -> SBU
```

### Сценарій 2: хмарність + низький SOC

```text
14:00, PV=250W, Load=900W, SOC=37%, прогнозний дефіцит>0
-> USB (зберегти SOC до вечора)
```

### Сценарій 3: ручне перемикання користувачем

```text
10:30 користувач ставить SBU
-> armManualOverride(30m)
-> до 11:00 алгоритм не змінює output
```

### Сценарій 4: перед штормом

```text
18:00 Storm mode
-> USB + SNU force
-> максимальна швидкість зарядки до 100%
```

---

## 12. FAQ / Діагностика

### Чому при сонці буває USB?

Перевір `reason=` у логах.

| reason | Пояснення | Що робити |
|---|---|---|
| `safety_low_soc` | SOC дуже низький | Дочекатись відновлення SOC > 22% |
| `day_forecast_deficit_*_low_soc` | Алгоритм захищає вечірній резерв | Скоригувати `productionCoefficient`/пороги |
| `dwell active` | Спрацював anti-flap | Почекати завершення dwell |
| `manual hold active` | Активний ручний override | Дочекатися завершення hold |

### Чому після ручного SBU повертає назад?

Зазвичай через відсутній `manual hold` (коли зміна не зафіксована додатком) або завершення hold-вікна. У UI-кнопках `ControlPanel` це вже враховано через `armManualOverride()`.

---

**Оновлено:** 2026-04-26  
**Сумісно з:** HEMS v1.3.2+


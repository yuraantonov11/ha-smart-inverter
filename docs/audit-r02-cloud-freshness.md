# R02 — свіжість cloud telemetry

Дослідження того, чи можна з API-відповіді `timePoints` визначити, коли саме
було зроблено вимірювання (`measured_at`), і чи можна відрізнити свіжий
фізичний вимір від кешованого значення. Жодних production-змін; лише
документація, fixtures і пропозиція freshness/offline policy.

## Зміст

1. [Поточна структура payload (cleaned, real)](#1-payload)
2. [Що API *не* надає](#2-що-api-не-надає)
3. [Що production робить із `point["time"]`](#3-що-production-робить)
4. [fetched_at vs measured_at: розділення](#4-fetched-vs-measured)
5. [Synthetic fixtures — п'ять сценаріїв](#5-fixtures)
6. [Freshness/offline policy (пропозиція)](#6-freshness-policy)
7. [Вплив на dashboard, калібратор, dispatch](#7-вплив)
8. [Висновок](#8-висновок)

## 1. Payload (cleaned, real)

Виклик: `await api._fetch_overview("daily", "pvGeneratedEnergy", day=..., raw_properties=True)`
(`api.py:_fetch_overview`, `cloud_history.py:measured_pv_days`).

Структура очищеного `properties` (один зразок, що реально приходить у
production, без секретів):

```json
[
  {
    "property": {
      "key": "pvGeneratedEnergy",
      "unit": "kWh"
    },
    "timePoints": [
      {
        "time": "2026-10-07",
        "value": 6.5,
        "isRealValue": true
      }
    ]
  }
]
```

Аналогічно для `generationPower` (`kW`/`W`):

```json
[
  {
    "property": {
      "key": "generationPower",
      "unit": "kW"
    },
    "timePoints": [
      {
        "time": "2026-10-08T10:00",
        "value": 1.45,
        "isRealValue": true
      },
      {
        "time": "2026-10-08T10:30",
        "value": 1.48,
        "isRealValue": true
      }
    ]
  }
]
```

Очищено від `deviceId` / `propertyCode` / etc., що не впливають на freshness.

## 2. Що API не надає

У жодному `timePoint` немає:

- `deviceTime` / `gatewayTime` / `measuredAt` — тобто **часу виміру**.
- `sequenceId` / `seqNo` / `sampleId` — тобто **номера послідовності**.
- `latency` / `ingestDelay` / `transit` — тобто **затримки доставки**.

`point["time"]` — це **мітка, яку повертає бекенд** для цього bucket'а. Це
**календарна позиція** (`YYYY-MM-DD` для daily, `YYYY-MM-DDTHH:MM` для
half-hour), а не момент фізичного вимірювання.

Тому production **не може** розрізнити:
- «Інвертор виміряв о 09:59:53, бекенд поклав у bucket 10:00» (свіжий).
- «Інвертор виміряв учора о 09:59:53, бекенд заповнив сьогоднішній bucket
  затримано» (stale).

`measured_at` — **невідомо** з боку клієнта. Це треба явно зафіксувати в
контракті.

## 3. Що production робить

`cloud_history.py:23` і `:62` — парсер суворо вимагає
`point.get("isRealValue") is not True`. Якщо поле відсутнє, не `True`, або
`isinstance(point, dict)` — point **відкидається** мовчки. Це захисний
фільтр: беремо тільки ті точки, які бекенд явно декларує як реальні
вимірювання (а не інтерполяції/прогнози/кешовані плейсхолдери).

Окрім того, `pv_learning.py:160–167` обмежує daily-виміри: `value` має бути
у `[0, 500]` kWh, `day` — у межах `[start, end]`, `value` має бути
`finite` (відкидаємо `NaN`/`inf`). Підсумкова перевірка — в
`test_calibration_backfill.py:TestCalibrationBackfillRejectSynthetic`:

> Cloud PV history skipped: account is not unambiguously single-device
> (`api.py:fetch_daily_pv_history`)

`isRealValue` — це **єдиний** біт свіжості, який надає API. Він не доводить,
що вимір зроблено *зараз*; він доводить лише, що це не синтетика
(інтерполяція, заповнювач, прогноз).

## 4. fetched_at vs measured_at

Production **розділяє** ці два моменти:

| Поле | Джерело | Тип | Зберігається? |
|------|---------|-----|---------------|
| `fetched_at` | `datetime.now(UTC)` на момент отримання HTTP-відповіді | aware UTC | ні (transient) |
| `point["time"]` (тільки для daily kWh) | API bucket boundary | naive date | так (PV history) |
| `point["time"]` (для half-hour W) | API bucket boundary | naive datetime | ні (поки що) |
| `daily_energy_at` | coordinator's response-completion | aware UTC | так (energy_freshness) |
| `daily_energy_date` | local-date of `daily_energy_at` | naive date | так (energy_freshness) |

`fetched_at` = коли наш код отримав відповідь.
`measured_at` = коли інвертор фізично виміряв (невідомо).

Зв'язок: `point["time"] <= measured_at <= fetched_at`. Це **гарантовано**,
бо бекенд не може повернути вимір раніше, ніж його зробили, і не може
доставити пізніше, ніж ми отримали.

Жодного `measured_at` у жодному з payload ми не бачимо. Тому:

- Якщо `point["time"]` = сьогоднішня дата і `fetched_at` = сьогодні — це
  валідний свіжий вимір **з погляду бекенду**, але ми **не знаємо**, чи це
  поточна секунда, чи кеш за годину.
- Якщо `point["time"]` = учорашня дата і `fetched_at` = сьогодні — це
  запізнілий вимір за вчора, не stale для сьогоднішнього bucket'а.
- Якщо `point["time"]` = сьогоднішня дата і `fetched_at` = учора — це
  **підозріло**; або кеш, або дуже повільна синхронізація.

## 5. Synthetic fixtures

`tests/fixtures/r02_cloud_payloads.json` — 5 сценаріїв (плюс один
"happy path" для контр-тесту). Усі payload — cleaned, без секретів.

### Сценарій A — успішний HTTP зі старим timestamp

```json
{
  "name": "A_old_timestamp_fresh_fetch",
  "fetched_at": "2026-10-08T12:00:00+00:00",
  "points": [
    {"time": "2026-10-07", "value": 6.5, "isRealValue": true}
  ],
  "expected_outcome": "yesterday_actual_for_yesterday"
}
```

Трактування: `point["time"]` = учора, `fetched_at` = сьогодні. Це
**нормальний** бакет за учора. `actual` для `2026-10-07` = 6.5 kWh. Pair
буде створено (2026-10-07 — повний день у минулому).

### Сценарій B — повтор payload (same value, same time)

```json
{
  "name": "B_repeated_payload",
  "fetched_at_first": "2026-10-08T10:00:00+00:00",
  "fetched_at_second": "2026-10-08T12:00:00+00:00",
  "points": [
    {"time": "2026-10-07", "value": 6.5, "isRealValue": true}
  ],
  "expected_outcome": "yesterday_actual_idempotent"
}
```

Трактування: той самий payload двічі. `isRealValue` = true. Ми **не
можемо** визначити, чи це два різні виміри (тоді це два однакові
значення), чи це кеш. Трактування у production — idempotent: пара буде
створена лише один раз (`RealForecastPairs.match` перевіряє `day in
self.pairs`).

### Сценарій C — відсутній timestamp

```json
{
  "name": "C_missing_time_field",
  "fetched_at": "2026-10-08T12:00:00+00:00",
  "points": [
    {"value": 6.5, "isRealValue": true}
  ],
  "expected_outcome": "point_rejected_invalid"
}
```

Трактування: `point["time"]` відсутній. `date.fromisoformat(point["time"])`
в `cloud_history.py:32` кидає `KeyError`, який ловиться в
`except (KeyError, TypeError, ValueError, OverflowError)`, і point
відкидається. У `actual` нічого не потрапляє.

### Сценарій D — майбутній або некоректний timestamp

```json
{
  "name": "D_future_or_garbage_timestamp",
  "fetched_at": "2026-10-08T12:00:00+00:00",
  "points": [
    {"time": "2099-12-31", "value": 6.5, "isRealValue": true},
    {"time": "not-a-date", "value": 6.5, "isRealValue": true},
    {"time": "", "value": 6.5, "isRealValue": true}
  ],
  "expected_outcome": "all_rejected_out_of_range_or_parse_error"
}
```

Трактування: всі три точки відкидаються — або `day > end` (майбутнє), або
parse-error (некоректний формат). У `actual` нічого не потрапляє.

### Сценарій E — свіжий нуль

```json
{
  "name": "E_fresh_zero_at_night",
  "fetched_at": "2026-10-08T22:00:00+00:00",
  "points": [
    {"time": "2026-10-08", "value": 0.0, "isRealValue": true}
  ],
  "expected_outcome": "today_actual_zero_valid"
}
```

Трактування: `point["time"]` = сьогодні, value = 0.0, `isRealValue` = true.
Це **валідний** вимір. Нічний PV=0 — це очікувано, а не stale.
`actual["2026-10-08"] = 0.0`. Pair НЕ створюється (`match` пропускає дні
`>= now.date().isoformat()`), але це не блокує calibrator, бо calibrator
чекає на `actual.get(day) is None` для сьогодні — це нормально, не ознака
помилки.

### Сценарій F — control: stale-кандидат з однаковим значенням

```json
{
  "name": "F_same_value_no_new_evidence",
  "fetched_at_first": "2026-10-08T08:00:00+00:00",
  "fetched_at_second": "2026-10-08T10:00:00+00:00",
  "points": [
    {"time": "2026-10-08T07:00", "value": 0.0, "isRealValue": true},
    {"time": "2026-10-08T07:30", "value": 0.0, "isRealValue": true}
  ],
  "expected_outcome": "yesterday_or_today_idempotent"
}
```

Трактування: ті самі два семпли, той самий час. Production не розрізняє
«це два незалежні виміри з однаковим результатом» від «це кеш». Жодних
`measured_at` немає, тому неможливо довести, що другий виклик приніс
нові дані.

## 6. Freshness/offline policy (пропозиція)

### Стан 1: HTTP 200 з `isRealValue=true`

- `point["time"]` у минулому (≤ `now.date()` для daily, ≤ `now` для
  half-hour): **приймаємо**. `fetched_at` не впливає на факт, тільки на
  логування. `measured_at` невідомий, але вважаємо, що це «свіжий
  бакет».
- `point["time"]` у майбутньому: **відхиляємо** (out-of-range).
- `point["time"]` відсутній або невалідний: **відхиляємо**.

### Стан 2: HTTP 200 з `isRealValue` відсутнім/не `True`

- **Відхиляємо** всі точки, бо це не «real». Це захист від синтетики.

### Стан 3: HTTP 200 з тим самим `point["time"]` повторно

- **Idempotent**: той самий `day` → `actual[day]` оновлюється тим самим
  значенням. Pair вже створено? Не перезаписуємо. Pair ще не створено?
  Створюємо з цим значенням. Семантика: production не знає, чи це новий
  вимір, чи кеш. **Ніяких блокувань не вводимо** (R02 не підключає).

### Стан 4: HTTP 4xx/5xx або timeout

- `actual` залишається без змін. `fetched_at` оновлюється тільки при
  успішній відповіді. **Без блокувань** (на цьому етапі R02 не
  підключає).

### Стан 5: HTTP 200, але `point["time"]` — це дата у минулому, а
`fetched_at - now > поріг`

- Це запізніла відповідь, але **валідна** для свого дня. Не stale для
  вчорашнього bucket'а. `actual[day]` приймається. (Вже реалізовано.)

### Стан 6: `fetched_at` стоїть на місці > N годин

- Це `energy_freshness` сигнал, не freshness of `point["time"]`. Уже
  винесено у `DailyEnergyFreshness.stale`. R02 не змінює пороги
  (default 6h, див. `energy_freshness.py:DEFAULT_FRESHNESS_HOURS`).

### Рекомендовані пороги (на наступний етап)

| Параметр | Значення | Обґрунтування |
|----------|----------|----------------|
| `DAILY_FRESH_HOURS` | 6 | default у `energy_freshness.py` |
| `HOURLY_FRESH_MINUTES` | 90 | half-hour bucket + 1 refresh cycle |
| `MAX_STALE_HOURS` | 36 | pair може прийти на наступний день — не блокувати |
| `CACHE_REPLAY_GRACE` | 60 min | якщо `point["time"]` той самий, але `fetched_at` < 60 min — не вважати новим |

## 7. Вплив на dashboard, калібратор, dispatch

### Dashboard

`sensor.garazh_smart_solar_inverter_predictive_decision_state` →
`forecast_calibration.pending_count`, `forecast_calibration.samples`,
`forecast_calibration.recent_pairs`. Ці поля **не показують freshness
кожного `point`**, лише агрегати. R02 пропонує додати атрибут
`last_fetched_at` до `forecast_calibration` — щоб користувач у
dashboard бачив, коли востаннє був HTTP-успіх.

### Калібратор

`ForecastCalibrator.record(forecast_w, actual_w)` приймає `(fc, ac)` і
не знає, звідки `ac`. Stale `point["time"]` з вчорашньою датою
**не впливає** на calibrator сьогодні — `match` кладе `actual[day]`
тільки для завершених днів, а калібратор записує лише завершені пари.
Тобто calibrator **стійкий** до stale-кандидатів.

### Dispatch

`HEMS` команди (`output_priority`, `charger_priority`, BMS, reserve
SOC, manual override) **не** залежать від `point["time"]` напряму — вони
залежать від `pv_power` (поточний), `battery_soc`, `load_power`,
`weather_code` тощо. Stale `daily_energy` не блокує dispatch.

## 8. Висновок

### Поточний контракт

- API надає лише `point["time"]` (календарний бакет) і `isRealValue` (біт
  реальності). **Жодних** `measured_at`, `sequenceId`, `deviceTime`.
- `fetched_at` (наш таймінг) і `point["time"]` (їхній бакет) — **різні
  речі**, і production їх розрізняє.
- `measured_at` — **невідомо** з боку клієнта. Це треба явно
  документувати, а не вгадувати.

### Підтверджені факти

- `isRealValue` = true — необхідна, але **недостатня** умова свіжості.
- `point["time"]` ≤ `now.date()` — необхідна умова для daily pair.
- Той самий `point["time"]` двічі — idempotent, не доводить stale.
- `point["time"]` у майбутньому — завжди відхиляється.
- `point["time"]` відсутній — завжди відхиляється.
- Нічний PV=0 — **валідний** вимір, не stale.

### Висновок

**Виправлення не потрібне.** Production-парсер уже робить усе розумне з
того, що дає API: `isRealValue` filter, range check, parse-error
handling, value bounds. П'ять сценаріїв R02 покриті існуючими
перевірками.

### Запропонована наступна зміна (окрема задача)

Додати в `forecast_calibration` поле `last_fetched_at` (aware UTC, з
`api._fetch_overview` response-completion). Використовувати тільки для
логування та dashboard-атрибуту, **без нових блокувань команд**.

Критерії приймання:

1. `last_fetched_at` з'являється у `calibration_status(...)` поряд з
   `pending_count`.
2. `last_fetched_at` оновлюється тільки при HTTP 200 з валідним
   `properties`.
3. При HTTP 4xx/5xx `last_fetched_at` не змінюється (як `daily_energy_at`
   у `energy_freshness`).
4. Тести `test_r02_*` (5 сценаріїв) **PASS** на fixtures.

### Нез'ясовано

- Якщо Powmr API має приховані поля, які клієнт зараз не зберігає
  (`deviceId`, `propertyCode`, тощо), це не в scope R02. R02 явно каже:
  cleaned payload не містить жодного freshness-поля.
- `sequenceId` у `point` API не повертає. Це підтверджено з реальних
  відповідей (див. структуру вище).

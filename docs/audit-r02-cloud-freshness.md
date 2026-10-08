# R02 — свіжість cloud telemetry

Дослідження двох **окремих контрактів** cloud-API:

1. **Historical (overview) buckets** — `pvGeneratedEnergy` (daily kWh)
   та `generationPower` (half-hour W). Використовуються для
   калібрування та dashboard. **Окремий контракт** від realtime.
2. **Realtime telemetry** — `deviceAttributeState` з
   `/apis/deviceState/simple/energy/flow/v1`. Використовується для
   HEMS dispatch. Це те, що дійсно впливає на freshness.

Цей документ раніше змішував ці два контракти. У round 2 ми
розділяємо їх: §1–§6 — historical, §7 — realtime, §8 — freshness
policy, §9 — висновок.

Жодних production-змін; лише документація, fixtures і пропозиція
freshness/offline policy.

## Зміст

1. [Historical: payload (cleaned, real)](#1-historical-payload)
2. [Historical: що API *не* надає](#2-historical-no-measured-at)
3. [Historical: що production робить із `point["time"]`](#3-historical-production)
4. [fetched_at vs measured_at: розділення](#4-fetched-vs-measured)
5. [Historical: synthetic fixtures — шість сценаріїв](#5-historical-fixtures)
6. [Historical: висновок і обмеження](#6-historical-conclusion)
7. [Realtime telemetry: payload і freshness](#7-realtime)
8. [Freshness/offline policy (пропозиція)](#8-freshness-policy)
9. [Вплив на dashboard, калібратор, dispatch](#9-вплив)
10. [Висновок](#10-висновок)

## 1. Historical: payload (cleaned, real)

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

## 2. Historical: що API *не* надає

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

## 3. Historical: що production робить

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

## 5. Historical: synthetic fixtures — шість сценаріїв

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

## 6. Historical: висновок і обмеження

**Підтверджено:**

1. Historical `timePoints` не надає `measured_at`, `sequenceId`,
   `deviceTime`, чи будь-який інший доказ нового фізичного виміру.
2. `point["time"]` — це **календарна позиція bucket'а** (daily
   `YYYY-MM-DD` або half-hour `YYYY-MM-DDTHH:MM`), не момент виміру.
3. Production `measured_pv_days` / `measured_pv_hours` приймають тільки
   `point["time"]` і `point["value"]` / `point["isRealValue"]`. Вони
   **не** приймають `fetched_at` — це transient поле, яке не
   передається в парсер.
4. **`fetched_at` — це клієнтський концепт**, не частина API-контракту.
   Він не зберігається в парсері й не впливає на результат.

**Обмеження:** Цей висновок обмежений **фактично дослідженими
відповідями** (production `measured_pv_days` / `measured_pv_hours` з
синтетичними payloads у `test_r02_cloud_payloads.py`). Якщо API
колись додасть поле `measured_at` у `timePoints` — це розширить
контракт і вимагатиме нових тестів.

**Повторюваний live-fetch:** `test_scenario_B_repeated_payload_idempotent`
викликає `measured_pv_days` **тричі** з тим самим payload і
стверджує, що результат ідемпотентний. Це доводить, що historical
парсер **не має** внутрішньої state, яка змінюється від повторних
викликів.

**Відхилення "майбутнього" timestamp:** відбувається через
**date-range check** у `measured_pv_days` (`start_day <= day <=
end_day`), а не через freshness policy. Це не пов'язано з
свіжістю даних.

## 7. Realtime telemetry: payload і freshness

**Endpoint:** `const.ENDPOINT_REALTIME = "/apis/deviceState/simple/energy/flow/v1"`
(`api.py:42-46`). Backend: `solar.siseli.com` (згадано в `api.py`).

**Payload structure (cleaned):**

```json
{
  "data": {
    "deviceAttributeState": {
      "pvInputPower": {"value": 1.5, "valueDisplay": "1.5"},
      "acOutputActivePower": {"value": 0.4, "valueDisplay": "0.4"},
      "batterySoc": {"value": 80, "valueDisplay": "80"}
    }
  },
  "code": 0,
  "received_at": "2026-10-08T12:00:00+00:00"  // CLIENT-side timestamp
}
```

**Production path:** `fetch_realtime_data` → `_try_realtime_endpoint`
(двічі — primary, fallback) → `_parse_realtime_fields` →
coordinator state. Поля, які читає production (зі списку в
`api._parse_realtime_fields`, рядки 781–980):

`pvInputPower`, `generationPower`, `solarPower`, `pvPower`,
`acOutputActivePower`, `loadPower`, `outputPower`, `acOutputPower`,
`batteryVoltage`, `batteryChargingCurrent`, `batteryDischargeCurrent`,
`batteryCurrent`, `batteryPower`, `gridPower`, `acInputPower`,
`gridPowerDirection`, `workingStates`, `outputSourcePriority`,
`chargerSourcePriority`, `batterySoc`, `batteryCapacity`, `pvVoltage`,
`solarVoltage`, `pvInputVoltage`, `gridVoltage`, `acInputVoltage`,
`loadPercent`, `loadPercentage`, `workingMode`, `deviceMode`,
`ntcMaximumTemperature`, `radiatorTemperature`, `invTemperature`,
`temperature`, `feedInPower`, `nominalAcVoltage`, `nominalAcCurrent`,
`ratedActivePower`, `acOutputRatingApparentPower`, `outputApparentPower`,
`outputFrequency`.

**Що API не надає (realtime):**

- **Жодного** `measuredAt` / `deviceTime` / `gatewayTime` / `timestamp`
  у `deviceAttributeState`. Перевірено в
  `test_r02_realtime_payload_structure_documented`.
- **Жодного** `sequenceId` / `seqNo` / `sampleId`. Перевірено.
- `received_at` — це **наш** (`await _try_realtime_endpoint(...)` —
  `datetime.now(UTC)`), не бекенд.

**Тести для realtime:**

- `test_r02_realtime_consecutive_same_payload`: двічі парсимо той
  самий payload → однаковий результат. Це доводить, що production
  parser не робить side-effect'ів.
- `test_r02_realtime_missing_timestamp`: payload без `received_at`
  парситься коректно.
- `test_r02_realtime_old_timestamp_still_valid`: `received_at`
  старший за місяць — payload усе ще парситься коректно (production
  realtime **не** відкидає на основі `received_at`).
- `test_r02_realtime_fresh_zero`: pvPower=0 (ніч) парситься як
  `pvPower=0.0`, не як "unknown" чи "stale".
- `test_r02_realtime_invalid_payload_returns_empty`: payload без
  `deviceAttributeState` → дефолтні нулі, не exception.

**Підтверджене обмеження:** `fetched_at` (наш `received_at`) **не
зберігається** в parsed state, **не** передається в coordinator, і
**не** використовується для freshness check у production. Якщо в
майбутньому знадобиться freshness policy, його треба будувати з
**нашого** `received_at` (а не `measured_at` — його немає в API).

**Обмеження тестів:** усі тести в `test_r02_realtime_telemetry.py`
використовують **синтетичний** payload, побудований у тесті. Вони
НЕ доводять відсутність `measuredAt` / `sequenceId` /
`deviceTime` у **реальних** API-відповідях. Для реального API
висновку потрібна **очищена capture з provenance** (тобто
записана відповідь з документованим способом отримання). Ми не
маємо такої capture. Усі твердження про структуру realtime
payload обмежені синтетичними даними, з якими ми тестуємо.

## 8. Freshness/offline policy (пропозиція)

**Не підключено** в production. Пропозиція для майбутнього:

```
fresh_window_minutes = 15   # state = OK
stale_threshold_minutes = 45  # state = STALE
offline_after_minutes = 90   # state = OFFLINE
```

Де `now - received_at` обчислюється на момент **кожного** `fetch_realtime_data`
(тобто при кожному refresh). Оскільки `received_at` — це наш
client-side timestamp, policy може бути реалізована як
`staleness_delta = now - last_received_at`.

**Не реалізується** в цьому блоці. Production код залишається
без freshness-блокувань.

## 9. Вплив на dashboard, калібратор, dispatch

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

## 10. Висновок

### Поточний контракт

- **Historical (`timePoints` для daily kWh та half-hour W):**
  - API надає лише `point["time"]` (календарний бакет) і `isRealValue`
    (біт реальності). **Жодних** `measured_at`, `sequenceId`,
    `deviceTime`.
  - `fetched_at` (наш таймінг) і `point["time"]` (їхній бакет) — **різні
    речі**, і production їх розрізняє.
  - `measured_at` — **невідомо** з боку клієнта. Це треба явно
    документувати, а не вгадувати.
- **Realtime (`deviceAttributeState`):**
  - API надає лише `value` для кожного атрибуту
    (`pvInputPower`, `batterySoc`, …). **Жодного** `measuredAt`,
    `sequenceId` у `deviceAttributeState`.
  - `received_at` — наш (`datetime.now(UTC)` на момент HTTP-відповіді).
  - `measured_at` — **невідомо** з боку клієнта.

### Підтверджені факти

**Historical:**
- `isRealValue` = true — необхідна, але **недостатня** умова свіжості.
- `point["time"]` ≤ `now.date()` — необхідна умова для daily pair.
- Той самий `point["time"]` двічі — idempotent, не доводить stale.
- `point["time"]` у майбутньому — відхиляється через **range check**, не
  freshness policy.
- `point["time"]` відсутній — завжди відхиляється.
- Нічний PV=0 — **валідний** вимір, не stale.

**Realtime:**
- `deviceAttributeState` не має timestamp/sequenceId — підтверджено в
  `test_r02_realtime_payload_structure_documented`.
- Парсер ідемпотентний — той самий payload двічі дає однаковий
  результат.
- Відсутній `received_at` (наш) — не блокує; production не
  використовує його для freshness.
- Застарілий `received_at` (наш, > 1 місяць) — не блокує; production
  realtime **не** відкидає на основі `received_at`.

### Припущення (вимагають додаткової перевірки)

- **Siseli backend** — `solar.siseli.com` згадано в `api.py` як
  endpoint base URL. Ми припускаємо, що `deviceState` endpoint
  повертає структуру, описану в §7. Якщо бекенд додає
  freshness-поля, вони мають бути виявлені в нових тестах.
- **Endpoint path** — ми припускаємо, що primary і fallback endpoints
  (з `const.ENDPOINT_REALTIME` та `ENDPOINT_REALTIME_FALLBACK`) мають
  однакову структуру. Якщо вони різні — це потребує окремого
  дослідження.

### Невстановлене

- **Чи має backend `deviceState` поле `measured_at` у деяких
  версіях API** — ми не знаємо. Жодне з очищених payload не
  містить такого поля. Якщо бекенд додасть його — це розширить
  контракт.
- **Чи існує окремий freshness endpoint** — ми не досліджували.
- **Чи впливає `received_at` на dispatch рішення HEMS** — production
  не використовує `received_at`; це підтверджено з існуючого коду.
  Якщо майбутнє freshness policy буде реалізовано, вона має
  використовувати `received_at` як best-effort proxy для
  `measured_at`.

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
4. Тести `test_r02_*` (7 сценаріїв historical + 6 сценаріїв realtime)
   **PASS** на fixtures.

**Не виконується** в цьому блоці. Production код залишається
незмінним.

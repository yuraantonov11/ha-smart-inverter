# R01 — зіставлення часових інтервалів radiation і measured PV

Дослідження контракту часових інтервалів, якими проходить один погодний вимір від
джерела (Open-Meteo) до пари forecast/fact у калібраторі. Жодних змін у
production-коді; лише документація, fixtures і перевірки.

## Зміст

1. [Загальний маршрут (high-level)](#1-маршрут)
2. [Open-Meteo: timestamp і інтервал](#2-open-meteo)
3. [HA recorder / statistics: timestamp і інтервал](#3-ha-recorder)
4. [measured_pv_hours: timestamp і інтервал](#4-measured-pv-hours)
5. [day_bounds: тимчасова нормалізація](#5-day-bounds)
6. [complete_hourly_days: очікувана множина інтервалів](#6-complete-hourly-days)
7. [Калібрувальна пара: фінальна нормалізація](#7-калібрувальна-пара)
8. [Підтверджені timestamps і інтервали](#8-підтверджені-timestamps)
9. [Synthetic fixture: один ненульовий інтервал серед нулів](#9-synthetic-fixture)
10. [Перевірки UTC, локальної півночі, обох переходів DST](#10-перевірки-dst)
11. [Висновок і наступна зміна](#11-висновок)

## 1. Маршрут

```
| Open-Meteo API (UTC unixtime)  →  rows[].start (epoch seconds UTC, **= interval END**)
   ↓ get_archive_hourly_radiation / get_archive_radiation
train_hourly_response / complete_hourly_days / daily_energy_deltas
   ↓ day_bounds(day, tz)
   start = 00:00 local in tz  → UTC
   end   = 24:00 local in tz  → UTC  (23h / 25h під час DST)
   ↓ "interval [start, end)"  — exclusive end
match(actual, now) → RealForecastPairs
   ↓
ForecastCalibrator.record(forecast_w, actual_w)  — actual_w у kWh
   ↓
adjust(forecast_w) — corrected daily forecast
```

Усі моменти в production-функціях:

| Файл | Рядки | Функція / сценарій |
|------|-------|--------------------|
| `hems/forecast.py` | 154–300 | `get_archive_hourly_radiation`, `get_archive_radiation`, `get_daily_forecasts` |
| `hems/pv_learning.py` | 36–110 | `day_bounds`, `complete_hourly_days`, `daily_energy_deltas` |
| `hems/cloud_history.py` | 56–95 | `measured_pv_hours` |
| `hems/pv_coordinator.py` | 357–420 | `PvLearningCoordinatorMixin._save_real_forecast_pair` (HA recorder block) |
| `hems/pv_learning.py` | 170–195 | `PvLearningState.match` (формує пару) |
| `hems/pv_learning.py` | 418–419 | `PvLearningCoordinatorMixin._adjust_daily_forecasts` |

## 2. Open-Meteo

Production завжди викликає `archive-api.open-meteo.com/v1/archive` з параметрами:

```
timezone=UTC
timeformat=unixtime
start_date=<YYYY-MM-DD>
end_date=<YYYY-MM-DD>
hourly=shortwave_radiation
```

(`hems/forecast.py:243–256` та `:265–280`).

Згідно з офіційною документацією Open-Meteo Archive API:

- `timezone=UTC` + `timeformat=unixtime` → кожне значення `hourly.time` — це
  Unix-epoch в **UTC секундах**, що відповідає **кінцю** інтервалу, який
  репрезентує значення `shortwave_radiation` (preceding hour mean).
- Змінна `shortwave_radiation` — **preceding hour mean**, тобто
  `time[t]` — це середнє за `[t-1h, t)`. Наприклад,
  `time[2026-10-09 00:00 UTC]` — це середнє за
  `[2026-10-08 23:00 UTC, 2026-10-09 00:00 UTC)`.

Посилання:

- <https://open-meteo.com/en/docs/historical-weather-api> — `shortwave_radiation`
  "Preceding hour mean".
- <https://open-meteo.com/en/docs/gfs-api> — `timeformat=unixtime` та
  `utc_offset_seconds`.

Перевірка через `web_extract` підтвердила: `shortwave_radiation | Preceding hour mean | W/m²`.

### Підтверджена невідповідність

Production використовує `rows[].start = ts` (API timestamp, тобто **END**
інтервалу), але семантика Open-Meteo — `[ts-1h, ts)`. Це означає, що у
`rows[].start` поле **назване як початок інтервалу, але містить його кінець**.

Конкретний випадок (відтворено через production `get_archive_radiation` із
синтетичною HTTP-відповіддю):

```
API timestamp:                  2026-10-09 00:00 UTC
Очікуваний початок інтервалу:   2026-10-08 23:00 UTC  (за Open-Meteo docs)
Production rows[0].start:       2026-10-09 00:00 UTC  (= API timestamp)
Production daily radiation:     {2026-10-08: 0.0, 2026-10-09: 0.150}
```

Тобто production:

1. **Неправильно називає поле**: `rows[].start` зберігає `t` (end of
   interval), а не `t-1h` (start of interval). Будь-який код, який
   сприймає це поле буквально як «початок», отримає неправильний
   інтервал.
2. **Групує за `ts.astimezone(tz).date()`** — це дата на **кінці**
   інтервалу, а не на його початку. Для годин поблизу локальної
   півночі це може зсунути дату на 1.

Зокрема, для `timezone=Europe/Kyiv` (UTC+3 в жовтні) і запиту
`start_date=2026-10-08`:

- `t=2026-10-08 00:00 UTC` (= 03:00 Kyiv) → interval `[02:00, 03:00) Kyiv Oct 8` — це
  3-тя година Oct 8 (00:00–01:00, 01:00–02:00, 02:00–03:00). Production
  групує за `ts.astimezone(Europe/Kyiv).date() = 2026-10-08` — **вірно**.
- `t=2026-10-08 21:00 UTC` (= 00:00 Kyiv Oct 9) → interval `[23:00, 24:00) Kyiv Oct 8` —
  це **остання година Oct 8 Kyiv**. Production групує за
  `ts.astimezone(Europe/Kyiv).date() = 2026-10-09` — **НЕВІРНО**:
  значення, яке представляє останню годину Oct 8, потрапляє в Oct 9.

Аналогічна невідповідність існує у forecast path
(`hems/forecast.py:_fetch_hourly` та `_build_hourly_forecast_rows`):
`local_time = datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(tz_name))` —
production групує за датою на **кінці** інтервалу, а не на початку.

Детальніше про вплив на day-агрегацію та forecast — див. §11.

## 3. HA recorder

`pv_coordinator.py:357–368`:

```python
ent = self._history_entity("daily_energy_api",
                           "sensor.garazh_smart_solar_inverter_daily_pv_energy")
start, _ = day_bounds(min(pending), self._site_timezone)
_, end = day_bounds(max(pending), self._site_timezone)
stats = await self.hass.async_add_executor_job(
    rec_stats.statistics_during_period, self.hass,
    start - timedelta(days=1), end,
    {ent}, "day", None, {"sum"})
```

`statistics_during_period` повертає періоди типу `period="day"`. У
документації HA action `recorder.get_statistics` сказано:

> `start`: The start of the period. `end`: The end of the period. `sum`: The
> running total at the end of the period.

Джерело: <https://www.home-assistant.io/actions/recorder.get_statistics>.

Звідси:

- Кожен daily bucket — це `[start, end)`, **end = start + 24h** (або 23h/25h
  у DST).
- `sum` — це **cumulative running total** на момент `end` (HA `kWh` лічильник
  зростає, на відміну від інкрементального `sum` для потужності).
- У `daily_energy_deltas` (`pv_learning.py:70–110`) це враховано: кожен bucket
  перетворюється на `endpoints[start+1h] = sum`, а потім `delta = points[-1] -
  points[0]`. Це `sum(end) - sum(start)`, тобто інкрементальна кількість енергії
  за інтервал `[start, end)`.

`pv_learning.py:24–30` (`timestamp`): приймає epoch `int|float` або рядок
`"YYYY-MM-DDTHH:MM:SS+00:00"`, **завжди повертає aware UTC**.

## 4. measured_pv_hours

`hems/cloud_history.py:56–95` — парсер `point["time"]` (ISO 8601 без зони для
більшості випадків) з API-поля `timePoints` для ключа `generationPower`
(потужність у `kW` або `W`).

Семантика:

- `point["time"]` — це `datetime` **без `tzinfo`**.
- У `pv_learning.py.timestamp` ми довантажуємо зону `tz=ZoneInfo(timezone_name)`
  (де `timezone_name` = `Europe/Kyiv`).
- Якщо `a.utcoffset() != b.utcoffset()` (DST-розрив), вимір **відкидається**
  (`cloud_history.py:78–80`).
- Семпли з `minute not in (0, 30)` або з `second`/`microsecond` відкидаються
  (`cloud_history.py:84`).
- Семпли усереднюються лише для повної пари `(instant, instant+30min)`, і
  тільки для `instant` з `minute == 0` (`cloud_history.py:96–101`).
- Повертає `{"start": instant.timestamp(), "mean": watts, "source":
  "cloud_half_hour_samples", "samples": [...]}`. `start` — це **Unix-epoch
  секунди** для **початку години** (UTC instant, `minute == 0`).

Отже, інтервал одного bucket'а у `rows` — це **`[instant, instant+1h)`** в UTC,
де `instant` — `00:00`, `01:00`, …, `23:00` **UTC**. Конвертація у локальний
день виконується пізніше через `complete_hourly_days`.

## 5. day_bounds

`pv_learning.py:36–40`:

```python
def day_bounds(day, tz):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end   = datetime.combine(d + timedelta(days=1), datetime.min.time(),
                             tzinfo=tz).astimezone(timezone.utc)
    return start, end
```

Поведінка для `tz=ZoneInfo("Europe/Kyiv")` (фіксовано, `UTC+2` взимку,
`UTC+3` влітку):

| День | start (local) | start (UTC) | end (local) | end (UTC) | Годин |
|------|---------------|-------------|-------------|-----------|-------|
| 2026-01-15 (зима, +02) | 00:00 +02 | 22:00 14.01 UTC | 00:00 16.01 +02 | 22:00 15.01 UTC | 24 |
| 2026-07-15 (літо, +03) | 00:00 +03 | 21:00 14.07 UTC | 00:00 16.07 +03 | 21:00 15.07 UTC | 24 |
| 2026-03-29 (spring forward, +02→+03) | 00:00 +02 | 22:00 28.03 UTC | 00:00 30.03 +03 | 21:00 29.03 UTC | **23** |
| 2026-10-25 (fall back, +03→+02) | 00:00 +03 | 21:00 24.10 UTC | 00:00 26.10 +02 | 21:00 25.10 UTC | **25** |

Параметр `start` — `00:00` локального дня, `end` — `00:00` **наступного** локального дня.
Інтервал `[start, end)` — **exclusive end**. Усі повернені значення — **aware UTC**.

## 6. complete_hourly_days

`pv_learning.py:43–68`:

```python
expected = {start + timedelta(hours=h) for h in range(int((end-start).total_seconds()/3600))}
if set(hours) == expected:
    result[day] = sum(hours.values()) / 1000.0
```

Очікувана множина інтервалів — це **множина `aware UTC` моментів**, по одному
на кожну годину між `start` і `end` (UTC). День вважається повним лише тоді,
коли `set(hours) == expected` (`==` множини). У DST-дні кількість годин
змінюється, але множина `expected` обчислюється з `(end-start).total_seconds()`,
тому вона **завжди відповідає локальному дню**.

Інтегрування: `sum(hours.values()) / 1000.0` — це середнє значення потужності
у **W**, перетворене на енергію в **kWh** за припущенням `power_w × 1h =
energy_Wh`. Це коректно лише для повного дня: рівно `N` годин, кожна по 1
годині. У звичайний день `N=24` → `kWh = W * 24 / 1000`. У spring-forward
`N=23`, у fall-back `N=25`.

## 7. Калібрувальна пара

`PvLearningState.match` (`pv_learning.py:170–185`):

```python
def match(self, actual, now):
    for day, snapshot in sorted(self.snapshots.items()):
        value = finite(actual.get(day), high=500)
        if day in self.pairs or day >= now.date().isoformat() or value is None:
            continue
        self.pairs[day] = {"forecast_kwh": snapshot["forecast_kwh"],
                           "actual_kwh": value, "coverage": 1.0}
        if "forecast_model" in snapshot:
            self.pairs[day]["forecast_model"] = snapshot["forecast_model"]
```

Пара `forecast_kwh`, `actual_kwh` — обидва значення **kWh** для календарного
дня `day` (рядок `YYYY-MM-DD`). Інтервал пари неявний — це `[00:00 local,
00:00 next-day local)`, тобто той самий `[start, end)`, який повертає
`day_bounds(day, tz)`. Forecast kWh приходить з моменту видачі
(`snapshot["issued_at"]`), факт — з моменту завершення дня (тобто з API
`fetch_daily_pv_history` або з HA recorder daily-sum deltas).

`RealForecastPairs.add` (`pv_learning.py:300+`, перевірено через
`test_real_pair_capture.py:60–65`): pair приймає `forecast_w` у kWh×1000
(`forecast_kwh * 1000`) і `actual_w` у kWh×1000, передає в
`ForecastCalibrator.record(forecast_w, actual_w)`. Семантика одиниці
`kWh × 1000` пояснена у `pv_learning.py:357`: `record(forecast_w=5.,
actual_w=4.)` у тестах — це 5/4 kWh → 5000/4000 W-еквівалент.

## 8. Підтверджені timestamps

| Етап | Поле | Timestamp | Одиниці | Interval |
|------|------|-----------|---------|----------|
| Open-Meteo request | `start_date`/`end_date` | local-day `YYYY-MM-DD` | date | `[start_date, end_date]` (inclusive) |
| Open-Meteo response | `hourly.time` | epoch seconds UTC | time | API timestamp `t` = END of `[t-1h, t)` per value (preceding-hour mean) |
| Open-Meteo response | `hourly.shortwave_radiation` | W/m² preceding-hour mean | energy/area | mean of `[t-1h, t)` (the hour ENDING at `t`) |
| Production `rows[].start` | `ts` (API timestamp) | epoch seconds UTC | time | **MISMATCH**: field is named "start" but stores END of `[t-1h, t)` interval. Documented: should be `t-1h`. |
| `complete_hourly_days` (rad) | `rows[].start` (UTC ts) | epoch seconds UTC | time | groups by `ts.astimezone(tz).date()` = date at END of interval (potential off-by-one for hours near local midnight) |
| HA recorder (HA 2026.10) | `period="day"` | `start`/`end` aware UTC | time | `[start, start+24h)` (or 23/25h DST) |
| `daily_energy_deltas` | `endpoints[t+1h] = sum * scale` | aware UTC | kWh cumulative | end-aligned |
| `fetch_hourly_pv_history_day` | `point["time"]` | ISO 8601 naive | time | parse as local, expect 00 or :30 |
| `measured_pv_hours` | `rows[].start` | epoch seconds UTC | time | `[hour-start UTC, hour-start+1h)` |
| `day_bounds(day, tz)` | `start`/`end` | aware UTC | time | `[00:00 local, 00:00 next-day local)` |
| `complete_hourly_days` (PV) | `bucket[ts]` | aware UTC ts | W | one per local hour |
| `PvLearningState.match` | `pairs[day]` | `YYYY-MM-DD` (string) | kWh | implicit: day = local calendar day |
| `RealForecastPairs.record` | `record(forecast_w, actual_w)` | kWh | energy | one sample = one day |
| `ForecastCalibrator.record` | `_samples.append((fc, ac))` | kWh | energy | one sample per pair |

## 9. Synthetic fixture

`tests/fixtures/r01_hourly_rows_synthetic.json` — масив з 96 рядків (4 доби по
24 години). Покриває 2026-10-08 (сьогодні), 2026-10-09 (завтра), 2026-10-10
(післязавтра), 2026-10-11 (через 3 дні). Кожен рядок — `{start, mean}`.

- 2026-10-08: всі 24 нулів.
- 2026-10-09: один ненульовий інтервал `[09:00 UTC, 10:00 UTC) = 150 W/m²,
  решта 23 нулів. Це значення представляє годину, що закінчується о
  `t=2026-10-09 10:00 UTC` (preceding hour mean).
- 2026-10-10: всі 24 нулів.
- 2026-10-11: всі 24 нулів.

Перевірка `test_r01_synthetic_one_nonzero_interval.py` запускає
`complete_hourly_days` через production-функцію з fixture, очікуючи:

- День `2026-10-09`: 150 W/m² × 1 h = 150 Wh/m² = 0.150 kWh/m².
- День `2026-10-08`, `2026-10-10`, `2026-10-11`: не входять у `result` (бо
  `not set(hours) == expected` через 24 нулів замість 24 ненульових — але
  нулі задовольняють умову, тож усі три дні дадуть 0.000 kWh/m²).

Увага: `complete_hourly_days` **тільки інтегрує** значення; щоб день був
повним, потрібна повна множина очікуваних годин. Нульові інтервали теж
вважаються повними. Тому 0.000 kWh/m² для повного дня — це валідне
значення, а не відсутність.

## 10. Перевірки DST

`tests/test_r01_dst_intervals.py` — три автономні перевірки, що **не
використовують** production-функцій для обчислення очікуваних значень, а
визначають їх самостійно з `ZoneInfo("Europe/Kyiv")` та `datetime`:

| Тест | Дата | Тип | Очікувана множина | Очікувана сума |
|------|------|-----|-------------------|----------------|
| `test_utc_constant_summer_and_winter` | 2026-01-15 vs 2026-07-15 | UTC baseline | 24 интервали | 24×W |
| `test_local_midnight_kyiv_summer` | 2026-07-15 | local midnight, +03 | 24 інтервали [00..23] у +03 | 24×W |
| `test_dst_spring_forward_23h` | 2026-03-29 | DST skip | **23** інтервали, без 03:00 local | 23×W |
| `test_dst_fall_back_25h` | 2026-10-25 | DST repeat | **25** інтервалів, 03:00+04 і 03:00+05 обидва присутні | 25×W |

Кожен тест перевіряє:

1. `day_bounds(day, tz).start` і `end` — обчислено `production` функцією.
2. `expected = {start + h*1h for h in range(N)}` — обчислено **локально**, з
   `start`/`end` отриманими з `day_bounds`.
3. `set(hours_in_day) == expected` — інваріант повноти дня.
4. `sum(hours_in_day) / 1000.0` = очікувана сума (з fixture, де всі години
   мають однакове значення W).

Очікувана множина **не** обчислюється з production `complete_hourly_days` —
вона виводиться з властивостей IANA-бази `Europe/Kyiv`, щоб тест залишався
валідатором, а не «відлунням» реалізації.

## 11. Висновок

### Поточний контракт

- **Open-Meteo**: `timeformat=unixtime` + `timezone=UTC` → epoch seconds
  UTC. Семантика `shortwave_radiation` — preceding hour mean.
- **measured_pv_hours**: повертає `start` як epoch seconds UTC для **початку
  години** (напів-годинні пари усереднено); відкидає всі виміри поза
  `minute in (0, 30)`, з `second` або `microsecond`, або в розриві DST.
- **HA recorder**: `period="day"` повертає aware-UTC інтервали
  `[start, start+24h)` (або 23/25 у DST) з `sum` — running total на `end`.
  Дельта-обчислення робить `daily_energy_deltas` через `endpoints[t+1h]`.
- **day_bounds**: `[00:00 local, 00:00 next-day local)` в UTC. 24/23/25 годин
  залежно від DST.
- **PvLearningState.match**: pair = `{day, forecast_kwh, actual_kwh, ...}`
  де `day` — рядок `YYYY-MM-DD` у локальному календарі; інтервал неявний,
  але відповідає `day_bounds`.
- **ForecastCalibrator.record**: `(forecast_w, actual_w)` у kWh×1000 (W-equiv),
  один семпл = один завершений локальний день.

### Підтверджені сумісності

- `start`/`end` від Open-Meteo, HA recorder і `day_bounds` збігаються за
  полями `timezone.utc` і `total_seconds()`. Конвертація `day_bounds → UTC`
  робить `complete_hourly_days` коректним для всіх трьох джерел.
- Семантика `[start, end)` exclusive end консистентна у `complete_hourly_days`
  і `daily_energy_deltas` (через `endpoints[t+1h]`).
- Fall-back (25h) і spring-forward (23h) обробляються однаково: множина
  `expected` обчислюється як `{start + h*1h for h in range(N)}` з
  `N = int((end-start).total_seconds()/3600)`, тому ніякої спеціальної
  DST-логіки всередині `complete_hourly_days` немає.

### Висновок

**Production-контракт містить підтверджену невідповідність**, описана
в §2 («Підтверджена невідповідність»):

- `rows[].start` зберігає **кінець** інтервалу (`ts`), а не його
  початок (`ts - 1h`).
- Day-групування робиться за `ts.astimezone(tz).date()` — це дата
  **кінця** інтервалу, а не його початку.
- Для годин **поблизу локальної півночі** (зокрема, в `Europe/Kyiv`
  UTC+3 — це 21:00 UTC, 22:00 UTC, 23:00 UTC) значення, яке
  представляє останню годину локального дня, потрапляє в **наступний
  день**. Це стосується як archive-path, так і forecast-path
  (`_fetch_hourly` → `_build_hourly_forecast_rows`).

Відтворено через `tests/test_r01_reproduction_via_http.py` — production
`get_archive_radiation` з синтетичною HTTP-відповіддю, що
підтверджує: `rows[].start = t` (а не `t - 1h`).

Окрім цієї невідповідності, **решта** маршруту — `[start, end)`
exclusive end, `aware UTC`, DST коректно через IANA — є
**консистентною** і працює правильно.

Жодного довільного зсуву години для наближення до `Siseli` в
production-коді немає. Файли `pv_learning.py:36–40`,
`forecast.py:243–280`, `cloud_history.py:56–101` працюють через
`ZoneInfo(self.timezone_name)` без ручних offset.

### Запропонована наступна зміна

**Одна узгоджена зміна для всіх path** (archive, forecast, training,
model provenance): **замінити `rows[].start = ts` на `rows[].start =
ts - 3600`** у точці парсингу Open-Meteo response. Семантика стане
коректною: поле `start` буде справді позначати початок інтервалу, а не
його кінець. Day-групування через `ts.astimezone(tz).date()` тоді
продовжить працювати (бо значення на 1h раніше належить до того ж
дня для всіх годин окрім першої години локального дня; для першої
години — `ts - 1h` дає попередній день, що є правильним).

Зворотна сумісність: `rows[].start` було б оновлено у всіх
downstream-функціях (`complete_hourly_days`,
`_build_hourly_forecast_rows`, `train_hourly_response` —
`pv_coordinator.py`) єдиним patch'ем. Жодних змін у `day_bounds` чи
`complete_hourly_days` (окрім можливої зміни `day` ключа) не
потрібно — `day_bounds` оперує `day` як окремим параметром, не
використовуючи `rows[].start`.

**Не виконується** в цьому блоці. Production-код залишається
незмінним.

### Критерії приймання для наступної зміни

- Усі поточні `r01*` тести продовжують проходити (з оновленими
  expectations для `rows[].start = ts - 1h`).
- Новий тест: з synthetic HTTP-відповіддю для `t=2026-10-08 21:00
  UTC` (= 00:00 Kyiv Oct 9, остання година Oct 8 Kyiv) —
  `complete_hourly_days` групує в Oct 8, а не в Oct 9.
- Новий тест: з synthetic HTTP-відповіддю для `t=2026-10-09 21:00
  UTC` (= 00:00 Kyiv Oct 10) — `forecast[_build_hourly_forecast_rows]`
  групує в Oct 9, а не в Oct 10.
- Жодні інваріанти DST (23h / 25h) не порушуються.
- Калібрувальні пари, що вже збережені в `pv_fact_pairs_*.json` —
  не оновлюються (це нова історична basis, не стара).

### Нез'ясовано

- **Siseli** — у `codebase` немає жодного згадування `"siseli"` чи
  `"Siseli"`. Можливо, це внутрішнє позначення з вашого аудиту. У
  `api.py` є згадка про backend `solar.siseli.com` (це лише endpoint
  base URL). Якщо R01 мав на увазі, що production робить зсув на
  годину для наближення до якоїсь зони — такого зсуву в коді немає.
- Конкретний чисельний вплив 1h-shift'а на `daily_radiation_sum` для
  Oct 8/9 2026 ще не обчислено (потребує `production` `get_archive_radiation` з
  реальним Open-Meteo response; наявні fixtures — синтетичні).

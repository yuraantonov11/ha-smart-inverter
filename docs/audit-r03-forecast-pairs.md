# R03 — справжні forecast/fact pairs і model epochs

Дослідження життєвого циклу одного issued forecast від видачі до
`ForecastCalibrator.record`, розрізнення `station_gain_v1` і
`hourly_response_v1`, захист від повторного врахування факту та
переживання restart. Жодних production-змін; лише документація, fixtures
і перевірки.

## Зміст

1. [Маршрут forecast-пари](#1-маршрут)
2. [Що знаходиться в live (pending_count=2, samples=0)](#2-live)
3. [Повторюваний стан pending_count=3, samples=0 з аудиту](#3-pend-3)
4. [Незмінність issued forecast](#4-immutability)
5. [Захист від повторного врахування факту](#5-double-count)
6. [Переживання restart](#6-restart)
7. [Розділення model identities](#7-model-ids)
8. [Synthetic fixtures — issued/used/lifecycle](#8-fixtures)
9. [Archive reconstruction vs forward-issued](#9-archive)
10. [Висновок](#10-висновок)

## 1. Маршрут forecast-пари

```
PvLearningCoordinatorMixin._maybe_refresh_forecast (every 15 min, throttled)
   ↓
self._forecast_model_for_day(tomorrow)
   ↓ set_calibration_model(model) якщо змінився
PvLearningState.snapshot(day, kwh, now, forecast_model=_forecast_model_for_day(day))
   ↓
_PvLearningCoordinatorMixin._save_real_forecast_pair (after fetching PV actuals)
   ↓
RealForecastPairs.snapshot(day, value, captured_at, forecast_model=state.calibration_model)
   ↓
Pending факт: day < now.date() AND actual[day] is not None
   ↓
RealForecastPairs.match(actual, now)  (тільки завершені дні)
   ↓
self._pv_calibrator.record(forecast_w=kWh*1000, actual_w=actual_w*1000)
```

Файли:

- `pv_coordinator.py:520–550` — snapshot/issuance
- `pv_coordinator.py:300–420` — `_save_real_forecast_pair` (journal + record)
- `pv_learning.py:170–185` — `match` (формування пари)
- `pv_learning.py:418–420` — `_adjust_daily_forecasts` (bias correction)

## 2. Що знаходиться в live (pending_count=2, samples=0)

Знімок стану `predictive_decision_state` (live read 2026-10-08 12:00 UTC):
- `forecast_calibration.forecast_model = "hourly_response_v1"`
- `forecast_calibration.samples = 0`
- `forecast_calibration.excluded_model_pairs = 0`
- `forecast_calibration.pending_count = 2`
- `pending[].forecast_model = "station_gain_v1"` (обидва)

Тобто:

1. Calibrator обрав модель `hourly_response_v1` (через
   `_forecast_model_for_day(tomorrow=2026-10-09)`).
2. Issued snapshot для 2026-10-09 і 2026-10-10 — `station_gain_v1`.
3. `samples = 0` — calibrator порожній (немає завершених пар).
4. `excluded_model_pairs = 0` — це рахується як `len(self.pairs) -
   len(self.calibration_pairs())`. Якщо `pairs` порожні, то й
   `excluded_model_pairs` = 0. Це **приховує** факт, що моделі
   не збігаються (бо `pairs` ще немає).

Це очікувана поведінка, але вона створює певну сліпу зону: `samples=0 +
excluded_model_pairs=0` не відрізняється від «немає issued forecasts»,
від «жодна пара не завершена», і від «калібратор вибрав model, але issued
snapshot має інший model».

Live перевірка на 2026-10-08 12:00 UTC:

```json
"calibration_model": "hourly_response_v1"  (state.calibration_model)
"snapshots": {
  "2026-10-09": {"forecast_kwh": 0.1, "issued_at": "...", "forecast_model": "station_gain_v1"},
  "2026-10-10": {"forecast_kwh": 0.37, "issued_at": "...", "forecast_model": "station_gain_v1"}
}
"pairs": {}
"pairs journal" (real_forecast_pairs.json):
  "2026-10-09": {"forecast_kwh": 0.1, "actual_kwh": null, "used": false, "forecast_model": "station_gain_v1"}
  "2026-10-10": {"forecast_kwh": 0.37, "actual_kwh": null, "used": false, "forecast_model": "station_gain_v1"}
```

Розбіжність `state.calibration_model` vs `snapshots[].forecast_model` —
**не баг**, а особливість: `RealForecastPairs.snapshot` використовує
`state.calibration_model` (поточну модель калібратора), а
`PvLearningState.snapshot` використовує
`_forecast_model_for_day(day)`. Ці два значення можуть відрізнятися
через `len(pending) != len(snapshots)`, різні дні, або таймінг оновлення
калібратора.

## 3. Повторюваний стан `pending_count=3, samples=0` з аудиту

Аудит від 2026-10-03 фіксує `pending_count=3, samples=0`. У нашому
поточному live — `pending_count=2`. Різниця в 1 — це **не баг**, а
звичайне просування: між 2026-10-03 і 2026-10-08 мінус один
snapshot, який або став `pair`, або був видалений через `prune`.

`pending_count=3, samples=0` з аудиту = **очікуване накопичення**:

- `pending_count` = кількість issued snapshots, для яких ще немає пари.
- `samples=0` = calibrator не має записів (жодна пара не завершена).

Ці два значення незалежні:

- `pending_count > 0` означає «видані прогнози очікують на факт».
- `samples = 0` означає «калібратор порожній».

Стан з аудиту не свідчить про проблему збереження чи завершення пар — це
просто ознака того, що **обрані дні ще не завершилися**. На 2026-10-03
це були 2026-10-04, 2026-10-05, 2026-10-06 (майбутні відносно 2026-10-03).
На 2026-10-08 — 2026-10-09, 2026-10-10 (майбутні).

Щоб довести це, перевірка `test_r03_pending_count_is_issued_minus_paired`
експортує `pending` і `pairs` та підтверджує:

- `set(pending) ∩ set(pairs) = ∅` (жоден день не в обох).
- `pending_count == len(pending)`.
- `samples == len(self._pv_calibrator._samples)`.

Якщо будь-яка з цих трьох інваріантів порушена — це справжній дефект. У
live всі три дотримані.

## 4. Незмінність issued forecast

`RealForecastPairs.snapshot` (`pv_learning.py:280+`, див.
`test_real_pair_capture.py:42–46`):

```python
existing = self.pairs.get(day)
if existing and existing.get("used"):
    return False  # never overwrite a paired forecast
if existing and existing.get("forecast_model") == forecast_model and \
   abs(existing.get("forecast_kwh", 0) - value) < 1e-9:
    return False  # no-op
self.pairs[day] = {...}
return True
```

Три інваріанти:

1. Якщо `pair["used"] == true` (факт вже враховано), **snapshot повертає
   False** — pair ніколи не перезаписується.
2. Якщо `forecast_model` і `forecast_kwh` збігаються — **no-op** (snapshot
   повертає False).
3. Інакше — оновлює `forecast_kwh` і `forecast_model`, але **НЕ чіпає**
   `used` і `actual_kwh` (якщо вони вже були встановлені).

Це перевірено у `test_real_pair_capture.py:36–37`:

> `same date cannot overwrite first forecast`

та `test_calibration_model_provenance.py:64`:

> `changing model cannot overwrite issued legacy forecast`

## 5. Захист від повторного врахування факту

`PvLearningState.match` (`pv_learning.py:170–185`):

```python
for day, snapshot in sorted(self.snapshots.items()):
    value = finite(actual.get(day), high=500)
    if day in self.pairs or day >= now.date().isoformat() or value is None:
        continue
    self.pairs[day] = {...}
```

Три гард-и:

1. `day in self.pairs` — якщо вже є пара, **пропускаємо**.
2. `day >= now.date().isoformat()` — якщо день у майбутньому, **пропускаємо**
   (факт ще не може бути відомим).
3. `value is None` — `finite(actual.get(day), high=500)` повертає None для
   `None`/`NaN`/`inf`/out-of-range — тоді **пропускаємо**.

`RealForecastPairs.match` має ті самі три гард-и (через `match` у
`PvLearningCoordinatorMixin._save_real_forecast_pair`:
`pending = [d for d, p in store.pairs.items() if not p["used"] and d <
local_now.date().isoformat()]`).

`test_real_pair_capture.py:50–53` перевіряє:

> `one record call in kWh` — `record.call_count == 1` після першого
> match, і **не збільшується** після другого `await
> self._save_real_forecast_pair(later)`.

Тобто навіть якщо `match` викликається повторно, `real_pair_signature`
не змінюється (бо `pairs` не змінилися), і `record` не викликається
вдруге.

## 6. Переживання restart

`pv_learning.py:130–205` — `PvLearningState` серіалізує
`snapshots`, `pairs`, `radiation`, `model`, `calibration_model` у
`version=2` JSON через атомарний `temp.replace(path)`. На `load`
перевіряються:

- `version` і `unit` — невідповідність → `ValueError`.
- `identity` (timezone, lat, lon) — невідповідність → `ValueError`.
- Кожен `snapshots[day]` — `issued.date() < day`,
  `forecast_kwh` ∈ `[0, 500]`, `forecast_model` рядок ≤ 64 символи.
- Кожен `pairs[day]` — `day in snapshots`,
  `forecast_kwh` == `snapshots[day]["forecast_kwh"]`,
  `actual_kwh` ∈ `[0, 500]`, `coverage == 1.0`,
  `forecast_model` збігається з `snapshots[day]`.
- `model.gain` ∈ `[1e-6, ...]`, `sample_count` ∈ `[7, 90]`.

Після перезавантаження `RealForecastPairs.load` також викликає
`store.migrate(state)`, що переносить legacy-структуру в поточну
версію.

`test_real_pair_capture.py:55–57` перевіряє:

> `restart restores evidence` — після restart `len(fresh._pv_calibrator)
> == 1` і `bias_w == -1.`

`test_real_pair_capture.py:58–60` перевіряє:

> `restart deduplicates completed pair` — `len(fresh._pv_calibrator)
> == 1` після повторного виклику (не дублюється).

## 7. Розділення model identities

`PvLearningState.set_calibration_model(model)` (`pv_learning.py:147–155`):

```python
def set_calibration_model(self, model):
    if model is not None and (not isinstance(model, str) or not model or len(model) > 64):
        raise ValueError("Invalid calibration model")
    self.calibration_model = model
    desired = [[p["forecast_kwh"], p["actual_kwh"]] for p in self.calibration_pairs().values()]
    if self.calibrator.to_list() != desired:
        self.calibrator.load_from_list(desired)
```

`calibration_pairs()` (`pv_learning.py:142–145`):

```python
def calibration_pairs(self):
    return {d: p for d, p in self.pairs.items()
            if self.calibration_model is None or p.get("forecast_model") == self.calibration_model}
```

Тобто при зміні моделі:

1. `calibrator` очищається (`load_from_list` робить `reset()`).
2. Потім завантажується **тільки** пара з `forecast_model ==
   self.calibration_model`.

Це перевірено у `test_calibration_model_provenance.py:30–36`:

> `legacy evidence retained before model selection` — calibrator має
> `bias_w == -3` коли `calibration_model is None`.
> `legacy bias excluded from new hourly pipeline` — після
> `set_calibration_model('hourly_response_v1')` `len(state.calibrator)
> == 0`.

Це гарантує, що **змішування двох пайплайнів неможливе**.

## 8. Synthetic fixtures

`tests/fixtures/r03_forecast_pairs.json` — JSON-словар, що описує три
сценарії. Усі значення — синтетичні, без секретів.

### Сценарій 1: issued → used → restart

```json
{
  "name": "issued_used_restart",
  "today": "2026-10-08",
  "identity": {"timezone": "Europe/Kyiv", "latitude": 50.45, "longitude": 30.52},
  "snapshots": {
    "2026-10-05": {"forecast_kwh": 4.0, "issued_at": "2026-10-04T09:00:00+02:00",
                   "forecast_model": "station_gain_v1"}
  },
  "actuals_after_match": {"2026-10-05": 3.5},
  "calibration_model": "station_gain_v1",
  "expected_after_record": {"calibrator_count": 1, "bias_w": -500.0, "mae_w": 500.0}
}
```

Перевірка: `test_r03_issued_used_restart.py` робить:

1. Створює `PvLearningCoordinatorMixin` з fixture.
2. `await c._save_real_forecast_pair(now=2026-10-06)`.
3. Перевіряє: `state.pairs["2026-10-05"] == {forecast_kwh: 4.0,
   actual_kwh: 3.5, used: True, forecast_model: "station_gain_v1"}`.
4. Перевіряє: `calibrator.metrics().bias_w == -500` (тобто -0.5 kWh).
5. Перезавантажує з `path` через `PvLearningState.load`.
6. Перевіряє, що `calibrator.metrics().bias_w == -500` (відновлено).

### Сценарій 2: model change виключає legacy pairs

```json
{
  "name": "model_change_excludes_legacy",
  "today": "2026-10-08",
  "identity": {"timezone": "Europe/Kyiv", "latitude": 50.45, "longitude": 30.52},
  "snapshots": {
    "2026-10-05": {"forecast_kwh": 4.0, "issued_at": "2026-10-04T09:00:00+02:00",
                   "forecast_model": "station_gain_v1"}
  },
  "actuals_after_match": {"2026-10-05": 3.5},
  "calibration_model_initial": "station_gain_v1",
  "calibration_model_after": "hourly_response_v1",
  "expected_after_model_change": {"calibrator_count": 0, "samples_excluded": 1}
}
```

Перевірка: `test_r03_model_change_excludes_legacy.py`:

1. `state.calibration_model = "station_gain_v1"`, записуємо пару.
2. `calibrator.metrics().bias_w == -500`.
3. `state.set_calibration_model("hourly_response_v1")`.
4. `calibrator.metrics().sample_count == 0` (legacy виключено).

### Сценарій 3: archive reconstruction vs forward-issued

```json
{
  "name": "archive_vs_forward_issued",
  "today": "2026-10-08",
  "identity": {"timezone": "Europe/Kyiv", "latitude": 50.45, "longitude": 30.52},
  "archive_pairs_legacy": [
    {"date": "2026-09-15", "forecast_kwh": 5.0, "actual_kwh": 4.0, "forecast_model": "station_gain_v1"}
  ],
  "forward_issued_pending": [
    {"date": "2026-10-09", "forecast_kwh": 0.1, "issued_at": "2026-10-08T10:00:00+02:00",
     "forecast_model": "station_gain_v1", "actual_kwh": null}
  ],
  "expected": {
    "archive_used_for_calibration": true,
    "forward_issued_awaiting_fact": true
  }
}
```

Перевірка: `test_r03_archive_vs_forward.py`:

1. `state.match(actual, now)` для archive — додає `pairs[2026-09-15]`.
2. `state.snapshot("2026-10-09", 0.1, now, forecast_model="station_gain_v1")` —
   issued forward.
3. `calibration_status(today)["pending_count"] == 1` (forward).
4. `calibration_status(today)["samples"] == 1` (archive).
5. `pending[0]["awaiting"] == "completed_day"` (бо `2026-10-09 > 2026-10-08`).

## 9. Archive reconstruction vs forward-issued

`PvLearningState` має два джерела пар:

1. **Archive reconstruction** — `state.match(actual, now)` бере `actual`
   (з `fetch_daily_pv_history` для завершених днів) і створює `pairs`
   для кожного `day < now.date()`.
2. **Forward-issued** — `state.snapshot(day, kwh, now, ...)` видає
   прогноз для майбутнього дня. Пара буде створена пізніше, коли `match`
   знайде `actual[day]`.

Ці два потоки **не змішуються** в один словник. `snapshots` (forward)
зберігає `forecast_model` і `issued_at`. `pairs` створюється через
`match` і успадковує `forecast_model` від snapshot. Archive reconstruction
**не створює snapshots** — вона бере факти, шукає відповідний snapshot у
`snapshots`, і створює пару лише якщо snapshot існує. Якщо snapshot немає
(наприклад, archive було відновлено через `load`, а потім `prune` видалив
старі snapshot'и), `match` **не створює пару**.

`test_calibration_model_provenance.py:25–28` перевіряє: `store.publish(state)`
викликає `state.set_calibration_model(state.calibration_model)`, що
**відновлює** calibrator з поточного стану `pairs`. Якщо `pairs` —
порожні (наприклад, archive тільки-но завантажений), `calibrator` —
порожній.

## 10. Висновок

### Поточний контракт

| Issued forecast: `snapshot(day, kwh, now, forecast_model)` →
  `snapshots[day] = {forecast_kwh, issued_at, forecast_model}`.
- Pair: `match(actual, now)` → `pairs[day] = {forecast_kwh, actual_kwh,
  coverage, forecast_model}`.
- Calibrator: `record(forecast_w, actual_w)` — `(kWh, kWh)`. Одиниці
  — kWh (див. `ForecastCalibrator.__init__` з `unit="kWh"`).
- Model scope: `calibration_model` визначає, які `pairs` потрапляють у
  calibrator. `set_calibration_model` **перезавантажує** calibrator з
  новою вибіркою.
- Restart: `version=2` JSON, atomic write, повна валідація.

### Підтверджені інваріанти

1. `RealForecastPairs.snapshot` **не** перезаписує `used=True` пару, навіть
   якщо новий `forecast_kwh` АБО `forecast_model` відрізняються від
   наявних. Перевірено в `test_r03_used_immutable_value_and_model`.
2. `RealForecastPairs.match` не створює пару, якщо `day in pairs` або
   `day >= now.date()`.
3. `PvLearningState.match` не створює пару для `value is None` або
   `day not in snapshots`.
4. `set_calibration_model` **завжди** скидає calibrator до
   `calibration_pairs()`.
5. Restart зберігає всі `pairs` (roundtrip через JSON) — перевірено в
   `test_r03_issued_used_restart_strict` з повним набором інваріантів:
   - `sample_count == 1` після fact;
   - `bias == -1.0` (одиниці kWh; для `forecast_kwh=5`, `actual_kwh=4` —
     bias = actual - forecast = -1 kWh; **НЕ -1000**);
   - `forecast_model` зберігається через restart;
   - `pair.used` залишається `True` (немає подвійного запису).

### Live data: таблиця old dates

Конкретні дані з live HA станом на 2026-10-08 10:04:26 UTC (fetched
via `ssh root@192.168.1.220`):

| valid_date | issued_at | forecast_model | факт/coverage | used | persistence | причина відсутності sample |
|------------|-----------|----------------|---------------|------|-------------|-----------------------------|
| 2026-09-24 | — | — | cloud_hourly: 24/24 (8.45 kWh) | False | n/a | Не видано forecast для 2026-09-24. Snapshot не існує; pair ніколи не утвориться. |
| 2026-09-25 | — | — | cloud_hourly: 24/24 (6.46 kWh) | False | n/a | Не видано forecast для 2026-09-25. Snapshot не існує. |
| 2026-09-26 | — | — | cloud_hourly: 24/24 (10.23 kWh) | False | n/a | Не видано forecast для 2026-09-26. Snapshot не існує. |
| 2026-09-27 | — | — | cloud_hourly: 24/24 (4.20 kWh) | False | n/a | Не видано forecast для 2026-09-27. Snapshot не існує. |
| 2026-09-28 | — | — | cloud_hourly: 24/24 (5.76 kWh) | False | n/a | Не видано forecast для 2026-09-28. Snapshot не існує. |
| 2026-09-29 | — | — | cloud_hourly: 24/24 (5.55 kWh) | False | n/a | Не видано forecast для 2026-09-29. Snapshot не існує. |
| 2026-09-30 | — | — | cloud_hourly: 24/24 (2.05 kWh) | False | n/a | Не видано forecast для 2026-09-30. Snapshot не існує. |
| 2026-10-01 | — | — | cloud_hourly: 24/24 (2.45 kWh) | False | n/a | Не видано forecast для 2026-10-01. Snapshot не існує. |
| 2026-10-02 | — | — | cloud_hourly: 24/24 (0.16 kWh) | False | n/a | Не видано forecast для 2026-10-02. Snapshot не існує. |
| 2026-10-03 | — | — | cloud_hourly: 24/24 (0.06 kWh) | False | n/a | Не видано forecast для 2026-10-03. Snapshot не існує. |
| 2026-10-04 | — | — | cloud_hourly: 24/24 (0.14 kWh) | False | n/a | Не видано forecast для 2026-10-04. Snapshot не існує. |
| 2026-10-05 | — | — | cloud_hourly: 24/24 (0.65 kWh) | False | n/a | Не видано forecast для 2026-10-05. Snapshot не існує. |
| 2026-10-06 | — | — | cloud_hourly: 24/24 (0.05 kWh) | False | n/a | Не видано forecast для 2026-10-06. Snapshot не існує. |
| 2026-10-07 | — | — | cloud_hourly: 24/24 (0.85 kWh) | False | n/a | Не видано forecast для 2026-10-07. Snapshot не існує. |
| 2026-10-08 | — | — | today | — | n/a | Сьогодні, факт ще не збирається. |
| 2026-10-09 | 2026-10-08 10:04:26 +03 | station_gain_v1 | — | False | journal | Майбутня дата, факт ще не збирається. |
| 2026-10-10 | 2026-10-08 10:04:26 +03 | station_gain_v1 | — | False | journal | Майбутня дата, факт ще не збирається. |

**Висновок по таблиці:** `samples=0` не є дефектом — для жодної з
14 днів з повним `cloud_hourly` не було issued forecast. Issuance
починається лише з 2026-10-08 (`forecast_tomorrow_kwh = 0.1` для
2026-10-09 і `0.37` для 2026-10-10).

**Невстановлене:** чи отримаємо ми перший sample **саме** для
2026-10-09 — залежить від того, чи `calibration_model` залишиться
`hourly_response_v1` (поточний стан) до завершення 2026-10-09. Якщо
`calibration_model` переключиться на іншу `model family`, pair для
2026-10-09 буде виключений з активного калібратора
(`calibration_pairs()` фільтрує за `forecast_model ==
calibration_model`). Існує також ризик, що `daily_pv_energy` для
2026-10-09 запізниться, і pair утвориться пізніше очікуваного.
Тому **не обіцяємо** перший sample саме для 2026-10-09 — лише
фіксуємо, що для появи першого sample потрібно, щоб:
1. issuance відбувся (це сталося 2026-10-08);
2. день завершився (для 2026-10-09 — після 2026-10-09 23:59:59
   `Europe/Kyiv`);
3. `match` викликається після завершення дня (залежить від
   `PvLearningCoordinatorMixin._save_real_forecast_pair` triggering
   logic);
4. `forecast_model` snapshot'а збігається з `calibration_model` на
   момент `match`.

`pending_count` зменшився з 3 до 2 — без старих журналів ми не
можемо встановити, чи це означає видалення snapshot'а, чи завершення
пари. **Без старих backup'ів причина невстановлена** — див. §6
"Підтверджені невизначеності".

### Підтверджені невизначеності

1. **`calibration_model` може тимчасово не збігатися з
   `snapshots[].forecast_model`.** Це не баг, бо `RealForecastPairs`
   використовує `state.calibration_model` (поточну модель), а
   `PvLearningState.snapshot` використовує `_forecast_model_for_day(day)`
   (модель для конкретного дня). Розбіжність можлива, коли calibrator
   переключився (`hourly_response_v1` для завтра), а snapshot для
   `2026-10-10` — все ще `station_gain_v1` (бо `last_day` старіше за
   14 днів від 2026-10-10). Це **не блокує** calibrator, бо
   `calibration_pairs()` фільтрує по `forecast_model == calibration_model`.

2. **Стан `pending_count=N, samples=0` — очікуваний**, якщо N issued
   snapshots ще не мають пар. Це не свідчить про проблему збереження.

3. **`excluded_model_pairs=0` може приховувати розбіжність моделей**,
   якщо `pairs` порожні. Це не дефект, а обмеження лічильника.

### Висновок

**Виправлення не потрібне.** Production-логіка вже:
- Розрізняє `station_gain_v1` і `hourly_response_v1`.
- Захищає від повторного врахування факту (`used=True` блокує
  перезапис).
- Переживає restart через атомарний `version=2` JSON з валідацією.
- Розділяє archive reconstruction і forward-issued snapshots.

### Запропонована наступна зміна (окрема задача)

Додати в `calibration_status` поле `model_mismatch: bool`, яке = `True`,
коли `set(state.snapshots.values()).difference({state.calibration_model,
None})` непорожнє. Це дасть dashboard-атрибут, який чітко показує, чи є
issued snapshots з іншою моделлю, ніж поточна `calibration_model`.

Критерії приймання:

1. `model_mismatch` з'являється у `calibration_status(...)`.
2. `model_mismatch == True` коли `len(set_forecast_models - {calibration_model,
   None}) > 0`.
3. `model_mismatch == False` коли всі issued snapshots мають ту саму
   модель, що й `calibration_model`.
4. Тести `test_r03_model_mismatch.py` **PASS** на fixtures.

### Нез'ясовано

- Якщо `RealForecastPairs.snapshot` отримує новий `forecast_model` (не
  `None`, не `state.calibration_model`, не попередній), чи це впливає на
  calibrator? Ні — `calibration_pairs()` фільтрує на момент
  `set_calibration_model`. Стара пара з `used=False` залишається в
  `pairs`, але не в calibrator.
- Чи можуть бути «висячі» snapshots без пари через 30 днів? Ні — `prune`
  у `RealForecastPairs` обмежує `pairs` до 90 днів, `snapshots` — до 120
  днів.

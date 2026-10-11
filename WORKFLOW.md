# Workflow для розробки плагіна powmr_inverter

## Гілки

- **`main`** — стабільна, звідси йдуть релізи в HACS
- **`develop`** — поточна розробка. Версія тут явно синхронізована з `manifest.json` і root `__init__.py`; автоматичного `-dev` суфікса немає. Поточна версія — **1.9.0**.

## Щоденна робота

```bash
# Перейти на develop, оновити з main
git checkout develop
git pull --rebase origin main

# Створити feature-гілку
git checkout -b feat/new-sensor

# ... редагувати код ...

# Коли готово:
git add .
git commit -m "feat: add new sensor"
git push origin feature/or-fix

# Створити PR на GitHub → merge в develop
```

## Релізний цикл

Release tag і GitHub Release публікуються **вручну Юрою після review**, не
автоматично і не через pre-push hook. Корінь цього репозиторію є коренем
інтеграції: файл версії — `__init__.py` у корені, а не
`custom_components/powmr_inverter/__init__.py`. Поточні `__version__` і
`manifest.json:version` — `1.9.0`.

```bash
# 1. Переконатись, що все на develop протестовано
git checkout develop
python tests/run_all.py

# 2. Перевірити узгодженість версії (root __version__ ↔ manifest.json)
#    — ручна правка обох файлів перед публікацією release tag.
#    Поточна: 1.9.0 (див. manifest.json: "version": "1.9.0"
#    і __init__.py: __version__ = "1.9.0").
grep -n '__version__' __init__.py
python3 -c 'import json; print(json.load(open("manifest.json"))["version"])'

# 3. Push develop (release НЕ створюється автоматично)
git push origin develop

# 4. Release tag (виконує Юра вручну після review)
git tag v1.9.0
git push origin v1.9.0
gh release create v1.9.0 --generate-notes
```

**Без автоматичного bump_version.sh**: скрипт не існує в репозиторії.
Версія редагується руками в `manifest.json` і `__init__.py` в одному
коміті перед публікацією release tag.

## Локальні Git hooks

| Hook | Коли | Що перевіряє |
|---|---|---|
| **pre-commit** | `git commit` | Якщо hook встановлений: Python syntax для staged `.py`, JSON-перевірка зміненого manifest, грубий секрет-скан Python-файлів; можливе попередження про версію відносно останнього tag. |
| **pre-push** | `git push` | У локальному hook `pytest` запускається лише якщо встановлений; за його відсутності hook попереджає і пропускає тести. Також є force-push guard і branch warning. Це не замінює `tests/run_all.py`. |
| **post-commit** | `git commit` (після) | У локальному hook HA reload виконується лише якщо задані `HASS_URL` і `HASS_TOKEN`; інакше пропускається. Не задавайте їх для роботи без змін HA. |

Не обходьте hooks у звичайному робочому процесі. Перед відправленням змін
запускайте канонічний `tests/run_all.py` вручну, навіть якщо локальний
pre-push hook пропустив перевірку через відсутній `pytest`.

## Змінні середовища для post-commit hook

```bash
# Додати в /etc/profile.d/git-hooks.sh:
export HASS_URL="http://192.168.1.220:8123"
export HASS_TOKEN="***"
```

## Rollback

```bash
# Подивитись історію
git log --oneline

# Повернутись на попередню версію (тимчасово)
git checkout v1.8.11

# Або скасувати останній push (безпечний спосіб)
git revert HEAD
git push origin main
```

## Тестування

Audit T27: єдиний runner для всіх Python + JS suites має перевірки
Windows-шляху та ізоляції. Ці контрактні тести не є доказом виконання на
реальній Windows-системі; фіксуйте фактичну платформу окремо. Будь-який
відсутній runner або залежність завершує роботу з ненульовим exit code;
нуль тестів не рахується як pass.

```bash
# Повний прогін: всі Python suites + JS suite
python tests/run_all.py

# Тільки Python
python tests/run_all.py --python-only

# Тільки JS
python tests/run_all.py --js-only

# Конкретний файл
python tests/run_all.py --only test_t18_debug_logging_threadsafe.py

# JSON summary для CI
python tests/run_all.py --json

# Інший каталог
python tests/run_all.py --dir path/to/tests
```

Exit codes:

| Код | Значення |
|---|---|
| `0` | всі suites пройшли |
| `1` | один або більше suites failed |
| `2` | відсутній Python/Node interpreter |
| `3` | zero test files discovered (audit T27 forbids treating zero tests as a pass) |

`tests/test_t27_runner_contract.py` pins the runner contract:

  * `test_t27_03_runner_exit_code_on_failing_child` — створює
    синтетичний `test_T27_synthetic_failing.py` з `self.fail()` і
    перевіряє, що runner повертає non-zero з його назвою у виводі.
  * `test_t27_04_runner_clear_error_for_missing_python` — через
    `T27_FORCE_NO_PYTHON=1` змушує runner обрати гілку відсутнього
    Python і перевіряє, що exit code 2 та повідомлення містить
    "python" / "interpreter".
  * `test_t27_06_zero_tests_is_not_a_pass` — запускає runner проти
    порожнього каталогу і перевіряє, що exit 3, не 0.

Використання в pre-commit / pre-push hook:

```bash
# Pre-push: запустити runner і відмовитись пушити якщо fail
python tests/run_all.py --python-only --json || {
    echo "Test runner failed; refusing push" >&2
    exit 1
}
```

Або класичний pytest (suite-рівень):

```bash
pytest tests/
python3 -c "import json; json.load(open('manifest.json'))"
```

## Шпаргалка

```bash
# Стан
git status
git log --oneline -10

# Різниця з останнім комітом
git diff HEAD

# Синхронізація
git fetch origin
git pull --rebase origin main
```
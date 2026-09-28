# Workflow для розробки плагіна powmr_inverter

## Гілки

- **`main`** — стабільна, звідси йдуть релізи в HACS
- **`develop`** — поточна розробка (тут версія з `-dev` суфіксом)

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
git push origin feat/new-sensor

# Створити PR на GitHub → merge в develop
```

## Релізний цикл

```bash
# 1. Переконатись, що все на develop протестовано
git checkout develop
pytest tests/

# 2. Merge develop в main
git checkout main
git merge --no-ff develop

# 3. Оновити версію (з -dev на релізну)
./bump_version.sh 1.9.0
git add manifest.json
git commit -m "chore: bump version to v1.9.0"
git tag v1.9.0

# 4. Push (pre-push hook запустить тести)
git push origin main --tags

# 5. GitHub Releases (опційно, для HACS metadata)
gh release create v1.9.0 --generate-notes
```

## Правила (enforced by hooks)

| Hook | Коли | Що перевіряє |
|---|---|---|
| **pre-commit** | `git commit` | Python syntax, manifest.json JSON, секрети, version drift |
| **pre-push** | `git push` | pytest, force-push protection, branch warning |
| **post-commit** | `git commit` (після) | reload HA integration (якщо є HASS_TOKEN) |

Bypass: `git commit --no-verify` або `git push --no-verify`

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

## Шпаргалка

```bash
# Стан
git status
git log --oneline -10

# Різниця з останнім комітом
git diff HEAD

# Тестування
pytest tests/
python3 -c "import json; json.load(open('manifest.json'))"

# Синхронізація
git fetch origin
git pull --rebase origin main
```

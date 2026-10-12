# Перевірка плагіна «Terminal & SSH» на «Insecure secrets detected»

**Мета:** визначити, яке саме поле в options спричиняє попередження
"Insecure secrets detected" — **не виводячи значення секрету**.

> Документ готує оператора до offline-діагностики на його власній
> Home Assistant-системі. Жоден скрипт не зчитує значення секретів;
> все, що ви бачите в цій інструкції, обмежене іменами полів та їх
> типами.

## Що таке «Insecure secrets detected» насправді

Supervisor перевіряє `options.json` плагіна **проти його схеми в
`config.yaml`**. Поле вважається секретом, якщо:

1. Його ім'я збігається з відомим секретним полем схеми
   (`password`, `authorized_keys`, `ssh_keys`, `password_hash` для
   core_ssh), **або**
2. Його тип в схемі — `password` / `match` з regex, або
   воно оголошене з `format: password` / `format: ssh_known_hosts`.

Попередження з'являється, коли **значення поля невалідне для
схеми** (наприклад, `password` коротший за 4 символи, або
`authorized_keys` не починається з `ssh-rsa`/`ssh-ed25519`/…).
Supervisor НЕ класифікує поля за словниковим збігом
`password|token|key` — це поширена помилка, якої не існує в
жодній версії Supervisor.

> 🚫 **Заборонено** передавати токени або паролі в чат, Git чи
> лог. Скрипти нижче друкують тільки імена полів та їх типи.

## Реальний slug і схема core_ssh

Офіційна документація:
- https://github.com/home-assistant/addons/blob/master/ssh/DOCS.md
- https://github.com/home-assistant/addons/blob/master/ssh/config.yaml

Slug додатка на HAOS — `core_ssh` (або `a0d7b954_ssh` в
деяких інсталяціях). Спільнотний slug `a0d7b954-terminal` —
це **community** додаток, не те, що йде з HAOS.

```bash
SLUG=core_ssh
# Перевірити, що add-on встановлений:
ha addons info $SLUG
```

Схема `config.yaml` (джерело: GitHub home-assistant/addons):

```yaml
options:
  password: password
  authorized_keys:
    - "ssh-rsa ABC... user@host"
    - "ssh-ed25519 ABC... user@host"
  ssh_keys:
    - "ssh-rsa ..."
  username: root
  host_network: false
  init_commands: []
  debug: false
  log_level: info
  password_hash: "$y$j9T$..."   # bcrypt, optional
```

Тільки ці поля схеми класифікуються Supervisor як secret:
`password`, `authorized_keys`, `ssh_keys`, `password_hash`.

---

## Шлях A — через Supervisor API (рекомендовано)

Supervisor API доступний з host-системи HA через unix-сокет або
через `http://supervisor:80` зсередини мережі HA.

```bash
# Потрібен Supervisor token. Він зберігається на host-системі HA у
# /usr/share/hassio/supervisor/socket і НЕ доступний напряму.
# Найпростіше — використати Home Assistant CLI:
ha addons info $SLUG --raw-json | jq '.data.options | to_entries | map({key, type, secure: true})'
```

або якщо у вас є Supervisor token з оточення:

```bash
AUTH="Authorization: Bearer $SUPERVISOR_TOKEN"
curl -sS -H "$AUTH" "http://supervisor/addons/$SLUG/info" \
  | jq '.data.options
        | to_entries
        | map({
            key: .key,
            type: (.value | type),
            secure: (.key | test("^(password|ssh_keys|authorized_keys|password_hash)$"))
          })
        | map(select(.secure == true))'
```

Очікуваний вивід — масив обʼєктів виду:

```json
[
  { "key": "password", "type": "string", "secure": true },
  { "key": "authorized_keys", "type": "array", "secure": true }
]
```

Якщо масив порожній — у options немає секретних полів, які
Supervisor класифікує як «insecure». Переходьте до кроку 3.

## Шлях B — на host-системі напряму

Якщо Supervisor API недоступний (наприклад, ви не маєте токена),
виконайте ті ж самі перевірки безпосередньо на host HA:

```bash
sudo jq '
  .options
  | to_entries
  | map({
      key: .key,
      type: (.value | type),
      secure: (.key | test("^(password|ssh_keys|authorized_keys|password_hash)$"))
    })
  | map(select(.secure == true))
' /usr/share/hassio/addons/data/$SLUG/options.json
```

## Крок 3 — Перевірити валідність полів

Supervisor позначає поле як insecure, якщо його значення **не
відповідає схемі** — наприклад:

| Поле             | Невалідне значення, що тригерить warning                  |
| ---------------- | --------------------------------------------------------- |
| `password`       | довжина `< 4` (занадто короткий)                          |
| `password_hash`  | невалідний bcrypt-формат                                  |
| `authorized_keys` | рядок не починається з `ssh-rsa`/`ssh-ed25519`/`ecdsa-` |
| `ssh_keys`       | масив без жодного валідного публічного ключа              |
| `username`       | некоректний shell-username (порожній, root з обмеженнями) |

```bash
# Перевірити authorized_keys (виводимо тільки перші 5 символів ключа)
jq -r '.authorized_keys[]? | .[0:5] + "..."' \
   /usr/share/hassio/addons/data/$SLUG/options.json

# Перевірити, чи password довший за 4 символи (НЕ виводить значення)
jq -r '.password | if length >= 4 then "OK (length=" + (.|tostring|length|tostring) + ")"
        else "INVALID: password too short" end' \
   /usr/share/hassio/addons/data/$SLUG/options.json
```

> `jq` тут НЕ виводить саме значення `password` — тільки довжину.

## Крок 4 — Підтвердження, що попередження зникло

Після **видалення невалідного секрету** або **переходу на SSH-ключі**
(див. `rotate_terminal_ssh_password.md`):

1. Перейдіть у **Settings → System → Supervisor → ⋮ → Check
   configuration**.
2. Якщо все чисто — попередження зникає зі списку.
3. Якщо ні — поверніться до кроку 3 і перегляньте, чи не
   залишилось поле, яке schema Supervisor класифікує як
   secret з невалідним значенням.

> ⚠ **Не робіть ротацію**, доки не буде підтвердженого
> альтернативного доступу SSH-ключем — інакше ви ризикуєте
> втратити доступ до host HA.

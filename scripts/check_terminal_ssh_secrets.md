# Перевірка плагіна «Terminal & SSH» на «Insecure secrets detected»

**Мета:** визначити, яке саме поле в options спричиняє попередження
"Insecure secrets detected" — **не виводячи значення секрету**.

> Sandbox без `HASS_URL` / `HASS_TOKEN` — тому цей документ готує Юру до
> offline-діагностики на його власній Home Assistant-системі.
> Жоден скрипт не зчитує значення секретів; все, що ви бачите в цій
> інструкції, обмежене іменами полів та їх типами.

## Передумови

- SSH-доступ до host-системи Home Assistant (cli add-on або host shell).
- На host: `curl`, `jq`.
- Якщо налаштовано Supervisor API token — бережіть його так само, як
  описано нижче для `Authorization: Bearer …`.

> 🚫 **Заборонено** передавати токени або паролі в чат, Git чи лог.
> Скрипти нижче друкують тільки `<SET:length>` / `<REDACTED>` замість
> значення.

---

## Шлях A — через Supervisor API (рекомендовано)

Supervisor API доступний всередині мережі HA на порту `80`
(`http://supervisor/...`) і на host-системі через `unix:///var/run/docker.sock`
або `http://supervisor:80`.

### 1. Отримати slug додатка

```bash
# slug = a0d7b954-terminal  (стабільний ідентифікатор Terminal & SSH)
SLUG=a0d7b954-terminal
```

Якщо ви не впевнені в slug — знайдіть його в
`/usr/share/hassio/addons/.storage/registered` або
через `GET /addons`.

### 2. Витягти options без значень секретів

```bash
AUTH='Authorization: Bearer ${SUPERVISOR_TOKEN}'   # NEVER log the value
BASE='http://supervisor/addons'                    # or http://homeassistant.local:80/api/hassio/addons
curl -sS -H "$AUTH" "$BASE/$SLUG/info" \
  | jq '
      .data.options
      | to_entries
      | map(
          select(
            (.value | type == "string")
            and (.key | test("(?i)(password|token|key|pass|secret|auth)"))
          )
          | {
              key: .key,
              type: (.value | type),
              length: (.value | length),
              sample: "<REDACTED>"
            }
        )
    '
```

Очікуваний вивід — масив обʼєктів виду:

```json
[
  { "key": "password", "type": "string", "length": 12, "sample": "<REDACTED>" },
  { "key": "ssh_keys", "type": "array",  "length": 0, "sample": "<REDACTED>" }
]
```

Якщо масив порожній — у options немає секретів, яких Supervisor класифікує
як «insecure». Переходьте до кроку 3.

### 3. Перевірити, що поле справді вважається insecure

Supervisor позначає поле як insecure, якщо:

* його імʼя відповідає регекспу `(password|token|key|pass|secret|...)`
  **або** воно знаходиться в secrets-розділі schema add-on.
* його значення невалідне: це plaintext-пароль, відсутній публічний ключ
  SSH, або файл дозволів містить `0o600` / `0o644` без
  `encrypted_at_rest`.

```bash
curl -sS -H "$AUTH" "$BASE/$SLUG/info" \
  | jq '.data.schema | to_entries
        | map(select(
            (.value.format? // "") | test("(?i)password|secret|token|key")
          ))
        | map({key: .key, format: .value.format, description: .value.description})
        '
```

Це показує, які поля **схеми** Supervisor вважає секретними. Якщо ваш
options має ключ із цього списку — це джерело попередження.

### 4. (Опціонально) показати permissions / owners без значення

```bash
ls -l /usr/share/hassio/addons/data/$SLUG/options.json
stat  /usr/share/hassio/addons/data/$SLUG/options.json | head -10
# confirm: file mode ≤ 0o600, owned by root:root
```

---

## Шлях B — на host-системі напряму

Якщо Supervisor API недоступний (наприклад, ви не маєте токена),
виконайте ті ж самі перевірки безпосередньо на host HA:

### B1. Зчитати файл options як root

```bash
sudo cat /usr/share/hassio/addons/data/a0d7b954-terminal/options.json \
  | jq 'to_entries
        | map(
            select(
              (.key | test("(?i)(password|token|key|pass|secret|auth)"))
            )
            | {key: .key, length: ((.value | tostring) | length), sample: "<REDACTED>"}
          )'
```

### B2. Зчитати `config.yaml` (schema)

```bash
sudo jq '
  .options
  | to_entries
  | map(select((.value.format? // "") | test("(?i)password|secret|token|key")))
' /usr/share/hassio/addons/a0d7b954-terminal/config.yaml
```

### B3. Перевірити supervisor_log на «Insecure secret»

```bash
journalctl -u hassio-supervisor.service --since "1 hour ago" \
  | grep -i "insecure secret" \
  | sed -E 's/(password|token|secret|key)[^"]*"[^"]+"/\1 "<REDACTED>"/g'
```

> `sed` замінює будь-яке значення, яке слідує за словом secret, на
> `<REDACTED>`. Якщо ваш sed знає коротші форми — замініть вираз.

---

## Чек-ліст «де саме insecure»

| Категорія               | Імʼя ключа (типово)        | Що вважається insecure                 |
| ----------------------- | -------------------------- | -------------------------------------- |
| `password`              | plaintext-пароль           | будь-який, довший за 4 символи         |
| `authorized_keys`       | рядок, перекладений як ключ | невалідний SSH public key              |
| `ssh_keys`              | масив ключів               | жоден не починається з `ssh-rsa/ssh-ed25519` |
| `username`              | користувач                 | некоректний shell-username             |
| `webui_password`        | пароль для UI              | plaintext, немає `password_hash`       |
| `ssl`                   | SSL ключі                  | файл не `0o600` або відсутній `cert`   |

> Перегляньте ваш `options.json` і поставте мітки проти кожного з цих
> полів. Те, що несекретне (наприклад, `host_network: true`), не
> тригерить попередження.

---

## Підтвердження, що попередження пішло

Після **видалення plaintext-секрету** або **переходу на SSH-ключі** (див.
`rotate_terminal_ssh_password.md`):

1. Перейдіть у **Settings → System → Supervisor → ⋮ → Check configuration**.
2. Якщо все чисто — попередження зникає зі списку.
3. Якщо ні — поверніться до кроку A3 і перегляньте, чи не залишилось
   поле, яке schema Supervisor класифікує як secret.

> ⚠ **Не робіть ротацію**, доки не буде підтвердженого альтернативного
> доступу SSH-ключем — інакше ви ризикуєте втратити доступ до host HA.

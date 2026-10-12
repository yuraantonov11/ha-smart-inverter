# Ротація пароля додатка «Terminal & SSH» на SSH-ключі

**Мета:** замінити plaintext-пароль на SSH public-key авторизацію, щоб
прибрати попередження **«Insecure secrets detected»** в Supervisor.

> ⚠ Цей документ — ДОКУМЕНТАЦІЯ, а не сценарій автоматичного виконання.
> Жодна команда не виконується в sandbox без `HASS_URL` / `HASS_TOKEN`.
> Юра виконує кроки локально і руками.

> 🚫 **Заборонено** передавати паролі, токени чи приватні ключі в чат,
> Git чи лог. Публічний ключ (`ssh-…` без чутливих речей) — ОК.

---

## Передумови

- Документ `check_terminal_ssh_secrets.md` опрацьовано: ви знаєте,
  яке саме поле тригерить попередження (типово `password`).
- Є **альтернативний доступ** до host HA (KVM, фізична консоль,
  debug-TTY, або інший робочий SSH-ключ).
- На робочій станції — `ssh-keygen` + `ssh` + копія `ssh-copy-id`.

---

## Крок 1 — Підготувати новий ключ

> Робоча станція (НЕ host HA).

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ha_recovery -C "ha-recovery-2026-10"
chmod 600 ~/.ssh/ha_recovery
ssh-keygen -y -f ~/.ssh/ha_recovery > ~/.ssh/ha_recovery.pub
```

`~/.ssh/ha_recovery.pub` — це те, що ми додамо в Terminal & SSH.

> 🚫 НІКОЛИ не передавайте приватний ключ (`~/.ssh/ha_recovery`) в
> чат / Git / логи.

---

## Крок 2 — Додати публічний ключ у Terminal & SSH

### Шлях A: через UI (рекомендовано)

1. Відкрийте **Settings → Add-ons → Terminal & SSH**.
2. Вкладка **Configuration**.
3. Знайдіть поле **`authorized_keys`** (або `ssh_keys`, залежно від
   версії). Вставте повний вміст `~/.ssh/ha_recovery.pub`:

   ```
   ssh-ed25519 AAAA…long-key-data… ha-recovery-2026-10
   ```

4. **НЕ чіпайте** поле `password` поки що — воно нам потрібне як
   fallback.
5. **Save** → add-on перезапуститься автоматично.

### Шлях B: напряму в options.json на host HA

```bash
sudo install -d -m 0700 /usr/share/hassio/addons/data/a0d7b954-terminal
sudo cp ~/.ssh/ha_recovery.pub /tmp/ak
sudo jq --arg ak "$(cat /tmp/ak)" '.authorized_keys = [$ak]' \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json \
  > /tmp/options.new.json
sudo mv /tmp/options.new.json \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
sudo chmod 0600 /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
```

> `sudo jq` з `--arg` не зберігає власне значення ключа в bash history;
> перевірте `~/.bash_history` на вашій робочій станції й видаліть
> будь-які сліди, якщо вони туди потрапили.

---

## Крок 3 — Перевірити SSH-доступ ПЕРЕД ротацією

> Підтвердіть, що можете увійти з новим ключем.

```bash
ssh -i ~/.ssh/ha_recovery -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    root@homeassistant.local
```

Або на host HA (зсередини Terminal & SSH):

```bash
mkdir -p ~/.ssh && chmod 0700 ~/.ssh
if ! grep -q "$(awk '{print $1" "$2}' ~/.ssh/ha_recovery.pub)" ~/.ssh/authorized_keys 2>/dev/null; then
    cat ~/.ssh/ha_recovery.pub >> ~/.ssh/authorized_keys
fi
chmod 0600 ~/.ssh/authorized_keys
```

### Контрольна перевірка

- ✅ Ви входите з робочої станції `ssh -i ~/.ssh/ha_recovery …`.
- ✅ Ви входите з іншого терміналу на host HA.
- ❌ **Не** переходьте до кроку 4, доки обидва ці тести не пройшли.

---

## Крок 4 — Видалити plaintext-пароль

> Тільки ПІСЛЯ успішного кроку 3.

### Через UI

1. **Settings → Add-ons → Terminal & SSH → Configuration**.
2. Видаліть поле `password` (або залиште його порожнім).
3. **Save** → ще один автоматичний перезапуск add-on.

### Через host (CLI)

```bash
sudo jq 'del(.password)' \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json \
  > /tmp/options.new.json
sudo mv /tmp/options.new.json \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
sudo chmod 0600 /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
```

Перезапустіть add-on:

```bash
sudo docker restart $(sudo docker ps -f name=a0d7b954-terminal -q)
```

---

## Крок 5 — Підтвердити зникнення попередження

1. **Settings → System → Supervisor → ⋮ → Check configuration**.
2. Очікуваний результат: «Configuration is valid» без записів у блоці
   «Security» / «Issues».
3. Якщо попередження залишилося:
   - перегляньте `check_terminal_ssh_secrets.md` крок A3 — можливо,
     інше поле схеми класифікується як секрет;
   - перевірте, чи не зʼявився новий plaintext `init_commands` або
     `env_vars` із токеном.

---

## Відкат (якщо щось пішло не так)

```bash
# На host HA, від імені root:
sudo jq '.password = "TEMP_PASSWORD_VALUE"' \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json \
  > /tmp/options.rollback.json
sudo mv /tmp/options.rollback.json \
  /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
sudo chmod 0600 /usr/share/hassio/addons/data/a0d7b954-terminal/options.json
sudo docker restart $(sudo docker ps -f name=a0d7b954-terminal -q)
```

> 🚫 **Не** зберігайте `TEMP_PASSWORD_VALUE` в історії bash чи в
> текстових файлах — використовуйте `read -s` і одразу чистіть
> змінну.

---

## Контрольний список (для commit/PR-опису)

- [ ] Поле `password` або видалено, або має довжину `< 4` (HA
      вважає короткі тестові рядки «warn-only»).
- [ ] `authorized_keys` містить **тільки публічні** ключі з
      правильним форматом (`ssh-ed25519 AAAA…`).
- [ ] `~/.ssh/authorized_keys` на host HA має `chmod 0600`.
- [ ] Supervisor → Check configuration повертає «Configuration is valid».
- [ ] Жоден plaintext secret не потрапив у `options.json` чи в логи.

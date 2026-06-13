# ============================================================================
# Home Assistant Deployment Guide — Smart Solar Inverter
# ============================================================================
# Інструкція з розгортання powmr_inverter custom integration
# на Home Assistant, що працює на Synology NAS (Docker).
# ============================================================================

## Передумовиgc $env:USERPROFILE\.ssh\synology_ha.pub | ssh yuraantonov@192.168.1.222 "mkdir -p ~/.ssh && cat > ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

### 1. Увімкнути SSH на Synology
- DSM → Control Panel → Terminal & SNMP → Enable SSH service → Port 22
- Переконайся, що твій користувач має права адміністратора

### 2. Знайти шлях до конфігурації HA
Варіанти:
- **Docker (типово):** `/volume1/docker/homeassistant/config`
- **Container Manager (DSM 7.2+):** `/volume1/@appstore/ContainerManager/...`
- **Визначити через SSH:**
  ```bash
  ssh admin@your-nas-ip
  sudo docker inspect homeassistant | grep -A5 Mounts
  # або
  sudo docker inspect homeassistant --format='{{range .Mounts}}{{.Destination}} {{end}}'
  ```

Типовий шлях: `/volume1/docker/homeassistant`

### 3. Налаштувати SSH-ключі (опціонально, для безпарольного деплою)

На Windows (PowerShell):
```powershell
ssh-keygen -t ed25519 -f ~/.ssh/synology_ha
type ~\.ssh\synology_ha.pub | ssh admin@YOUR_NAS_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Додати в `~/.ssh/config`:
```
Host synology-ha
    HostName YOUR_NAS_IP
    User admin
    IdentityFile ~/.ssh/synology_ha
```

---

## Способи деплою

### Спосіб 1: PowerShell скрипт (рекомендовано)

```powershell
# Перший запуск — налаштування
.\scripts\deploy_ha.ps1 -Setup

# Деплой
.\scripts\deploy_ha.ps1

# Деплой + перезавантаження HA
.\scripts\deploy_ha.ps1 -Restart

# Форсувати деплой (без підтвердження)
.\scripts\deploy_ha.ps1 -Force

# Авто-деплой при зміні файлів
.\scripts\watch_ha.ps1
```

### Спосіб 2: SMB-шар (простий, без SSH)

1. На Synology: Control Panel → File Services → SMB → Enable SMB
2. На Windows: `\\YOUR_NAS_IP\docker\homeassistant\config\custom_components\powmr_inverter\`
3. Копіювати файли вручну або через robocopy:
   ```powershell
   robocopy custom_components\powmr_inverter \\YOUR_NAS_IP\docker\homeassistant\config\custom_components\powmr_inverter /MIR /NJH /NJS
   ```

### Спосіб 3: Git Pull на NAS

```bash
ssh admin@YOUR_NAS_IP
cd /volume1/docker/homeassistant/config/custom_components/powmr_inverter
git pull
```

### Спосіб 4: VS Code Remote SSH

1. Встанови розширення "Remote - SSH" у VS Code
2. Підключись до `admin@YOUR_NAS_IP`
3. Відкрий теку `/volume1/docker/homeassistant/config`
4. Редагуй файли безпосередньо на NAS

---

## Швидкий старт

```powershell
# 1. Налаштуй змінні в скрипті (один раз)
.\scripts\deploy_ha.ps1 -Setup

# 2. Запусти авто-деплой (буде слідкувати за змінами)
.\scripts\watch_ha.ps1

# 3. В іншому терміналі — дивись логи HA:
ssh admin@YOUR_NAS_IP "sudo docker logs -f homeassistant | grep powmr"
```

---

## Перевірка деплою

Після деплою перевір логи HA:
```bash
ssh admin@YOUR_NAS_IP "sudo docker logs homeassistant --tail 50"
```

Або через HA UI:
- Settings → System → Logs
- Фільтр: `powmr_inverter`

---

## Підключення PowMr dashboard (YAML)

Файл dashboard уже лежить у:

- `config/dashboards/powmr_dashboard.yaml`

Щоб підключити його в Home Assistant UI:

1. Settings → Dashboards → Add Dashboard
2. Type: `YAML`
3. Title: `PowMr Energy Hub`
4. URL path: `powmr-energy`
5. YAML file: `dashboards/powmr_dashboard.yaml`

Після збереження dashboard з'явиться в лівому меню.

---

## Типові проблеми

| Проблема | Рішення |
|----------|---------|
| `Permission denied (publickey)` | Перевір ключ: `ssh admin@NAS_IP` |
| `No such file or directory` | Перевір шлях до HA config: `sudo docker inspect homeassistant` |
| HA не бачить інтеграцію | Перевір, чи файли в `custom_components/powmr_inverter/`, а не в `custom_components/powmr_inverter/custom_components/...` |
| `ModuleNotFoundError: Crypto` | Зайди в контейнер: `sudo docker exec -it homeassistant pip install pycryptodome` |
| Docker немає `scp`/`rsync` | Використай SMB-шар або `docker cp` |

## Важливо для HA automation + `select`

- Для `select`-сутностей у Home Assistant поточне значення потрібно читати через `states('select.entity_id')`.
- Не використовуй `state_attr('select.entity_id', 'option')` для перевірки активної опції: зазвичай цей атрибут відсутній.
- Якщо порівнюєш пріоритети інвертора, нормалізуй значення (`USB`, `SBU`, `SNU`, `OSO`), бо інтеграція може повертати як короткі коди, так і розгорнуті лейбли.

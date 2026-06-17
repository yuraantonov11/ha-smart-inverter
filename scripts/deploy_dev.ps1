# ============================================================================
# Dev Deploy Script — Smart Solar Inverter
# ============================================================================
# Сценарій для швидкого деплою розробки на локальний Home Assistant
# без викочування релізу на GitHub.
#
# Використання:
#   .\scripts\deploy_dev.ps1                    # деплой + перезапуск
#   .\scripts\deploy_dev.ps1 -DryRun            # подивитись що буде
#   .\scripts\deploy_dev.ps1 -SkipRestart       # без перезапуску HA
#   .\scripts\deploy_dev.ps1 -Setup             # налаштувати SSH/SMB
# ============================================================================

param(
    [switch]$Setup,
    [switch]$DryRun,
    [switch]$SkipRestart,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path "$ScriptDir\.."
$ComponentName = "powmr_inverter"
$ComponentPath = "$RepoRoot\custom_components\$ComponentName"

# ── Config file ──────────────────────────────────────────────────────
$ConfigFile = "$RepoRoot\.deploy_config.json"

$defaultConfig = @{
    method  = "ssh"          # ssh або smb
    host    = "192.168.1.222"
    user    = "admin"
    ha_path = "/volume1/docker/homeassistant/config"
}

$config = $defaultConfig.Clone()

if (Test-Path $ConfigFile) {
    try {
        $saved = Get-Content $ConfigFile | ConvertFrom-Json
        foreach ($key in $config.Keys) {
            if ($saved.$key) { $config[$key] = $saved.$key }
        }
    } catch {
        Write-Warning "Не вдалось прочитати $ConfigFile, використовуються налаштування за замовчуванням."
    }
}

# ── Setup wizard ─────────────────────────────────────────────────────
if ($Setup) {
    Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║         Dev Deploy — налаштування        ║" -ForegroundColor Cyan
    Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan

    $h = $config.host
    $u = $config.user
    $p = $config.ha_path

    $config.host = Read-Host "IP або хост Synology NAS [$h]"
    if ([string]::IsNullOrWhiteSpace($config.host)) { $config.host = $h }

    $config.user = Read-Host "SSH користувач [$u]"
    if ([string]::IsNullOrWhiteSpace($config.user)) { $config.user = $u }

    $config.ha_path = Read-Host "Шлях до HA config на NAS [$p]"
    if ([string]::IsNullOrWhiteSpace($config.ha_path)) { $config.ha_path = $p }

    # Test SSH
    try {
        $test = ssh -o ConnectTimeout=5 -o BatchMode=yes "${config.user}@${config.host}" "echo OK" 2>&1
        if ($test -match "OK") {
            Write-Host "✅ SSH з'єднання працює" -ForegroundColor Green
        } else {
            Write-Host "❌ SSH не відповідає. Перевір: ssh ${config.user}@${config.host}" -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "❌ Помилка SSH: $_" -ForegroundColor Red
        exit 1
    }

    $config | ConvertTo-Json | Set-Content $ConfigFile
    Write-Host "✅ Налаштування збережено в $ConfigFile" -ForegroundColor Green
    exit 0
}

# ── Validation ───────────────────────────────────────────────────────
if (-not (Test-Path $ComponentPath)) {
    Write-Error "Немає $ComponentPath. Запускай скрипт з кореня репозиторію."
    exit 1
}

# ── Build exclusion list ─────────────────────────────────────────────
$ExcludeList = @(
    "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db",
    ".git", ".github", "tests", "test_*.py"
)

Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║      Dev Deploy — Smart Solar Inverter   ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host "Репозиторій: $RepoRoot" -ForegroundColor Gray
Write-Host "Компонент:    $ComponentName" -ForegroundColor Gray
Write-Host "Ціль:         ${config.user}@${config.host}:${config.ha_path}/custom_components/$ComponentName" -ForegroundColor Gray
Write-Host ""

# ── File list ────────────────────────────────────────────────────────
$Files = Get-ChildItem -Path $ComponentPath -Recurse -File | Where-Object {
    $exclude = $false
    foreach ($pattern in $ExcludeList) {
        if ($_.FullName -like "*\$pattern") { $exclude = $true; break }
    }
    -not $exclude
}

Write-Host "Знайдено $($Files.Count) файлів для деплою:" -ForegroundColor Yellow
foreach ($f in $Files) {
    $relPath = $f.FullName.Substring($ComponentPath.Length + 1)
    Write-Host "  📄 $relPath" -ForegroundColor Gray
}
Write-Host ""

if ($DryRun) {
    Write-Host "🔍 Dry-Run: нічого не скопійовано. Запусти без -DryRun для реального деплою." -ForegroundColor Yellow
    exit 0
}

# ── Confirmation ─────────────────────────────────────────────────────
if (-not $Force) {
    $answer = Read-Host "Деплоїти $($Files.Count) файлів на NAS? (y/N)"
    if ($answer -ne "y") { Write-Host "❌ Скасовано."; exit 1 }
}

# ── Deploy via SSH (rsync preferred, fallback to scp/tar) ───────────
$RemotePath = "${config.ha_path}/custom_components/$ComponentName"
$RemoteBak = "${config.ha_path}/custom_components/${ComponentName}_bak"

try {
    # 1. Backup поточного компонента на NAS
    Write-Host "📦 Створюю бекап поточної версії..." -ForegroundColor Cyan
    ssh -o BatchMode=yes "${config.user}@${config.host}" "rm -rf $RemoteBak && cp -r $RemotePath $RemoteBak" 2>&1 | Out-Null

    # 2. Copy через SSH (використовуємо tar piped через ssh для кращої швидкості)
    Write-Host "🚀 Копіюю $($Files.Count) файлів на NAS..." -ForegroundColor Cyan
    $timer = [System.Diagnostics.Stopwatch]::StartNew()

    tar -cf - -C "$ComponentPath" --exclude="__pycache__" --exclude="*.pyc" --exclude="tests" . |
        ssh -o BatchMode=yes "${config.user}@${config.host}" "tar -xf - -C $RemotePath" 2>&1

    if ($LASTEXITCODE -ne 0) { throw "tar/ssh failed with code $LASTEXITCODE" }

    $timer.Stop()
    Write-Host "✅ Скопійовано за $($timer.Elapsed.TotalSeconds.ToString('0.0'))с" -ForegroundColor Green

    # 3. Очистка кешу .pyc на NAS
    ssh -o BatchMode=yes "${config.user}@${config.host}" "find $RemotePath -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; find $RemotePath -name '*.pyc' -delete 2>/dev/null"
    Write-Host "🧹 Кеш очищено" -ForegroundColor Gray

    # 4. Перезапуск HA
    if (-not $SkipRestart) {
        Write-Host "🔄 Перезапускаю Home Assistant..." -ForegroundColor Cyan
        ssh -o BatchMode=yes "${config.user}@${config.host}" "docker restart homeassistant" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ HA перезапущено. Чекаю 30с на запуск..." -ForegroundColor Green
            Start-Sleep -Seconds 30
            Write-Host "✅ Готово. Перевір логи: ssh ${config.user}@${config.host} 'docker logs homeassistant --tail 50 | grep Inverter'" -ForegroundColor Green
        } else {
            Write-Warning "⚠️ Не вдалось перезапустити HA. Зроби це вручну."
        }
    } else {
        Write-Host "ℹ️ Перезапуск HA пропущено (параметр -SkipRestart)." -ForegroundColor Yellow
        Write-Host "   Перезапусти вручну: ssh ${config.user}@${config.host} 'docker restart homeassistant'" -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  Деплой завершено успішно!" -ForegroundColor Green
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Перевірка логів:" -ForegroundColor Gray
    Write-Host "  ssh ${config.user}@${config.host} 'docker logs homeassistant --tail 50 | grep -i inverter'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Якщо щось пішло не так, відкотити бекап:" -ForegroundColor Gray
    Write-Host "  ssh ${config.user}@${config.host} 'rm -rf $RemotePath && mv $RemoteBak $RemotePath && docker restart homeassistant'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Щоб налаштувати SSH-ключі:" -ForegroundColor Gray
    Write-Host "  ssh-keygen -t ed25519 -f ~/.ssh/ha_dev" -ForegroundColor Gray
    Write-Host '  type $env:USERPROFILE\.ssh\ha_dev.pub | ssh admin@192.168.1.222 "cat >> ~/.ssh/authorized_keys"' -ForegroundColor Gray
    Write-Host "  додай в ~/.ssh/config рядки Host=nas-ha, HostName=192.168.1.222, User=admin, IdentityFile=~/.ssh/ha_dev" -ForegroundColor Gray

} catch {
    Write-Host ""
    Write-Host "❌ ПОМИЛКА: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Спробуй відкотити бекап:" -ForegroundColor Yellow
    Write-Host "  ssh ${config.user}@${config.host} 'rm -rf $RemotePath && mv $RemoteBak $RemotePath && docker restart homeassistant'" -ForegroundColor Yellow
    exit 1
}

param(
    [switch]$SkipTests,
    [switch]$SkipAndroid,
    [switch]$SkipWindows,
    [switch]$SkipMsix,
    [switch]$SkipInno,
    [string]$InnoCompilerPath = "ISCC",
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host "Usage: .\\scripts\\build_release.ps1 [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -SkipTests        Skip 'flutter test'"
    Write-Host "  -SkipAndroid      Skip Android AAB/APK builds"
    Write-Host "  -SkipWindows      Skip Windows build"
    Write-Host "  -SkipMsix         Skip 'flutter pub run msix:create'"
    Write-Host "  -SkipInno         Skip Inno Setup EXE build"
    Write-Host "  -InnoCompilerPath Path or command for Inno Setup compiler (default: ISCC)"
    exit 0
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Assert-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host "==> $Label"
    & $Action
    if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
        throw "Step failed: $Label (exit code: $LASTEXITCODE)"
    }
}

Assert-CommandExists -Name "flutter"

if (-not $SkipInno -and -not $SkipWindows) {
    if (-not (Get-Command $InnoCompilerPath -ErrorAction SilentlyContinue)) {
        throw "Inno Setup compiler not found: $InnoCompilerPath. Use -SkipInno or pass -InnoCompilerPath."
    }
}

if (-not $SkipAndroid) {
    $keyPropsPath = Join-Path $repoRoot "android\key.properties"
    if (-not (Test-Path $keyPropsPath)) {
        throw "Missing android/key.properties. Create it from android/key.properties.example before Android release build."
    }
}

Invoke-Step -Label "flutter pub get" -Action { flutter pub get }

if (-not $SkipTests) {
    Invoke-Step -Label "flutter test" -Action { flutter test }
}

if (-not $SkipAndroid) {
    Invoke-Step -Label "flutter build appbundle --release" -Action { flutter build appbundle --release }
    Invoke-Step -Label "flutter build apk --release" -Action { flutter build apk --release }
}

if (-not $SkipWindows) {
    Invoke-Step -Label "flutter build windows --release" -Action { flutter build windows --release }

    if (-not $SkipMsix) {
        Invoke-Step -Label "flutter pub run msix:create" -Action { flutter pub run msix:create }
    }

    if (-not $SkipInno) {
        $issPath = Join-Path $repoRoot "windows\installer_script.iss"
        Invoke-Step -Label "Inno Setup build" -Action { & $InnoCompilerPath $issPath }
    }
}

Write-Host ""
Write-Host "Release build completed."
Write-Host "Check artifacts in:"
Write-Host "  - build/app/outputs/bundle/release"
Write-Host "  - build/app/outputs/flutter-apk"
Write-Host "  - build/windows/x64/runner/Release"
Write-Host "  - build/windows/x64/runner/Release (and Inno Setup output dir)"


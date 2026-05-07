param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $false)]
    [int]$Build = 0,

    [switch]$Commit,
    [switch]$Tag,
    [switch]$Push
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must be in semver format: 2.0.0"
}

$pubspecPath = Join-Path $repoRoot 'pubspec.yaml'
$issPath = Join-Path $repoRoot 'windows\installer_script.iss'

$pubspec = Get-Content $pubspecPath -Raw
$versionMatch = [regex]::Match($pubspec, 'version:\s*(\d+\.\d+\.\d+)\+(\d+)')
if (-not $versionMatch.Success) {
    throw "Could not read current version from pubspec.yaml"
}

$currentBuild = [int]$versionMatch.Groups[2].Value
if ($Build -le 0) {
    $Build = $currentBuild + 1
}

$fullVersion = "$Version+$Build"
$msixVersion = "$($Version.Replace('.', '.')).$Build"
$tagName = "v$fullVersion"

$pubspec = [regex]::Replace($pubspec, 'version:\s*\d+\.\d+\.\d+\+\d+', "version: $fullVersion")
$pubspec = [regex]::Replace($pubspec, 'msix_version:\s*\d+\.\d+\.\d+\.\d+', "msix_version: $($Version).$Build")
Set-Content -Path $pubspecPath -Value $pubspec -NoNewline

$iss = Get-Content $issPath -Raw
$iss = [regex]::Replace($iss, '#define AppVersion "\d+\.\d+\.\d+"', "#define AppVersion `"$Version`"")
Set-Content -Path $issPath -Value $iss -NoNewline

$notesPath = Join-Path $repoRoot "release_notes_$Version.md"
if (-not (Test-Path $notesPath)) {
@"
## Smart Inverter App v$Version

### Highlights
- Add release highlights here.

### Packaging
- App version: `$fullVersion`
- MSIX identity version: `$Version.$Build`

### Release artifacts
- `smart_inverter_setup_$Version.exe` (Windows installer)
- `smart_inverter_v$Version.msix` (Windows MSIX)
- `inverter_app_portable_$Version.zip` (Windows portable)
- `smart_inverter_android_v$Version.apk` (Android APK)
- `smart_inverter_android_v$Version.aab` (Android AAB)
- `SHA256SUMS.txt` (checksums)
"@ | Set-Content -Path $notesPath
}

Write-Host "Prepared release: $fullVersion"
Write-Host "Tag to use: $tagName"

if ($Commit) {
    git add pubspec.yaml windows/installer_script.iss $notesPath
    git commit -m "release: bump to $fullVersion"
}

if ($Tag) {
    git tag $tagName
}

if ($Push) {
    git push origin main
    if ($Tag) {
        git push origin $tagName
    }
}


param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $false)]
    [int]$Build = 0,

    [switch]$Push,
    [switch]$AllowDirty,
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
    Write-Host "Usage: .\\scripts\\release.ps1 -Version X.Y.Z [-Build N] [options]"
    Write-Host ""
    Write-Host "Canonical release flow: prepare -> build/test -> commit -> tag -> optional push"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Push            Push main and release tag after successful commit/tag"
    Write-Host "  -AllowDirty      Allow running with non-clean git tree (not recommended)"
    Write-Host "  -SkipTests       Pass through to build_release.ps1"
    Write-Host "  -SkipAndroid     Pass through to build_release.ps1"
    Write-Host "  -SkipWindows     Pass through to build_release.ps1"
    Write-Host "  -SkipMsix        Pass through to build_release.ps1"
    Write-Host "  -SkipInno        Pass through to build_release.ps1"
    Write-Host "  -InnoCompilerPath Pass through to build_release.ps1"
    exit 0
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must be in semver format: X.Y.Z"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

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

if (-not $AllowDirty) {
    $status = git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read git status"
    }
    if (-not [string]::IsNullOrWhiteSpace(($status | Out-String))) {
        throw "Working tree is not clean. Commit/stash changes first or rerun with -AllowDirty."
    }
}

$prepareScript = Join-Path $PSScriptRoot "prepare_release.ps1"
$buildScript = Join-Path $PSScriptRoot "build_release.ps1"

if (-not (Test-Path $prepareScript)) {
    throw "Missing helper script: $prepareScript"
}
if (-not (Test-Path $buildScript)) {
    throw "Missing helper script: $buildScript"
}

$prepareArgs = @{
    Version = $Version
}
if ($Build -gt 0) {
    $prepareArgs.Build = $Build
}

Invoke-Step -Label "Prepare release metadata" -Action {
    & $prepareScript @prepareArgs
}

$buildArgs = @{}
if ($SkipTests) { $buildArgs.SkipTests = $true }
if ($SkipAndroid) { $buildArgs.SkipAndroid = $true }
if ($SkipWindows) { $buildArgs.SkipWindows = $true }
if ($SkipMsix) { $buildArgs.SkipMsix = $true }
if ($SkipInno) { $buildArgs.SkipInno = $true }
if ($PSBoundParameters.ContainsKey("InnoCompilerPath")) {
    $buildArgs.InnoCompilerPath = $InnoCompilerPath
}

Invoke-Step -Label "Build and validate release" -Action {
    & $buildScript @buildArgs
}

$pubspecPath = Join-Path $repoRoot "pubspec.yaml"
$pubspec = Get-Content $pubspecPath -Raw
$match = [regex]::Match($pubspec, 'version:\s*(\d+\.\d+\.\d+)\+(\d+)')
if (-not $match.Success) {
    throw "Failed to parse final version from pubspec.yaml"
}

$resolvedVersion = $match.Groups[1].Value
$resolvedBuild = $match.Groups[2].Value
$fullVersion = "$resolvedVersion+$resolvedBuild"
$tagName = "v$fullVersion"
$notesPath = Join-Path $repoRoot "release_notes_$resolvedVersion.md"

if (-not (Test-Path $notesPath)) {
    throw "Missing release notes file: $notesPath"
}

$existingTag = git tag --list $tagName
if ($LASTEXITCODE -ne 0) {
    throw "Unable to check existing tags"
}
if (-not [string]::IsNullOrWhiteSpace(($existingTag | Out-String))) {
    throw "Tag already exists: $tagName"
}

Invoke-Step -Label "Commit release metadata" -Action {
    git add pubspec.yaml windows/installer_script.iss $notesPath
    git commit -m "release: bump to $fullVersion"
}

Invoke-Step -Label "Create annotated tag $tagName" -Action {
    git tag -a $tagName -m "Release $tagName"
}

if ($Push) {
    Invoke-Step -Label "Push main" -Action { git push origin main }
    Invoke-Step -Label "Push tag $tagName" -Action { git push origin $tagName }
}

Write-Host ""
Write-Host "Release flow completed successfully."
Write-Host "Version: $fullVersion"
Write-Host "Tag: $tagName"
if (-not $Push) {
    Write-Host "Push was skipped. To publish now, run:"
    Write-Host "  git push origin main"
    Write-Host "  git push origin $tagName"
}


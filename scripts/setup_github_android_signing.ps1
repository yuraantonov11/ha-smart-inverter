param(
    [Parameter(Mandatory = $false)]
    [string]$KeystorePath,

    [Parameter(Mandatory = $false)]
    [string]$StorePassword,

    [Parameter(Mandatory = $false)]
    [string]$KeyAlias,

    [Parameter(Mandatory = $false)]
    [string]$KeyPassword,

    [Parameter(Mandatory = $false)]
    [string]$Repo,

    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host "Usage: .\\scripts\\setup_github_android_signing.ps1 -KeystorePath <path> -StorePassword <pwd> -KeyAlias <alias> -KeyPassword <pwd> [-Repo owner/name]"
    Write-Host ""
    Write-Host "Example:"
    Write-Host "  .\\scripts\\setup_github_android_signing.ps1 -KeystorePath .\\android\\upload-keystore.jks -StorePassword '***' -KeyAlias upload -KeyPassword '***' -Repo yuraantonov11/siseli-app"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($KeystorePath) -or
    [string]::IsNullOrWhiteSpace($StorePassword) -or
    [string]::IsNullOrWhiteSpace($KeyAlias) -or
    [string]::IsNullOrWhiteSpace($KeyPassword)) {
    throw "All required parameters are mandatory: -KeystorePath -StorePassword -KeyAlias -KeyPassword"
}

$resolvedKeystore = Resolve-Path -Path $KeystorePath -ErrorAction Stop
if (-not (Test-Path -Path $resolvedKeystore -PathType Leaf)) {
    throw "Keystore file not found: $KeystorePath"
}

$ghCmd = Get-Command gh -ErrorAction SilentlyContinue
if (-not $ghCmd) {
    throw "GitHub CLI (gh) is not installed or not available in PATH"
}

$repoArgs = @()
if (-not [string]::IsNullOrWhiteSpace($Repo)) {
    $repoArgs = @("--repo", $Repo)
}

$keystoreBytes = [System.IO.File]::ReadAllBytes($resolvedKeystore)
$keystoreBase64 = [System.Convert]::ToBase64String($keystoreBytes)

function Set-SecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $args = @("secret", "set", $Name) + $repoArgs
    $Value | & gh @args
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to set GitHub secret: $Name"
    }
}

Set-SecretValue -Name "ANDROID_KEYSTORE_BASE64" -Value $keystoreBase64
Set-SecretValue -Name "ANDROID_KEYSTORE_PASSWORD" -Value $StorePassword
Set-SecretValue -Name "ANDROID_KEY_ALIAS" -Value $KeyAlias
Set-SecretValue -Name "ANDROID_KEY_PASSWORD" -Value $KeyPassword

Write-Host "Android signing secrets uploaded successfully."
if ($repoArgs.Count -gt 0) {
    Write-Host "Repository: $Repo"
}

$checkArgs = @("secret", "list") + $repoArgs
& gh @checkArgs


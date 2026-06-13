<#
.SYNOPSIS
    Upload your GOG Galaxy database to gamatrix, unattended.

.DESCRIPTION
    Restores v1's scriptable upload (issue #129). Copies the live (locked)
    Galaxy DB, asks gamatrix for a presigned S3 POST using your API token, and
    uploads the file straight to S3 — gamatrix ingests it automatically from
    there. Designed to run from Task Scheduler with no user interaction.

    Needs nothing beyond a stock Windows 10/11: it uses the built-in curl.exe
    for the S3 upload and Windows PowerShell's Invoke-RestMethod for the presign.

.PARAMETER Token
    Your gamatrix API token. Defaults to the GAMATRIX_TOKEN environment variable,
    then to %USERPROFILE%\.gamatrix\token.

.PARAMETER BaseUrl
    Your gamatrix site, e.g. https://gamatrix.example.com.

.PARAMETER DbPath
    Path to galaxy-2.0.db. Defaults to the standard GOG Galaxy location.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File upload-gamatrix.ps1 -BaseUrl https://gamatrix.example.com
#>
[CmdletBinding()]
param(
    [string]$Token   = $env:GAMATRIX_TOKEN,
    [string]$BaseUrl = "https://gamatrix.example.com",
    [string]$DbPath  = "$env:ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Token)) {
    $tokenFile = "$env:USERPROFILE\.gamatrix\token"
    if (Test-Path $tokenFile) {
        $Token = (Get-Content -Raw $tokenFile).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "No API token. Pass -Token, set GAMATRIX_TOKEN, or save it to $env:USERPROFILE\.gamatrix\token."
}
if (-not (Test-Path $DbPath)) {
    throw "GOG Galaxy DB not found at $DbPath. Pass -DbPath."
}

# Galaxy keeps the DB open, so copy it requesting shared read access (a plain
# Copy-Item can fail with "being used by another process").
$tmp = Join-Path $env:TEMP "gamatrix-galaxy-2.0.db"
$src = [System.IO.File]::Open($DbPath, 'Open', 'Read', 'ReadWrite')
try {
    $dst = [System.IO.File]::Create($tmp)
    try { $src.CopyTo($dst) } finally { $dst.Dispose() }
} finally { $src.Dispose() }

try {
    # 1) Ask gamatrix for a presigned S3 POST (the only authenticated call).
    $presign = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/upload/presign" `
        -Headers @{ Authorization = "Bearer $Token" }

    # 2) Build curl form args: every policy field first, the file LAST (S3
    #    ignores anything after "file").
    $curlArgs = @()
    foreach ($field in $presign.fields.PSObject.Properties) {
        $curlArgs += "-F"; $curlArgs += "$($field.Name)=$($field.Value)"
    }
    $curlArgs += "-F"; $curlArgs += "file=@$tmp"
    $curlArgs += $presign.url

    # 3) Upload straight to S3 with the in-box curl.exe (NOT the PowerShell
    #    `curl` alias, which is Invoke-WebRequest).
    & curl.exe --fail --silent --show-error @curlArgs
    if ($LASTEXITCODE -ne 0) { throw "S3 upload failed (curl exit $LASTEXITCODE)." }

    Write-Host "Uploaded $([math]::Round((Get-Item $tmp).Length / 1MB, 1)) MB. gamatrix will ingest it shortly."
}
finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

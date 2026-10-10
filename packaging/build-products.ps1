[CmdletBinding()]
param(
    [ValidateSet('bridge', 'player', 'all')][string]$Product = 'all',
    [string]$Python,
    [string]$JdkHome,
    [string]$Jar,
    [string]$JSymphonicSource,
    [switch]$Offline,
    [switch]$SkipFrontend,
    [switch]$UnpackedOnly
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Python) { $Python = Join-Path $Repo 'backend\.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Pass -Python with a working Python 3.11+ build interpreter.' }
$Selected = if ($Product -eq 'all') { @('bridge', 'player') } else { @($Product) }
Push-Location $Repo
try {
    & node packaging/generate-icon.cjs
    if ($LASTEXITCODE) { throw 'Approved lotus icon preparation failed' }
    if (-not $SkipFrontend) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE) { throw 'Frontend build failed' }
    }
    foreach ($SelectedProduct in $Selected) {
        $StageArguments = @('packaging/stage.py', '--product', $SelectedProduct)
        if ($Offline) { $StageArguments += '--offline' }
        if ($JdkHome) { $StageArguments += @('--jdk-home', $JdkHome) }
        if ($Jar) { $StageArguments += @('--jar', $Jar) }
        if ($JSymphonicSource) { $StageArguments += @('--jsymphonic-source', $JSymphonicSource) }
        & $Python @StageArguments
        if ($LASTEXITCODE) { throw "Staging $SelectedProduct failed" }
        $PreviousProduct = $env:NIGHTOPS_BUILD_PRODUCT
        $env:NIGHTOPS_BUILD_PRODUCT = $SelectedProduct
        try {
            $BuilderArguments = @('node_modules/electron-builder/cli.js', '--config', 'packaging/electron-builder.cjs', '--win', '--x64', '--publish', 'never')
            if ($UnpackedOnly) { $BuilderArguments += '--dir' }
            & node @BuilderArguments
            if ($LASTEXITCODE) { throw "Installer build failed for $SelectedProduct" }
        } finally { $env:NIGHTOPS_BUILD_PRODUCT = $PreviousProduct }
        $OutputDirectory = Join-Path $Repo "dist_electron\$SelectedProduct"
        $Hashes = Get-ChildItem -LiteralPath $OutputDirectory -File | Where-Object { $_.Extension -in @('.exe', '.blockmap') } | ForEach-Object {
            $Digest = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
            "$($Digest.Hash.ToLowerInvariant())  $($_.Name)"
        }
        if ($Hashes) { $Hashes | Set-Content -LiteralPath (Join-Path $OutputDirectory 'SHA256SUMS.txt') -Encoding utf8 }
    }
} finally { Pop-Location }

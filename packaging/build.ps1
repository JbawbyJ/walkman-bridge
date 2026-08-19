<#
.SYNOPSIS
    Build the Walkman Bridge Windows installer.

.DESCRIPTION
    Assembles a self-contained application - trimmed JRE, ffmpeg, embedded
    Python, the built dashboard and the JSymphonic jar - then compiles it into
    a per-user installer with Inno Setup.

    Everything lands in packaging\build\ (staging) and packaging\dist\ (the
    finished .exe). Both are gitignored.

.PARAMETER SkipJar
    Reuse backend\vendor\jsymphonic.jar instead of rebuilding it from the
    JSymphonic fork with Maven.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
[CmdletBinding()]
param(
    [string]$JdkHome,
    [string]$MavenHome,
    [string]$ForkPath,
    [switch]$SkipJar
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot is not populated inside param() defaults under Windows
# PowerShell 5.1, so the toolchain paths are resolved here instead. They
# default to the portable toolchain kept beside the repo.
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $JdkHome)   { $JdkHome   = Join-Path $Here "..\..\tools\jdk-21.0.12+8" }
if (-not $MavenHome) { $MavenHome = Join-Path $Here "..\..\tools\apache-maven-3.9.9" }
if (-not $ForkPath)  { $ForkPath  = Join-Path $Here "..\..\jsymphonic" }
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is ~10x faster without it

$Root    = (Resolve-Path (Join-Path $Here "..")).Path
$Pkg     = $Here
$Build   = Join-Path $Pkg "build"
$Stage   = Join-Path $Build "app-root"     # becomes the installed folder
$Cache   = Join-Path $Pkg ".cache"         # downloads, reused across builds
$Dist    = Join-Path $Pkg "dist"
$Version = "0.1.0"

$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

function Need-File($path, $what) {
    if (-not (Test-Path $path)) { throw "$what not found: $path" }
}

function Fetch($url, $dest) {
    if (Test-Path $dest) { Info "cached: $(Split-Path $dest -Leaf)"; return }
    Info "downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest
}

# --------------------------------------------------------------------------- #
Step "Preflight"
Need-File $JdkHome "JDK (needed for jlink; override with -JdkHome)"
Need-File (Join-Path $JdkHome "bin\jlink.exe") "jlink"

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",   # winget per-user install
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }
Info "Inno Setup: $iscc"

New-Item -ItemType Directory -Force -Path $Cache, $Dist | Out-Null
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

# --------------------------------------------------------------------------- #
Step "JSymphonic engine (jar)"
$vendorJar = Join-Path $Root "backend\vendor\jsymphonic.jar"
if (-not $SkipJar -and (Test-Path $ForkPath)) {
    $mvn = Join-Path $MavenHome "bin\mvn.cmd"
    if (Test-Path $mvn) {
        Info "mvn package in $ForkPath"
        $env:JAVA_HOME = $JdkHome
        & $mvn -B -q -f (Join-Path $ForkPath "pom.xml") package -DskipTests
        if ($LASTEXITCODE -ne 0) { throw "Maven build failed" }
        $built = Get-ChildItem (Join-Path $ForkPath "target") -Filter "*-jar-with-dependencies.jar" |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 1
        Copy-Item $built.FullName $vendorJar -Force
        Info "jar: $($built.Name)"
    } else { Info "Maven not found - reusing existing jar" }
} else { Info "reusing existing jar" }
Need-File $vendorJar "jsymphonic.jar (build the fork, or place it in backend\vendor\)"

# --------------------------------------------------------------------------- #
Step "Dashboard (npm build)"
Push-Location (Join-Path $Root "frontend")
try {
    if (-not (Test-Path "node_modules")) { & npm install --silent; if ($LASTEXITCODE) { throw "npm install failed" } }
    & npm run build --silent
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
Need-File (Join-Path $Root "frontend\dist\index.html") "frontend build output"

# --------------------------------------------------------------------------- #
Step "Java runtime (jlink)"
# Modules per `jdeps` on the jar (java.desktop is required: the code imports
# Swing types even on the headless path), plus naming/management for the JVM.
& (Join-Path $JdkHome "bin\jlink.exe") `
    --add-modules java.base,java.desktop,java.logging,java.naming,java.management `
    --strip-debug --no-header-files --no-man-pages --compress=zip-6 `
    --output (Join-Path $Stage "jre")
if ($LASTEXITCODE -ne 0) { throw "jlink failed" }
Info ("jre: {0:N0} MB" -f ((Get-ChildItem (Join-Path $Stage "jre") -Recurse | Measure-Object Length -Sum).Sum / 1MB))

# --------------------------------------------------------------------------- #
Step "ffmpeg"
$ffZip = Join-Path $Cache "ffmpeg-essentials.zip"
Fetch $FfmpegUrl $ffZip
$ffTmp = Join-Path $Build "ffmpeg-extract"
if (Test-Path $ffTmp) { Remove-Item -Recurse -Force $ffTmp }
Expand-Archive -Path $ffZip -DestinationPath $ffTmp -Force
$ffExe = Get-ChildItem $ffTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
if (-not $ffExe) { throw "ffmpeg.exe not found in the downloaded archive" }
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "ffmpeg") | Out-Null
Copy-Item $ffExe.FullName (Join-Path $Stage "ffmpeg\ffmpeg.exe") -Force
Info ("ffmpeg: {0:N0} MB" -f ($ffExe.Length / 1MB))

# --------------------------------------------------------------------------- #
Step "Application files"
$appDir = Join-Path $Stage "app"
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "backend"), (Join-Path $appDir "frontend") | Out-Null
Copy-Item (Join-Path $Root "backend\*.py") (Join-Path $appDir "backend") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "backend\vendor") | Out-Null
Copy-Item $vendorJar (Join-Path $appDir "backend\vendor\jsymphonic.jar") -Force
Copy-Item (Join-Path $Root "frontend\dist") (Join-Path $appDir "frontend\dist") -Recurse -Force
Copy-Item (Join-Path $Root "README.md"), (Join-Path $Root "LICENSE") $Stage -Force

# --------------------------------------------------------------------------- #
Step "Launcher (PyInstaller)"
# Built with the project's own venv, which already has the runtime deps.
$venvPy = Join-Path $Root "backend\.venv\Scripts\python.exe"
Need-File $venvPy "backend virtualenv (run scripts\setup.bat first)"
& $venvPy -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Build "pyi-dist") --workpath (Join-Path $Build "pyi-work") `
    --specpath $Build `
    --name WalkmanBridge --onefile --windowed `
    --icon (Join-Path $Pkg "walkman-bridge.ico") `
    --collect-all webview --collect-all fastapi --collect-all starlette `
    --collect-all pydantic --collect-all pydantic_core --collect-all multipart `
    --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    (Join-Path $Pkg "launcher.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item (Join-Path $Build "pyi-dist\WalkmanBridge.exe") $Stage -Force
Info ("launcher: {0:N0} MB" -f ((Get-Item (Join-Path $Stage "WalkmanBridge.exe")).Length / 1MB))

# --------------------------------------------------------------------------- #
Step "Installer (Inno Setup)"
& $iscc "/DAppVersion=$Version" "/DStageDir=$Stage" "/DOutDir=$Dist" (Join-Path $Pkg "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$out = Get-ChildItem $Dist -Filter "*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "`nBuilt: $($out.FullName)" -ForegroundColor Green
Write-Host ("Size:  {0:N1} MB" -f ($out.Length / 1MB)) -ForegroundColor Green

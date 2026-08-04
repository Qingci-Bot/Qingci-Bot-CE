# Qingci-Bot build script (PyInstaller onedir)
# Usage: .\build.ps1
# Output: dist\qingci-bot\qingci-bot.exe
#
# config.yaml and data\ inside the output folder are treated as user data:
# they are stashed before the build and restored afterwards, never overwritten.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$DistDir = Join-Path $Root "dist"
$AppDir  = Join-Path $DistDir "qingci-bot"
$Stash   = Join-Path $DistDir ".user-stash"

if (-not (Test-Path $Python)) {
    throw "python not found: $Python (create .venv and install dependencies first)"
}

# ---------- before build: stash existing user data ----------
if (Test-Path (Join-Path $AppDir "config.yaml")) {
    New-Item -ItemType Directory -Force -Path $Stash | Out-Null
    Copy-Item (Join-Path $AppDir "config.yaml") (Join-Path $Stash "config.yaml") -Force
    Write-Host "stashed existing config.yaml (will restore after build)"
}
if (Test-Path (Join-Path $AppDir "data")) {
    New-Item -ItemType Directory -Force -Path $Stash | Out-Null
    Copy-Item -Recurse -Force (Join-Path $AppDir "data") (Join-Path $Stash "data")
    Write-Host "stashed existing data\ folder (will restore after build)"
}

# ---------- [1/4] PyInstaller build ----------
Write-Host "==> [1/4] PyInstaller build (first run takes 3-10 min)..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean qingci-bot.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }

# ---------- [2/4] copy Web UI ----------
Write-Host "==> [2/4] copying Web UI (web\dist)..." -ForegroundColor Cyan
$WebSrc = Join-Path $Root "web\dist"
if (Test-Path (Join-Path $WebSrc "index.html")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "web") | Out-Null
    Copy-Item -Recurse -Force $WebSrc (Join-Path $AppDir "web\dist")
    Write-Host "    copied web\dist into output folder"
} else {
    Write-Warning "    web\dist\index.html not found; run 'npm run build' in web\ first, /ui will be unavailable"
}

# ---------- [3/4] config.yaml (keep if exists) ----------
Write-Host "==> [3/4] preparing config.yaml..." -ForegroundColor Cyan
$TargetConfig = Join-Path $AppDir "config.yaml"
$StashConfig  = Join-Path $Stash "config.yaml"
if (Test-Path $StashConfig) {
    Copy-Item $StashConfig $TargetConfig -Force
    Write-Host "    restored user config.yaml"
} else {
    Copy-Item (Join-Path $Root "config.yaml") $TargetConfig
    Write-Host "    copied config.yaml template"
}

# ---------- [4/4] data\ folder (keep if exists) ----------
Write-Host "==> [4/4] preparing data\ folder..." -ForegroundColor Cyan
$DataDir   = Join-Path $AppDir "data"
$StashData = Join-Path $Stash "data"
if (Test-Path $StashData) {
    Copy-Item -Recurse -Force $StashData $DataDir
    Write-Host "    restored user data\ folder"
} else {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $DataDir "sensitive_words.txt") | Out-Null
    Write-Host "    created data\ folder (with empty sensitive_words.txt)"
}

# cleanup stash
if (Test-Path $Stash) {
    Remove-Item -Recurse -Force $Stash
}

Write-Host ""
Write-Host "Build finished: $AppDir" -ForegroundColor Green
Write-Host "Run:"
Write-Host "  .\dist\qingci-bot\qingci-bot.exe                # Bot + API"
Write-Host "  .\dist\qingci-bot\qingci-bot.exe --no-bot       # API/Web UI only"
Write-Host "  .\dist\qingci-bot\qingci-bot.exe --port 8080    # custom port"
Write-Host "Then open http://127.0.0.1:8080/ui/"
Write-Host "Note: keep config.yaml and data\ next to the exe; do not move the exe alone."

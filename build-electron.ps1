# Qingci-Bot CE Electron packaging (Electron + Python hybrid)
# Produces: dist\electron\Qingci-Bot-CE-<version>-win-x64.exe (portable)
#
# Prerequisites:
#   1. Run build.ps1 first -> produces dist\qingci-bot-ce\ (Python backend onedir).
#   2. Node.js + npm on PATH.
#
# Output:
#   dist\electron\...  (portable exe; the Python backend is bundled as extraResources/backend)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ElectronDir = Join-Path $Root "desktop\electron"
$BackendDist = Join-Path $Root "dist\qingci-bot-ce"
$ElectronOut = Join-Path $Root "dist\electron"

if (-not (Test-Path (Join-Path $BackendDist "qingci-bot-ce.exe"))) {
    throw "Python backend build not found: $BackendDist. Run build.ps1 first."
}

Set-Location $ElectronDir

# ---------- [1/4] install Electron toolchain (mirror for CN) ----------
Write-Host "==> [1/4] installing electron toolchain..." -ForegroundColor Cyan
& npm install --registry=https://registry.npmmirror.com 2>$null
if ($LASTEXITCODE -ne 0) {
    # fall back to default registry
    Write-Host "    npmmirror install failed; retrying with default registry..." -ForegroundColor DarkYellow
    & npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}

# ---------- [2/4] package (unpacked dir) ----------
Write-Host "==> [2/4] electron-builder (dir)..."
# ELECTRON_MIRROR points electron-builder at npmmirror so the Electron binary
# downloads fast/safely in CN; fall back to the official CDN if it fails.
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
& npx electron-builder --win --dir
if ($LASTEXITCODE -ne 0) {
    Remove-Item Env:ELECTRON_MIRROR -ErrorAction SilentlyContinue
    Write-Host "    npmmirror build failed; retrying with official resources..." -ForegroundColor DarkYellow
    & npx electron-builder --win --dir
    if ($LASTEXITCODE -ne 0) { throw "electron-builder (dir) failed" }
}
Remove-Item Env:ELECTRON_MIRROR -ErrorAction SilentlyContinue

# ---------- [3/4] verify resources bundled ----------
$BackendBundle = Join-Path $ElectronOut "win-unpacked\resources\backend\qingci-bot-ce.exe"
if (-not (Test-Path $BackendBundle)) {
    throw "backend not bundled into resources: $BackendBundle"
}
Write-Host "    backend bundled -> $BackendBundle"

# ---------- [4/4] portable exe ----------
Write-Host "==> [4/4] electron-builder (portable)..."
& npx electron-builder --win portable
if ($LASTEXITCODE -ne 0) { throw "electron-builder (portable) failed" }

# ---------- finish ----------
$Artifact = Get-ChildItem -Path $ElectronOut -Filter "*.exe" | Where-Object { $_.Name -notlike "*unpacked*" } | Select-Object -First 1
Write-Host ""
if ($Artifact) {
    Write-Host "Electron package finished: $($Artifact.FullName)" -ForegroundColor Green
    Write-Host "Run it to start Qingci-Bot CE (bundled Python backend)."
} else {
    Write-Warning "portable exe not found in $ElectronOut"
}
# Qingci-Bot CE build script (PyInstaller onedir)
# Usage: .\build.ps1
# Output: dist\qingci-bot-ce\qingci-bot-ce.exe
#
# 自 v1.6 起配置/插件/数据已收敛到 instances/<name>/ 自包含目录，
# 构建产物不再生成根级 config.yaml 或 data\（用户数据按实例隔离，随实例目录分发）。

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$DistDir = Join-Path $Root "dist"
$AppDir  = Join-Path $DistDir "qingci-bot-ce"

if (-not (Test-Path $Python)) {
    throw "python not found: $Python (create .venv and install dependencies first)"
}

# ---------- [0/3] 独立插件 SDK ----------
# 外部插件运行时 import qingci_plugin_sdk，必须装进构建环境才能在
# PyInstaller 打包时一并收集（spec 中 collect_all('qingci_plugin_sdk')）。
# 相对路径依赖在 pyproject.toml 中无法解析（打包时丢工作目录），故在此显式安装。
Write-Host "==> [0/3] installing qingci-plugin-sdk (Plugins-SDK)..." -ForegroundColor Cyan
uv pip install --python $Python -e (Join-Path $Root "..\Plugins-SDK")
if ($LASTEXITCODE -ne 0) { throw "qingci-plugin-sdk install failed with exit code $LASTEXITCODE" }

# ---------- [1/3] PyInstaller build ----------
Write-Host "==> [1/3] PyInstaller build (first run takes 3-10 min)..." -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean qingci-bot-ce.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }

# ---------- [2/3] copy Web UI ----------
Write-Host "==> [2/3] copying Web UI (web\dist)..." -ForegroundColor Cyan
$WebSrc = Join-Path $Root "web\dist"
if (Test-Path (Join-Path $WebSrc "index.html")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $AppDir "web") | Out-Null
    Copy-Item -Recurse -Force $WebSrc (Join-Path $AppDir "web\dist")
    Write-Host "    copied web\dist into output folder"
} else {
    Write-Warning "    web\dist\index.html not found; run 'npm run build' in web\ first, /ui will be unavailable"
}

# ---------- [3/3] ensure output folder exists ----------
Write-Host "==> [3/3] finished output folder..." -ForegroundColor Cyan
if (-not (Test-Path $AppDir)) {
    throw "output folder missing: $AppDir"
}

Write-Host ""
Write-Host "Build finished: $AppDir" -ForegroundColor Green
Write-Host "Run:"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe                # Bot + API"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe --no-bot       # API/Web UI only"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe --port 8080    # custom port"
Write-Host "Then open http://127.0.0.1:8080/ui/"
Write-Host "Note: config/plugins/data live inside instances\<name>\; first launch auto-creates the default instance."

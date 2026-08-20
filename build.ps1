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

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found in PATH. Install uv first: https://docs.astral.sh/uv/ (e.g. 'pip install uv' or 'winget install astral-sh.uv')"
}

# ---------- [0/3] 独立插件 SDK ----------
# 外部插件运行时 import qingci_plugin_sdk，必须装进构建环境才能在
# PyInstaller 打包时一并收集（spec 中 collect_all('qingci_plugin_sdk')）。
# 直接拉取 Gitee main（Gitee 与 GitHub 实时同步），不依赖本地兄弟目录。
Write-Host "==> [0/3] installing qingci-plugin-sdk (Gitee main)..." -ForegroundColor Cyan
uv pip install --python $Python "qingci-plugin-sdk @ git+https://gitee.com/qingci-bot/Plugins-SDK.git@main"
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

# ---------- [2.5/3] bundle Playwright browser ----------
# 全内置 HTML 渲染：把 Chromium 浏览器下载到产物目录 ms-playwright/，
# 运行时由 main.py 设 PLAYWRIGHT_BROWSERS_PATH 指向它，EXE 开箱即可渲染签到卡，
# 无需最终用户另行 `playwright install chromium`。
# 国内镜像前缀可缓解下载慢/失败：$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"
Write-Host "==> [2.5/3] bundling Playwright chromium (headless shell)..." -ForegroundColor Cyan
try {
    # playwright 作为构建期依赖被安装（render 分组），这里先确认可导入
    & $Python -c "import playwright.async_api"
    if ($LASTEXITCODE -ne 0) { throw "playwright not importable" }

    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $AppDir "ms-playwright"
    # --only-shell 仅下载无头浏览器（比完整 chromium 更小，足以支撑 HTML 渲染）
    & $Python -m playwright install chromium --only-shell
    if ($LASTEXITCODE -ne 0) { throw "playwright install chromium --only-shell failed" }

    if (-not (Test-Path (Join-Path $env:PLAYWRIGHT_BROWSERS_PATH))) {
        throw "browser bundle dir missing: $env:PLAYWRIGHT_BROWSERS_PATH"
    }
    Write-Host "    bundled Playwright browsers -> $env:PLAYWRIGHT_BROWSERS_PATH"
} catch {
    Write-Warning "    Playwright 浏览器内置失败（EXE 将不带渲染能力，签到卡会降级纯文本）: $_"
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

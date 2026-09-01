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
# 锁定到已发布 tag（与 pyproject.toml 保持一致），保证构建可复现；
# SDK 发新版后同步更新这里与 pyproject.toml 的 tag。
# 默认走 Gitee 镜像（国内拉取更快）；GitHub Actions（海外 IP）访问 Gitee 常被限流
#（HTTP 429），故支持 QINGCI_SDK_GIT_URL 覆盖为 GitHub 主仓库（release 流水线已注入）。
$SdkGitUrl = if ($env:QINGCI_SDK_GIT_URL) { $env:QINGCI_SDK_GIT_URL } else { "https://gitee.com/qingci-bot/Plugins-SDK.git" }
Write-Host "==> [0/3] installing qingci-plugin-sdk (v1.13.6) from $SdkGitUrl ..." -ForegroundColor Cyan
uv pip install --python $Python "qingci-plugin-sdk @ git+${SdkGitUrl}@v1.13.6"
if ($LASTEXITCODE -ne 0) { throw "qingci-plugin-sdk install failed with exit code $LASTEXITCODE" }

# ---------- ensure pip in venv (for bundled deps installer) ----------
# uv-managed venvs ship without pip by default. In packaged (frozen) mode the
# plugin deps installer (bot/plugin/deps.py) calls pip._internal in-process, and
# the spec collects pip via collect_all('pip'). Without pip present in the
# build venv, collect_all grabs nothing and the bundled EXE cannot auto-install
# plugin third-party deps. Install pip here so it gets collected into the EXE.
Write-Host "==> ensuring pip in build venv (for bundled plugin deps)..." -ForegroundColor Cyan
# 用 2>$null 丢弃 stderr 而非 2>&1 | Out-Null：$ErrorActionPreference="Stop" 下
# PowerShell 5.1 会把 native stderr 转成 terminating error 中断脚本（pip 缺失时
# python 打印的 Traceback 会误杀构建）；这里只关心退出码。
& $Python -c "import pip" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    pip absent in uv-managed venv; installing via uv (normal path)..." -ForegroundColor DarkGray
    # uv-managed venvs ship without pip; install via uv instead of ensurepip
    # (uv may not offer ensurepip). Needed so collect_all('pip') in the spec
    # can bundle pip into the EXE for in-process plugin dependency install.
    & uv pip install --python $Python pip
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "    could not install pip in build venv; EXE will not bundle pip, plugin auto-deps unavailable in packaged mode"
    }
}
& $Python -c "import pip; print('    pip available:', pip.__version__)"

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
# Bundle a Chromium browser into dist/ms-playwright/ so the EXE can render HTML
# out of the box (sign-in card) without requiring end users to run
# `playwright install chromium`. Runtime reads PLAYWRIGHT_BROWSERS_PATH to point
# at this folder.
# Download strategy: prefer the npmmirror mirror (fast in CN), fall back to the
# official CDN. Success is decided by the on-disk browser dir, NOT the exit code,
# because Playwright writes progress to stdout which PowerShell swallows into the
# return value and may report a non-zero status even after a successful download.
Write-Host "==> [2.5/3] bundling Playwright browser..." -ForegroundColor Cyan
try {
    # playwright is a build-time dependency (render extra); assert it is importable
    & $Python -c "import playwright.async_api"
    if ($LASTEXITCODE -ne 0) { throw "playwright not importable" }

    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $AppDir "ms-playwright"
    # Clear stale/custom download-host vars so a leftover env value (e.g. the
    # deprecated playwright.azureedge.net) cannot hijack the downloads.
    Remove-Item Env:PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST -ErrorAction SilentlyContinue
    $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"

    # A browser is present if the headless-shell or full-chromium dir exists.
    function Test-BrowserBundled {
        param([string]$Dir)
        if (-not (Test-Path $Dir)) { return $false }
        return (Get-ChildItem -Path $Dir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "chromium" }).Count -gt 0
    }

    function Invoke-PlaywrightInstall {
        param([string[]]$Flags)
        # CI=1 makes Playwright suppress its ANSI progress bar (which renders as
        # mojibake in Windows PowerShell) and print a single line per download.
        $env:CI = "1"
        # CRITICAL: capture the child output to a temp log file instead of letting
        # it flow to the console. A PowerShell function's return value is the
        # concatenation of ALL unredirected output, so `& $Python ... 2>&1` would
        # leak the download progress into `$code` and make `-ne 0` always true
        # (a string is never 0), i.e. every download reported as a failure even
        # when it succeeded. Redirecting everything to a file keeps the real exit
        # code in `$code` and hides the raw progress/mojibake from the console.
        $logFile = Join-Path $env:TEMP ("pw-install-{0}.log" -f ([guid]::NewGuid()))
        & $Python -m playwright install chromium @Flags *> $logFile
        $rc = $LASTEXITCODE
        Remove-Item Env:CI -ErrorAction SilentlyContinue
        return $rc
    }

    $browserDir = $env:PLAYWRIGHT_BROWSERS_PATH
    # Try only-shell first (small, enough for HTML rendering), then full chromium
    # (mirror lags on the headless-shell revision and 404s; full chromium is
    # always synced), then fall back to the official CDN.
    $attempts = @(
        @{ Host = "https://npmmirror.com/mirrors/playwright"; Flags = @("--only-shell") },
        @{ Host = "https://npmmirror.com/mirrors/playwright"; Flags = @("--no-shell") },
        @{ Host = "https://cdn.playwright.dev";                 Flags = @("--only-shell") }
    )
    $ok = Test-BrowserBundled $browserDir
    foreach ($a in $attempts) {
        if ($ok) { break }
        Write-Host "    downloading from $($a.Host) ($($a.Flags -join ' '))..."
        $env:PLAYWRIGHT_DOWNLOAD_HOST = $a.Host
        $code = Invoke-PlaywrightInstall @($a.Flags)
        if ($code -ne 0) {
            Write-Warning "    download failed (exit=$code), trying next source..."
        }
        $ok = Test-BrowserBundled $browserDir
    }
    if (-not $ok) {
        throw "browser bundle dir missing or empty: $browserDir"
    }
    Write-Host "    bundled Playwright browser -> $browserDir"
} catch {
    Write-Warning "    Playwright browser bundling failed (EXE will lack render capability; sign-in card degrades to text): $_"
}

# ---------- [3/3] ensure output folder exists ----------
Write-Host "==> [3/3] finished output folder..." -ForegroundColor Cyan
if (-not (Test-Path $AppDir)) {
    throw "output folder missing: $AppDir"
}

# ---------- [3.5/3] SHA256 校验清单 ----------
# 供最终用户校验分发产物完整性/防篡改；同时校验 Web UI 与 EXE 本体。
Write-Host "==> [3.5/3] generating SHA256 checksums..." -ForegroundColor Cyan
$ExeFile = Join-Path $AppDir "qingci-bot-ce.exe"
$Manifest = Join-Path $DistDir "qingci-bot-ce.sha256"
if (Test-Path $ExeFile) {
    $exeHash = (Get-FileHash -Algorithm SHA256 -Path $ExeFile).Hash.ToLower()
    $webHash = $null
    $webIndex = Join-Path $AppDir "web\dist\index.html"
    if (Test-Path $webIndex) {
        $webHash = (Get-FileHash -Algorithm SHA256 -Path $webIndex).Hash.ToLower()
    }
    $lines = @("qingci-bot-ce.exe  $exeHash")
    if ($webHash) { $lines += "web\dist\index.html  $webHash" }
    $lines | Set-Content -Encoding utf8 $Manifest
    Write-Host "    SHA256 manifest -> $Manifest"
} else {
    Write-Warning "    exe 未找到，跳过 SHA256 清单生成: $ExeFile"
}

Write-Host ""
Write-Host "Build finished: $AppDir" -ForegroundColor Green
Write-Host "Run:"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe                # Bot + API"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe --no-bot       # API/Web UI only"
Write-Host "  .\dist\qingci-bot-ce\qingci-bot-ce.exe --port 8080    # custom port"
Write-Host "Then open http://127.0.0.1:8080/ui/"
Write-Host "Note: config/plugins/data live inside instances\<name>\; first launch auto-creates the default instance."

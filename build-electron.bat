@echo off
setlocal
rem ============================================================
rem Qingci-Bot CE Electron packaging (Electron + Python hybrid)
rem Produces Windows targets from desktop\electron\package.json
rem   (default: NSIS installer + green zip)
rem
rem Prerequisites:
rem   1. Run build.bat first -> produces dist\qingci-bot-ce\
rem      (Python backend onedir).
rem   2. Node.js + npm on PATH.
rem
rem Output:
rem   dist\electron\...  (Windows targets; the Python backend is
rem      bundled as extraResources/backend)
rem ============================================================

set "Root=%~dp0"
set "ElectronDir=%Root%desktop\electron"
set "BackendDist=%Root%dist\qingci-bot-ce"
set "ElectronOut=%Root%dist\electron"

if not exist "%BackendDist%\qingci-bot-ce.exe" (
    echo [ERROR] Python backend build not found: %BackendDist%
    echo          Run build.bat first.
    exit /b 1
)

cd /d "%ElectronDir%"

rem ---- [1/4] install Electron toolchain (mirror for CN) ----
echo ==^> [1/4] installing electron toolchain...
npm install --registry=https://registry.npmmirror.com >nul 2>&1
if errorlevel 1 (
    echo      npmmirror install failed; retrying with default registry...
    npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed
        exit /b 1
    )
)

rem ---- [2/4] package (unpacked dir) ----
rem ELECTRON_MIRROR points electron-builder at npmmirror so the Electron binary
rem downloads fast/safely in CN; fall back to the official CDN if it fails.
echo ==^> [2/4] electron-builder ^(dir^)...
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
npx electron-builder --win --dir
if errorlevel 1 (
    set "ELECTRON_MIRROR="
    echo      npmmirror build failed; retrying with official resources...
    npx electron-builder --win --dir
    if errorlevel 1 (
        echo [ERROR] electron-builder ^(dir^) failed
        exit /b 1
    )
)
set "ELECTRON_MIRROR="

rem ---- [3/4] verify resources bundled ----
set "BackendBundle=%ElectronOut%\win-unpacked\resources\backend\qingci-bot-ce.exe"
if not exist "%BackendBundle%" (
    echo [ERROR] backend not bundled into resources: %BackendBundle%
    exit /b 1
)
echo      backend bundled -^> %BackendBundle%

rem ---- [4/4] Windows targets from package.json (NSIS installer + green zip) ----
echo ==^> [4/4] electron-builder ^(Windows targets^)...
npx electron-builder --win
if errorlevel 1 (
    echo [ERROR] electron-builder ^(Windows targets^) failed
    exit /b 1
)

rem ---- finish: list produced artifacts ----
echo.
set "ARTIFACTS="
for %%f in ("%ElectronOut%\*.exe") do if exist "%%f" set "ARTIFACTS=1"
for %%f in ("%ElectronOut%\*.zip") do if exist "%%f" set "ARTIFACTS=1"
if defined ARTIFACTS (
    echo Electron packages finished in %ElectronOut%:
    dir /b "%ElectronOut%\*.exe" "%ElectronOut%\*.zip"
    echo.
    echo Run the installer or unzip the green package to start Qingci-Bot CE ^(bundled Python backend^).
) else (
    echo [WARN] Windows packages not found in %ElectronOut%
)
echo.
exit /b 0
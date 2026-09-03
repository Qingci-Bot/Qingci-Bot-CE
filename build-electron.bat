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
rem
rem Logging:
rem   Every step is echoed to the console AND appended to
rem   build-electron.log beside this script (recreated each run),
rem   so a double-click exit can still be diagnosed from the log.
rem   On failure the window pauses to keep the message visible,
rem   unless running in CI (GITHUB_ACTIONS / CI set) where it
rem   exits immediately so the pipeline records the failure.
rem ============================================================

set "Root=%~dp0"
set "ElectronDir=%Root%desktop\electron"
set "BackendDist=%Root%dist\qingci-bot-ce"
set "ElectronOut=%Root%dist\electron"
set "LogFile=%Root%build-electron.log"

if exist "%LogFile%" del /q "%LogFile%"

if not exist "%BackendDist%\qingci-bot-ce.exe" (
    call :log "[ERROR] Python backend build not found: %BackendDist%"
    call :log "          Run build.bat first."
    call :fail
)

cd /d "%ElectronDir%"

rem ---- [1/4] install Electron toolchain (mirror for CN) ----
call :log "==> [1/4] installing electron toolchain..."
npm install --registry=https://registry.npmmirror.com >>"%LogFile%" 2>&1
if errorlevel 1 (
    call :log "      npmmirror install failed; retrying with default registry..."
    npm install >>"%LogFile%" 2>&1
    if errorlevel 1 (
        call :log "[ERROR] npm install failed"
        call :fail
    )
)

rem ---- [2/4] package (unpacked dir) ----
rem ELECTRON_MIRROR points electron-builder at npmmirror so the Electron binary
rem downloads fast/safely in CN; fall back to the official CDN if it fails.
call :log "==> [2/4] electron-builder (dir)..."
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
npx electron-builder --win --dir >>"%LogFile%" 2>&1
if errorlevel 1 (
    set "ELECTRON_MIRROR="
    call :log "      npmmirror build failed; retrying with official resources..."
    npx electron-builder --win --dir >>"%LogFile%" 2>&1
    if errorlevel 1 (
        call :log "[ERROR] electron-builder (dir) failed"
        call :fail
    )
)
set "ELECTRON_MIRROR="

rem ---- [3/4] verify resources bundled ----
set "BackendBundle=%ElectronOut%\win-unpacked\resources\backend\qingci-bot-ce.exe"
if not exist "%BackendBundle%" (
    call :log "[ERROR] backend not bundled into resources: %BackendBundle%"
    call :fail
)
call :log "      backend bundled: %BackendBundle%"

rem ---- [4/4] Windows targets from package.json (NSIS installer + green zip) ----
call :log "==> [4/4] electron-builder (Windows targets)..."
npx electron-builder --win >>"%LogFile%" 2>&1
if errorlevel 1 (
    call :log "[ERROR] electron-builder (Windows targets) failed"
    call :fail
)

rem ---- finish: list produced artifacts ----
call :log "."
set "ARTIFACTS="
for %%f in ("%ElectronOut%\*.exe") do if exist "%%f" set "ARTIFACTS=1"
for %%f in ("%ElectronOut%\*.zip") do if exist "%%f" set "ARTIFACTS=1"
if defined ARTIFACTS (
    call :log "Electron packages finished in %ElectronOut%:"
    dir /b "%ElectronOut%\*.exe" "%ElectronOut%\*.zip" >>"%LogFile%" 2>&1
    call :log "      See %LogFile% for details."
    call :log "      Run the installer or unzip the green package to start Qingci-Bot CE (bundled Python backend)."
) else (
    call :log "[WARN] Windows packages not found in %ElectronOut%"
)
call :log "."
exit /b 0

rem --- subroutines ---
:fail
if not defined GITHUB_ACTIONS if not defined CI pause
exit /b 1

:log
echo %~1
echo %~1>>"%LogFile%"
exit /b
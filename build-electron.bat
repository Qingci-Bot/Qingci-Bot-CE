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
rem Logging: every step is echoed AND appended to build-electron.log
rem beside this script (recreated each run) so a double-click exit
rem can still be diagnosed. On failure the window pauses unless CI.
rem ============================================================

set "Root=%~dp0"
set "ElectronDir=%Root%desktop\electron"
set "BackendDist=%Root%dist\qingci-bot-ce"
set "ElectronOut=%Root%dist\electron"
set "LogFile=%Root%build-electron.log"

if exist "%LogFile%" del /q "%LogFile%"

rem ---- [0/4] backend prerequisite ----
if exist "%BackendDist%\qingci-bot-ce.exe" goto backend_ok
echo.>>"%LogFile%"
echo [ERROR] Python backend build not found: %BackendDist%>>"%LogFile%"
echo [ERROR] Run build.bat first.>>"%LogFile%"
goto fail

:backend_ok
cd /d "%ElectronDir%" >nul 2>&1
if errorlevel 1 goto fail

echo.>>"%LogFile%"
echo ==> [1/4] installing electron toolchain...>>"%LogFile%"
echo ==> [1/4] installing electron toolchain...
npm install --registry=https://registry.npmmirror.com >>"%LogFile%" 2>&1
if not errorlevel 1 goto npm_ok
echo      npmmirror failed; retrying default registry...>>"%LogFile%"
npm install >>"%LogFile%" 2>&1
if errorlevel 1 goto fail

:npm_ok
echo ==> [2/4] electron-builder (dir)...>>"%LogFile%"
echo ==> [2/4] electron-builder (dir)...
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
npx electron-builder --win --dir >>"%LogFile%" 2>&1
if not errorlevel 1 goto dir_ok
set "ELECTRON_MIRROR="
echo      npmmirror build failed; retrying official resources...>>"%LogFile%"
npx electron-builder --win --dir >>"%LogFile%" 2>&1
if errorlevel 1 goto fail
:dir_ok
set "ELECTRON_MIRROR="

rem ---- [3/4] verify backend bundled ----
if exist "%ElectronOut%\win-unpacked\resources\backend\qingci-bot-ce.exe" goto bundle_ok
echo [ERROR] backend not bundled into resources.>>"%LogFile%"
goto fail
:bundle_ok
echo      backend bundled.>>"%LogFile%"

rem ---- [4/4] Windows targets (NSIS installer + green zip) ----
echo ==> [4/4] electron-builder (Windows targets)...>>"%LogFile%"
echo ==> [4/4] electron-builder (Windows targets)...
npx electron-builder --win >>"%LogFile%" 2>&1
if errorlevel 1 goto fail

for %%f in ("%ElectronOut%\*.zip") do if exist "%%f" goto artifacts
echo [WARN] Windows packages not found in %ElectronOut%.>>"%LogFile%"
goto done

:artifacts
echo.>>"%LogFile%"
echo Electron packages finished in %ElectronOut%:>>"%LogFile%"
dir /b "%ElectronOut%\*.exe" "%ElectronOut%\*.zip" >>"%LogFile%" 2>&1

:done
echo.>>"%LogFile%"
exit /b 0

:fail
echo.>>"%LogFile%"
echo [ERROR] build failed - see %LogFile%.>>"%LogFile%"
if not defined GITHUB_ACTIONS if not defined CI pause
exit /b 1
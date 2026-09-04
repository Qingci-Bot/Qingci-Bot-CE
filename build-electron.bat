@echo off
setlocal
echo P0 start
set "Root=%~dp0"
echo P1 root=[%Root%]
set "ElectronDir=%Root%desktop\electron"
set "BackendDist=%Root%dist\qingci-bot-ce"
set "ElectronOut=%Root%dist\electron"
echo P2 be=[%BackendDist%]\qingci-bot-ce.exe
if not exist "%BackendDist%\qingci-bot-ce.exe" goto fail
echo P3 backend ok
cd /d "%ElectronDir%" >nul 2>&1
echo P4 cd_err=%errorlevel% pwd=%cd%
if errorlevel 1 goto fail
echo P5 npm
call npm install --registry=https://registry.npmmirror.com
echo P6 npm_err=%errorlevel%
if not errorlevel 1 goto PN
echo P7 npm fallback
call npm install
echo P8 npm2_err=%errorlevel%
if errorlevel 1 goto fail
:PN
echo P9 dir build
call npx electron-builder --win --dir
echo P10 dir_err=%errorlevel%
if errorlevel 1 goto fail
echo P11 bundle check
if not exist "%ElectronOut%\win-unpacked\resources\backend\qingci-bot-ce.exe" goto fail
echo P12 win build
call npx electron-builder --win
echo P13 win_err=%errorlevel%
if errorlevel 1 goto fail
echo P14 done
exit /b 0
:fail
echo PFAIL errorlevel=%errorlevel%
if not defined CI if not defined GITHUB_ACTIONS pause
exit /b 1
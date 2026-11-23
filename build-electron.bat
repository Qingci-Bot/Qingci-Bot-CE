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
rem 后端打成单个 zip：安装阶段只写 1 个大文件，避免数千个小文件逐个落盘
set "PyExe=%Root%.venv\Scripts\python.exe"
if not exist "%PyExe%" set "PyExe=python"
echo P3b pack backend
"%PyExe%" "%Root%scripts\pack_backend.py"
if errorlevel 1 goto fail
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
if not exist "%ElectronOut%\win-unpacked\resources\backend.zip" goto fail
echo P12 win build
rem --publish never：只构建，不上传。electron-builder 生成 nsis 的 blockmap 后
rem 默认会尝试自动发布到 GitHub，缺 GH_TOKEN 时整个构建以失败告终
rem （产物其实已就绪）。发布统一由 release.yml 的 gh release upload 负责。
call npx electron-builder --win --publish never
echo P13 win_err=%errorlevel%
if errorlevel 1 goto fail
echo P14 done
exit /b 0
:fail
echo PFAIL errorlevel=%errorlevel%
if not defined CI if not defined GITHUB_ACTIONS pause
exit /b 1
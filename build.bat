@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem Qingci-Bot CE build script (PyInstaller onedir)
rem Usage: build.bat
rem Output: dist\qingci-bot-ce\qingci-bot-ce.exe
rem
rem Since v1.6 config/plugin/data are self-contained under
rem instances\<name>\ so the build output no longer creates a
rem root-level config.yaml or data\ (user data stays per-instance).
rem ============================================================

set "Root=%~dp0"
cd /d "%Root%"

set "Python=%Root%.venv\Scripts\python.exe"
set "DistDir=%Root%dist"
set "AppDir=%DistDir%\qingci-bot-ce"

if not exist "%Python%" (
    echo [ERROR] python not found: %Python%
    echo          ^(create .venv and install dependencies first^)
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found in PATH. Install uv first:
    echo          https://docs.astral.sh/uv/   ^(e.g. 'pip install uv' or 'winget install astral-sh.uv'^)
    exit /b 1
)

rem ---- [0/3] standalone plugin SDK ----
rem External plugins import qingci_plugin_sdk at runtime, so it must be
rem installed into the build env for PyInstaller collect_all. Pin to a
rem released tag (kept in sync with pyproject.toml) for reproducible builds.
rem Default source is the Gitee mirror (faster in CN); GitHub Actions hitting
rem Gitee often gets HTTP 429, so QINGCI_SDK_GIT_URL can override to GitHub.
if defined QINGCI_SDK_GIT_URL (
    set "SdkGitUrl=%QINGCI_SDK_GIT_URL%"
) else (
    set "SdkGitUrl=https://gitee.com/qingci-bot/Plugins-SDK.git"
)
echo ==^> [0/3] installing qingci-plugin-sdk ^(v1.13.6^) from !SdkGitUrl! ...
uv pip install --python "%Python%" "qingci-plugin-sdk @ git+!SdkGitUrl!@v1.13.6"
if errorlevel 1 (
    echo [ERROR] qingci-plugin-sdk install failed
    exit /b 1
)

rem ---- ensure pip in venv (for bundled deps installer) ----
rem uv-managed venvs ship without pip by default. In packaged (frozen) mode the
rem plugin deps installer calls pip._internal in-process and the spec collects
rem pip via collect_all('pip'). Install pip here so it gets collected into EXE.
echo ==^> ensuring pip in build venv ^(for bundled plugin deps^)...
"%Python%" -c "import pip" >nul 2>&1
if errorlevel 1 (
    echo      pip absent in uv-managed venv; installing via uv ^(normal path^)...
    uv pip install --python "%Python%" pip
    if errorlevel 1 (
        echo [WARN] could not install pip in build venv; EXE will not bundle pip
    )
)
"%Python%" -c "import pip; print('    pip available:', pip.__version__)" >nul 2>&1

rem ---- [1/3] PyInstaller build ----
echo ==^> [1/3] PyInstaller build ^(first run takes 3-10 min^)...
"%Python%" -m PyInstaller --noconfirm --clean qingci-bot-ce.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed
    exit /b 1
)

rem ---- [2/3] copy Web UI ----
echo ==^> [2/3] copying Web UI ^(web\dist^)...
if exist "%Root%web\dist\index.html" (
    if not exist "%AppDir%\web" mkdir "%AppDir%\web" >nul
    xcopy /e /i /y "%Root%web\dist" "%AppDir%\web\dist" >nul
    echo      copied web\dist into output folder
) else (
    echo [WARN] web\dist\index.html not found; run 'npm run build' in web\ first
)

rem ---- [2.5/3] bundle Playwright browser ----
rem Bundle a Chromium browser into dist/ms-playwright so the EXE renders HTML
rem out of the box (sign-in card) without requiring `playwright install`.
rem Download strategy: prefer npmmirror (fast in CN), fall back to official CDN.
rem Success is decided by the on-disk browser dir, NOT the exit code (Playwright
rem writes progress to stdout and may exit non-zero even after a successful run).
echo ==^> [2.5/3] bundling Playwright browser...
set "PWBROWSER=%AppDir%\ms-playwright"
set "PW_BUNDLED=0"

rem playwright is a build-time dependency (render extra); assert importable
"%Python%" -c "import playwright.async_api" >nul 2>&1
if errorlevel 1 goto :pw_not_importable

set "PLAYWRIGHT_BROWSERS_PATH=%PWBROWSER%"
rem Clear stale/custom download-host vars so a leftover env value cannot hijack
set "PLAYWRIGHT_DOWNLOAD_HOST="
set "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST="

call :check_browser
if "!PW_BUNDLED!"=="1" goto :pw_done

echo      downloading from npmmirror ^(--only-shell^)...
set "PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright"
set "CI=1"
"%Python%" -m playwright install chromium --only-shell > "%TEMP%\pw-install.log" 2>&1
set "CI="
call :check_browser
if "!PW_BUNDLED!"=="1" goto :pw_done

echo      downloading from npmmirror ^(--no-shell^)...
"%Python%" -m playwright install chromium --no-shell > "%TEMP%\pw-install.log" 2>&1
call :check_browser
if "!PW_BUNDLED!"=="1" goto :pw_done

echo      downloading from official CDN ^(--only-shell^)...
set "PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.playwright.dev"
"%Python%" -m playwright install chromium --only-shell > "%TEMP%\pw-install.log" 2>&1
call :check_browser
if "!PW_BUNDLED!"=="1" goto :pw_done

echo [WARN] browser bundle dir missing or empty: !PWBROWSER!
goto :pw_finish

:pw_done
echo      bundled Playwright browser -^> !PWBROWSER!
goto :pw_finish

:pw_not_importable
echo [WARN] playwright not importable; skipping browser bundling
:pw_finish

rem ---- [3/3] ensure output folder exists ----
echo ==^> [3/3] finished output folder...
if not exist "%AppDir%\." (
    echo [ERROR] output folder missing: %AppDir%
    exit /b 1
)

rem ---- [3.5/3] SHA256 checksums ----
rem For end users to verify distribution integrity / tampering; covers the Web
rem UI and the EXE itself.
echo ==^> [3.5/3] generating SHA256 checksums...
set "ExeFile=%AppDir%\qingci-bot-ce.exe"
set "Manifest=%DistDir%\qingci-bot-ce.sha256"
if exist "%ExeFile%" (
    set "EXE_HASH=x"
    for /f "skip=1 delims= tokens=*" %%h in ('certutil -hashfile "%ExeFile%" SHA256') do (
        if "!EXE_HASH!"=="x" set "EXE_HASH=%%h"
    )
    if "!EXE_HASH!"=="x" set "EXE_HASH="
    set "EXE_HASH=!EXE_HASH: =!"

    set "WEB_HASH="
    if exist "%AppDir%\web\dist\index.html" (
        set "WEB_HASH=x"
        for /f "skip=1 delims= tokens=*" %%w in ('certutil -hashfile "%AppDir%\web\dist\index.html" SHA256') do (
            if "!WEB_HASH!"=="x" set "WEB_HASH=%%w"
        )
        if "!WEB_HASH!"=="x" set "WEB_HASH="
        set "WEB_HASH=!WEB_HASH: =!"
    )

    (
        echo qingci-bot-ce.exe  !EXE_HASH!
        if defined WEB_HASH echo web\dist\index.html  !WEB_HASH!
    ) > "%Manifest%"
    echo      SHA256 manifest -^> %Manifest%
) else (
    echo [WARN] exe not found, skipping SHA256 manifest: %ExeFile%
)

echo.
echo Build finished: %AppDir%
echo Run:
echo   .\dist\qingci-bot-ce\qingci-bot-ce.exe                # Bot + API
echo   .\dist\qingci-bot-ce\qingci-bot-ce.exe --no-bot       # API/Web UI only
echo   .\dist\qingci-bot-ce\qingci-bot-ce.exe --port 8080    # custom port
echo Then open http://127.0.0.1:8080/ui/
echo Note: config/plugins/data live inside instances\<name>; first launch auto-creates the default instance.
echo.
exit /b 0

rem ---- subroutine: true if a chromium browser dir exists under PWBROWSER ----
:check_browser
set "PW_BUNDLED=0"
if not exist "%PWBROWSER%\." exit /b 0
rem dir /b with no match sets errorlevel 1; avoids the `for /d` literal-pattern
rem iteration quirk that would otherwise always report "bundled".
dir /b /a:d "%PWBROWSER%\chromium*" >nul 2>&1
if not errorlevel 1 set "PW_BUNDLED=1"
exit /b 0
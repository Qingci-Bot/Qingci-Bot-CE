#!/usr/bin/env bash
# Qingci-Bot CE Linux build script (PyInstaller onedir)
# Usage: ./build-linux.sh
# Output: dist/qingci-bot-ce/qingci-bot-ce
#
# 与 build.bat（Windows）对应的 Linux 版本。产出 Python 后端 onedir，
# 供 release 流水线（.github/workflows/ci.yml 的 release job）随后用
# electron-builder 打 Linux AppImage。
#
# 自 v1.6 起配置/插件/数据已收敛到 instances/<name>/ 自包含目录，
# 构建产物不再生成根级 config.yaml 或 data/（用户数据按实例隔离，随实例目录分发）。
#
# 注意：icon 仅 Windows 使用（.ico），Linux 构建时 spec 会自动跳过，无需拷贝。

set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$Root"

DistDir="$Root/dist"
AppDir="$DistDir/qingci-bot-ce"

PY=""
if [ -f ".venv/bin/python" ]; then
  PY="$Root/.venv/bin/python"
fi
if [ -z "$PY" ]; then
  command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found; create .venv and install deps first" >&2; exit 1; }
  PY="$(command -v python3)"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found in PATH. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "Root:     $Root"
echo "Python:   $PY"

# ---------- [0/3] 独立插件 SDK ----------
# 与 build.bat 一致，锁定到已发布 tag，保证构建可复现。SDK 源默认走 Gitee 镜像
#（国内拉取更快，对齐 build.bat）；但 GitHub Actions（海外 IP）访问 Gitee 常被限流
#（HTTP 429），故支持 QINGCI_SDK_GIT_URL 覆盖为 GitHub 主仓库。SDK 发新版后同步
# 更新这里与 pyproject.toml 的 tag。
SDK_GIT_URL="${QINGCI_SDK_GIT_URL:-https://gitee.com/qingci-bot/Plugins-SDK.git}"
echo "==> [0/3] installing qingci-plugin-sdk (v1.13.7) from $SDK_GIT_URL ..."
uv pip install --python "$PY" "qingci-plugin-sdk @ git+${SDK_GIT_URL}@v1.13.7"

# ---------- 确保 pip 在 venv（供打包后插件依赖安装器使用） ----------
# 打包（frozen）模式下插件依赖安装器（bot/plugin/deps.py）调用 pip._internal，
# spec 通过 collect_all('pip') 收集；venv 缺 pip 会让 EXE 无法自动安装插件依赖。
echo "==> ensuring pip in build venv (for bundled plugin deps)..."
if ! "$PY" -c "import pip" >/dev/null 2>&1; then
  uv pip install --python "$PY" pip || echo "    WARNING: could not install pip; plugin auto-deps may be unavailable in packaged mode" >&2
fi
"$PY" -c "import pip; print('    pip available:', pip.__version__)"

# ---------- [1/3] PyInstaller build ----------
echo "==> [1/3] PyInstaller build (first run takes 3-10 min)..."
"$PY" -m PyInstaller --noconfirm --clean "$Root/qingci-bot-ce.spec"

# ---------- [2/3] copy Web UI ----------
echo "==> [2/3] copying Web UI (web/dist)..."
if [ -f "$Root/web/dist/index.html" ]; then
  mkdir -p "$AppDir/web"
  cp -R "$Root/web/dist" "$AppDir/web/dist"
  echo "    copied web/dist into output folder"
else
  echo "    WARNING: web/dist/index.html not found; run 'npm run build' in web/ first, /ui will be unavailable" >&2
fi

# ---------- [2.5/3] bundle Playwright browser ----------
# 内置一个 Chromium 到 dist/ms-playwright/，让 EXE 开箱即可渲染 HTML（签到卡），
# 无需最终用户再 `playwright install chromium`。运行时经 PLAYWRIGHT_BROWSERS_PATH
# 定位。下载策略：优先 npmmirror 镜像（国内快），回退官方 CDN。
echo "==> [2.5/3] bundling Playwright browser..."
if ! "$PY" -c "import playwright.async_api" >/dev/null 2>&1; then
  echo "    WARNING: playwright not importable; EXE will lack render capability, sign-in card degrades to text" >&2
else
  export PLAYWRIGHT_BROWSERS_PATH="$AppDir/ms-playwright"
  # 成功判定：目录里存在 chromium* 子目录，而非退出码（下载进度会污染退出码）
  test_browser_bundled() {
    [ -d "$PLAYWRIGHT_BROWSERS_PATH" ] && find "$PLAYWRIGHT_BROWSERS_PATH" -maxdepth 1 -type d -name 'chromium*' | grep -q .
  }
  attempts=0
  ok=false
  for host in \
      "https://npmmirror.com/mirrors/playwright" \
      "https://cdn.playwright.dev"; do
    [ "$ok" = true ] && break
    attempts=$((attempts + 1))
    echo "    downloading browsers from $host (try $attempts)..."
    # 先 only-shell（体积小、够渲染），失败再 full chromium（镜像可能滞后 headless-shell 版本而 404）
    export PLAYWRIGHT_DOWNLOAD_HOST="$host"
    if CI=1 "$PY" -m playwright install chromium --only-shell >/dev/null 2>&1 || \
       CI=1 "$PY" -m playwright install chromium --no-shell   >/dev/null 2>&1; then
      ok=true
    fi
  done
  if [ "$ok" = true ]; then
    test_browser_bundled && echo "    bundled Playwright browser -> $PLAYWRIGHT_BROWSERS_PATH" \
      || { echo "    WARNING: no chromium dir found after download; render capability unavailable" >&2; }
  else
    echo "    WARNING: all browser download attempts failed; render capability unavailable" >&2
  fi
fi

# ---------- [3/3] ensure output folder exists ----------
echo "==> [3/3] finished output folder..."
if [ ! -d "$AppDir" ]; then
  echo "ERROR: output folder missing: $AppDir" >&2
  exit 1
fi

# ---------- [3.5/3] SHA256 校验清单 ----------
echo "==> [3.5/3] generating SHA256 checksums..."
ExeFile="$AppDir/qingci-bot-ce"
Manifest="$DistDir/qingci-bot-ce.sha256"
if [ -f "$ExeFile" ]; then
  : > "$Manifest"
  exeHash="$(sha256sum "$ExeFile" | awk '{print $1}')"
  echo "qingci-bot-ce  $exeHash" >> "$Manifest"
  if [ -f "$AppDir/web/dist/index.html" ]; then
    webHash="$(sha256sum "$AppDir/web/dist/index.html" | awk '{print $1}')"
    echo "web/dist/index.html  $webHash" >> "$Manifest"
  fi
  echo "    SHA256 manifest -> $Manifest"
else
  echo "    WARNING: backend binary not found, skipping SHA256 manifest: $ExeFile" >&2
fi

echo ""
echo "Build finished: $AppDir"
echo "Run:"
echo "  ./dist/qingci-bot-ce/qingci-bot-ce                # Bot + API"
echo "  ./dist/qingci-bot-ce/qingci-bot-ce --no-bot       # API/Web UI only"
echo "  ./dist/qingci-bot-ce/qingci-bot-ce --port 8080    # custom port"
echo "Then open http://127.0.0.1:8080/ui/"
echo "Note: config/plugins/data live inside instances/<name>/; first launch auto-creates the default instance."
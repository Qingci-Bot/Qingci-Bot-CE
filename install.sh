#!/usr/bin/env bash
#
# Qingci-Bot CE Linux 一键安装脚本
#
# 用法（在项目根目录，即本文件同目录下运行）：
#   chmod +x install.sh
#   ./install.sh              # 创建 .venv 并安装核心运行依赖
#   ./install.sh --vector     # 额外安装向量知识库依赖（lancedb）
#   ./install.sh --with-gui   # 额外安装桌面 GUI 系统库（需用 --desktop 时）
#   ./install.sh --dev        # 额外安装测试/构建/代码质量工具
#
# 默认 Headless 模式（Bot + WebUI/API），无需任何 GUI 系统依赖。
# 装完启动：
#   .venv/bin/python main.py --instance default
# 然后浏览器访问 http://127.0.0.1:8080/ui

set -euo pipefail

# ── 参数解析 ──────────────────────────────────────────────
WITH_VECTOR=0
WITH_DEV=0
WITH_GUI=0
for arg in "$@"; do
  case "$arg" in
    --vector) WITH_VECTOR=1 ;;
    --with-gui) WITH_GUI=1 ;;
    --dev) WITH_DEV=1 ;;
    --help|-h)
      echo "用法: ./install.sh [--vector] [--with-gui] [--dev]"
      echo "  --vector    追加安装向量知识库依赖（lancedb）"
      echo "  --with-gui  追加安装桌面 GUI 系统库（桌面模式下才需要）"
      echo "  --dev       追加安装测试/构建/代码质量工具"
      exit 0
      ;;
    *) echo "未知参数: $arg（见 ./install.sh --help）" >&2; exit 1 ;;
  esac
done

# ── 必须且在项目根 ────────────────────────────────────────
if [ ! -f "pyproject.toml" ]; then
  echo "错误：请在 Qingci-Bot CE 项目根目录（含 pyproject.toml 与 main.py 的目录）运行本脚本" >&2
  exit 1
fi

# ── 工具检测 ──────────────────────────────────────────────
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少命令: $1"; return 1; }; }
for c in git python3; do
  need "$c" || { echo "请先安装: $c（见下方『系统依赖』）" >&2; exit 1; }
done

PY=
for py in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$py"; break
  fi
done
[ -n "$PY" ] || { echo "需要 Python >= 3.10，未找到可用解释器" >&2; exit 1; }
echo "使用 Python: $PY（$("$PY" --version)）"

# ── 系统依赖安装（可选，需 root）───────────────────────────
install_sys_deps() {
  local pkgs="git ca-certificates build-essential python3-venv python3-pip"
  if [ "$WITH_GUI" = "1" ]; then
    # 桌面模式：pywebview(GTK 后端) + pystray(libappindicator)
    pkgs="$pkgs libwebkit2gtk-4.1-0 libgtk-3-0 gir1.2-webkit2-4.1 gir1.2-gtk-3.0 libappindicator3-1"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    echo ">> 检测到 apt，安装系统依赖：$pkgs"
    apt-get update
    apt-get install -y $pkgs
  elif command -v dnf >/dev/null 2>&1; then
    # dnf 基础包与 apt 不同名（无 build-essential/python3-venv），用发行版精简集
    dnf_pkgs="git gcc python3-devel ca-certificates"
    if [ "$WITH_GUI" = "1" ]; then
      # --with-gui：补装桌面模式（pywebview GTK / pystray）所需系统库
      dnf_pkgs="$dnf_pkgs webkit2gtk4.1 gtk3 gir1.2-webkit2-4.1 libappindicator-gtk3"
    fi
    echo ">> 检测到 dnf，安装系统依赖：$dnf_pkgs"
    dnf install -y $dnf_pkgs
  elif command -v apk >/dev/null 2>&1; then
    apk_pkgs="git gcc musl-dev python3-dev ca-certificates"
    if [ "$WITH_GUI" = "1" ]; then
      # Alpine：--with-gui 时补装 pywebview GTK / pystray 依赖
      apk_pkgs="$apk_pkgs py3-gobject3 py3-cairo gtk+3.0 webkit2gtk libappindicator"
    fi
    echo ">> 检测到 apk，安装系统依赖：$apk_pkgs"
    apk add --no-cache $apk_pkgs
  else
    echo ">> 未识别的包管理器，跳过系统依赖自动安装；请按发行版文档手工安装：$pkgs"
  fi
}
# 默认尝试自动安装系统依赖；设 SKIP_SYS_DEPS=1 可跳过
if [ "${SKIP_SYS_DEPS:-0}" != "1" ]; then
  if [ "$(id -u)" = "0" ]; then
    install_sys_deps
  elif command -v sudo >/dev/null 2>&1; then
    echo ">> 需要 root 权限安装系统依赖，尝试 sudo..."
    # declare -f 只序列化函数体，WITH_GUI 等变量需显式传入子 shell
    sudo bash -c "$(declare -f install_sys_deps); WITH_GUI=${WITH_GUI:-0}; install_sys_deps"
  else
    echo ">> 非 root 且无 sudo，跳过系统依赖自动安装；如运行报缺库请手工安装（见 README『Linux 部署』）"
  fi
fi

# ── 创建虚拟环境 ──────────────────────────────────────────
ENVDIR=".venv"
if [ ! -d "$ENVDIR" ]; then
  echo ">> 创建虚拟环境: $ENVDIR"
  "$PY" -m venv "$ENVDIR"
fi
VENV_PY="$ENVDIR/bin/python"

# ── 安装依赖（优先 uv，否则 pip）───────────────────────────
extras=""
[ "$WITH_VECTOR" = "1" ] && extras="$extras,vector"
# GUI 系统库已由上方 install_sys_deps 安装，无需 Python extra（pywebview/pystray 在核心依赖里）
[ "$WITH_DEV" = "1" ] && extras="$extras,dev"
if [ -n "$extras" ]; then
  EXTRAS_STR="${extras#,}"   # 去掉前导逗号
  PKG_SPEC=".[$EXTRAS_STR]"
else
  PKG_SPEC="."
fi

if command -v uv >/dev/null 2>&1; then
  echo ">> 使用 uv 安装依赖: uv pip install -e \"$PKG_SPEC\""
  UV=$(command -v uv)
  "$UV" pip install -e "$PKG_SPEC" --python "$VENV_PY"
else
  echo ">> 使用 pip 安装依赖: $VENV_PY -m pip install -e \"$PKG_SPEC\""
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install -e "$PKG_SPEC"
fi

# ── 提示下一步 ────────────────────────────────────────────
cat <<EOF

✅ 安装完成。
启动 Bot + WebUI（首次启动会自动创建 default 实例及默认 config.yaml）：
    $ENVDIR/bin/python main.py --instance default

- 浏览器访问 http://127.0.0.1:8080/ui
- 编辑实例配置：vim instances/default/config.yaml（如 LLM api_key、OneBot host 等）
- 外部 OneBot 前端（NapCat/LLBot）连入前，请将实例 config.yaml 的 onebot.host 改为 0.0.0.0
- 更省心的部署方式见 Docker：docker compose up -d
EOF
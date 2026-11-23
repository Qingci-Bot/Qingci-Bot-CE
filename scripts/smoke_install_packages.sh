#!/usr/bin/env bash
# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE
#
# 分发包安装冒烟：在目标发行版容器里真实安装 deb / rpm，验证
#   「装得上 → 布局对 → 能跑起来 → 卸得干净且保留数据」。
#
# 为什么需要：release 流水线此前只用 dpkg-deb -f / rpm -qp 读元数据，
# 证明的是「包名和版本写对了」，证明不了「用户能不能装上」。缺依赖、
# postinst 失败、onedir 权限位丢失、systemd 单元路径写错——这些都只有
# 真正 install 之后才会暴露。
#
# 用法：
#   scripts/smoke_install_packages.sh [包目录] [--only deb|rpm]
#
# 环境变量：
#   SMOKE_ENGINE   docker（默认）或 podman
#   UBUNTU_IMAGE   默认 ubuntu:22.04——构建基线，也是 deb 的目标下限
#   FEDORA_IMAGE   默认 fedora:41（rpm 侧取在用版本；更老的 Fedora 已 EOL，
#                  dnf 源不可用，会因基础设施而非兼容性失败）
#
# 镜像应与构建基线**同代**：构建在 ubuntu-22.04 上做，这里就测 22.04。
# 测系统比构建系统新，等于放过"在老系统上跑不起来"的问题（1.22.0 就是这么
# 漏掉的：在 24.04 构建、只在 24.04 验证，22.04/Debian 12 全挂）。
# 详见 docs/PACKAGING.md 的「构建机 glibc 决定运行时下限」。
#
# 覆盖的断言：
#   服务端包：/opt 下的冻结二进制可执行、系统用户已创建、systemd 单元就位、
#             `--help` 退出码 0、headless 启动后 /api/bot/health 返回 200、
#             卸载后 /opt 清空而 /var/lib 实例数据保留
#   桌面包  ：/opt/Qingci-Bot CE 下的可执行文件、/usr/bin 软链可解析、
#             .desktop 条目就位、卸载后 /opt 清空
#             （不执行 GUI：容器内无 X server，启动必然失败，断言没有意义）
set -euo pipefail

PKG_DIR="dist/linux"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
  PKG_DIR="$1"
  shift
fi
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --only)
      ONLY="${2:-}"
      shift 2
      ;;
    *)
      echo "[FAIL] 未知参数: $1" >&2
      exit 2
      ;;
  esac
done

ENGINE="${SMOKE_ENGINE:-docker}"
UBUNTU_IMAGE="${UBUNTU_IMAGE:-ubuntu:22.04}"
FEDORA_IMAGE="${FEDORA_IMAGE:-fedora:41}"
SERVER_PKG="qingci-bot-ce-server"
DESKTOP_PKG="qingci-bot-ce"
INSTALL_DIR_SERVER="/opt/${SERVER_PKG}"
INSTALL_DIR_DESKTOP="/opt/Qingci-Bot CE"

# SELinux enforcing 的主机（Fedora / RHEL）上，:ro 只读 bind mount 会因标签
# 不匹配被拒（Permission denied），需为该容器关闭标签隔离；Ubuntu runner 无
# SELinux，不传这个参数，避免不同引擎对未知 opt 的兼容性差异。
SEC_ARGS=()
if [ -d /sys/fs/selinux ]; then
  SEC_ARGS=(--security-opt label=disable)
fi

if ! command -v "$ENGINE" >/dev/null 2>&1; then
  echo "[FAIL] 找不到容器引擎: $ENGINE（设置 SMOKE_ENGINE=docker 或 podman）" >&2
  exit 2
fi
if [ ! -d "$PKG_DIR" ]; then
  echo "[FAIL] 包目录不存在: $PKG_DIR" >&2
  exit 2
fi

# 容器内执行的断言脚本：参数 $1=包文件绝对路径(容器内) $2=deb|rpm $3=server|desktop
read -r -d '' INNER <<'INNER_SCRIPT' || true
set -euo pipefail
pkg="$1"
fmt="$2"
kind="$3"

say() { echo "    [$kind] $*"; }

install_pkg() {
  if [ "$fmt" = "deb" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    # 用 apt 而非 dpkg -i：让依赖解析真正走一遍（缺依赖就是要在这里失败）
    apt-get install -y -qq "$pkg"
  else
    dnf install -y -q "$pkg"
  fi
}

remove_pkg() {
  if [ "$fmt" = "deb" ]; then
    apt-get remove -y -qq "$1"
  else
    dnf remove -y -q "$1"
  fi
}

if [ "$kind" = "server" ]; then
  install_pkg
  say "已安装"

  test -x "/opt/qingci-bot-ce-server/qingci-bot-ce" \
    || { echo "    [FAIL] 冻结二进制不存在或不可执行"; exit 1; }
  say "二进制可执行位 OK"

  id qingci-bot >/dev/null 2>&1 \
    || { echo "    [FAIL] 系统用户 qingci-bot 未创建"; exit 1; }
  say "系统用户 qingci-bot OK"

  unit=""
  for candidate in /etc/systemd/system/qingci-bot-ce.service /usr/lib/systemd/system/qingci-bot-ce.service; do
    if [ -f "$candidate" ]; then unit="$candidate"; break; fi
  done
  [ -n "$unit" ] || { echo "    [FAIL] systemd 单元未安装"; exit 1; }
  grep -q "/opt/qingci-bot-ce-server/qingci-bot-ce" "$unit" \
    || { echo "    [FAIL] 单元 ExecStart 路径与安装目录不一致"; grep -n "ExecStart" "$unit"; exit 1; }
  say "systemd 单元 OK ($unit)"

  test -d /var/lib/qingci-bot-ce-server \
    || { echo "    [FAIL] 实例数据目录不存在"; exit 1; }
  test -d /var/log/qingci-bot-ce-server \
    || { echo "    [FAIL] 日志目录不存在"; exit 1; }
  say "数据/日志目录 OK"

  if ! command -v curl >/dev/null 2>&1; then
    if [ "$fmt" = "deb" ]; then apt-get install -y -qq curl; else dnf install -y -q curl; fi
  fi

  "/opt/qingci-bot-ce-server/qingci-bot-ce" --help >/dev/null 2>&1 \
    || { echo "    [FAIL] --help 执行失败（冻结产物可能缺库）"; exit 1; }
  say "--help 退出码 0"

  # 真启动：按 systemd 单元的方式跑（QINGCI_DATA_HOME 指向可写目录）
  work="$(mktemp -d)"
  export QINGCI_DATA_HOME="$work" HOME="$work"
  "/opt/qingci-bot-ce-server/qingci-bot-ce" --host 127.0.0.1 --port 18080 \
    >"$work/boot.log" 2>&1 &
  pid=$!
  ok=0
  for _ in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then break; fi
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:18080/api/bot/health || true)"
    if [ "$code" = "200" ]; then ok=1; break; fi
    sleep 1
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  if [ "$ok" != "1" ]; then
    echo "    [FAIL] headless 启动后 /api/bot/health 未就绪（60s）"
    tail -30 "$work/boot.log" || true
    exit 1
  fi
  say "headless 启动 + /api/bot/health 200 OK"

  # 模拟真实用户数据：实例目录由运行时创建，不归 dpkg/rpm 所有。
  # 注意包内自带 README.txt 与空 instances/，remove 时清掉它们是正常的
  # （dpkg 只删自己拥有的文件与变空的目录），所以断言必须落在我们预置的文件上。
  probe_dir=/var/lib/qingci-bot-ce-server/instances/default
  mkdir -p "$probe_dir"
  echo '{"probe": 1}' > "$probe_dir/config.yaml"

  remove_pkg qingci-bot-ce-server
  test ! -e /opt/qingci-bot-ce-server/qingci-bot-ce \
    || { echo "    [FAIL] 卸载后二进制仍在 /opt/qingci-bot-ce-server"; exit 1; }
  test -f "$probe_dir/config.yaml" \
    || { echo "    [FAIL] 卸载删掉了用户实例数据（应保留到 purge）"; exit 1; }
  say "卸载保留实例数据 OK"

  # purge（仅 deb 有该语义）才允许清 /var/lib；rpm 无 purge，移除即最终删除
  if [ "$fmt" = "deb" ]; then
    apt-get purge -y -qq qingci-bot-ce-server
    test ! -e "$probe_dir/config.yaml" \
      || { echo "    [FAIL] purge 后实例数据仍在（应清空）"; exit 1; }
    say "purge 清理数据 OK"
  fi
else
  install_pkg
  say "已安装"

  test -x "/opt/Qingci-Bot CE/qingci-bot-ce" \
    || { echo "    [FAIL] 桌面可执行文件不存在或不可执行"; ls -l "/opt/Qingci-Bot CE" | head; exit 1; }
  say "可执行文件 OK"

  test -L /usr/bin/qingci-bot-ce \
    || { echo "    [FAIL] /usr/bin/qingci-bot-ce 软链缺失"; exit 1; }
  test -e /usr/bin/qingci-bot-ce \
    || { echo "    [FAIL] /usr/bin/qingci-bot-ce 软链断链"; exit 1; }
  say "/usr/bin 软链 OK"

  ls /usr/share/applications/qingci-bot-ce.desktop >/dev/null 2>&1 \
    || { echo "    [FAIL] .desktop 条目缺失"; ls /usr/share/applications | grep -i qingci || true; exit 1; }
  say ".desktop 条目 OK"

  test -f "/opt/Qingci-Bot CE/resources/backend.zip" \
    || { echo "    [FAIL] 内嵌后端归档缺失"; exit 1; }
  say "内嵌 backend.zip OK"

  # 桌面壳本身在容器里起不来（无 X server），但它内嵌的后端是纯 CLI：
  # 解压后跑 --help 能验证「冻结产物在该发行版上可加载」——这正是 glibc
  # 版本错配（如 libstdc++ 需要 GLIBC_2.38）最早暴露的地方。不测这一步，
  # 桌面包会假绿：装得上、桌面壳一启动就拉不起后端。
  if ! command -v unzip >/dev/null 2>&1; then
    if [ "$fmt" = "deb" ]; then apt-get install -y -qq unzip; else dnf install -y -q unzip; fi
  fi
  bwork="$(mktemp -d)"
  unzip -q "/opt/Qingci-Bot CE/resources/backend.zip" -d "$bwork" \
    || { echo "    [FAIL] backend.zip 解压失败"; exit 1; }
  backend_bin="$(find "$bwork" -maxdepth 2 -type f -name 'qingci-bot-ce' -perm -u+x | head -1)"
  [ -n "$backend_bin" ] || { echo "    [FAIL] backend.zip 内未找到可执行主程序"; ls "$bwork" | head; exit 1; }
  if ! "$backend_bin" --help >/dev/null 2>&1; then
    echo "    [FAIL] 内嵌后端无法运行（缺库或 glibc 版本不兼容）"
    "$backend_bin" --help 2>&1 | tail -5
    exit 1
  fi
  say "内嵌后端可执行 OK"

  remove_pkg qingci-bot-ce
  test ! -e "/opt/Qingci-Bot CE/qingci-bot-ce" \
    || { echo "    [FAIL] 卸载后可执行文件仍在 /opt/Qingci-Bot CE"; exit 1; }
  say "卸载清理 OK"
fi
INNER_SCRIPT

run_case() {
  local pkg="$1" fmt="$2" kind="$3" image="$4"
  local name pkgdir
  name="$(basename "$pkg")"
  # 挂目录而非单文件：apt/dnf 要求文件名带 .deb/.rpm 扩展名才会当本地包处理
  pkgdir="$(cd "$(dirname "$pkg")" && pwd)"
  echo ""
  echo "==> [$fmt/$kind] $name  ($image)"
  "$ENGINE" run --rm "${SEC_ARGS[@]}" \
    -v "${pkgdir}:/pkgs:ro" \
    "$image" \
    bash -c "$INNER" _ "/pkgs/${name}" "$fmt" "$kind" \
    || {
      echo "[FAIL] $name 冒烟未通过" >&2
      exit 1
    }
  echo "    OK: $name"
}

count=0
for deb in "$PKG_DIR"/*.deb; do
  [ -e "$deb" ] || continue
  [ -z "$ONLY" ] || [ "$ONLY" = "deb" ] || continue
  case "$(basename "$deb")" in
    "${SERVER_PKG}"_*) run_case "$deb" deb server "$UBUNTU_IMAGE"; count=$((count + 1)) ;;
    "${DESKTOP_PKG}"_*) run_case "$deb" deb desktop "$UBUNTU_IMAGE"; count=$((count + 1)) ;;
    *) echo "[WARN] 跳过未识别的 deb: $deb" ;;
  esac
done

for rpmf in "$PKG_DIR"/*.rpm; do
  [ -e "$rpmf" ] || continue
  [ -z "$ONLY" ] || [ "$ONLY" = "rpm" ] || continue
  case "$(basename "$rpmf")" in
    "${SERVER_PKG}"-*) run_case "$rpmf" rpm server "$FEDORA_IMAGE"; count=$((count + 1)) ;;
    "${DESKTOP_PKG}"-*) run_case "$rpmf" rpm desktop "$FEDORA_IMAGE"; count=$((count + 1)) ;;
    *) echo "[WARN] 跳过未识别的 rpm: $rpmf" ;;
  esac
done

if [ "$count" -eq 0 ]; then
  echo "[FAIL] $PKG_DIR 下没有找到可冒烟的分发包" >&2
  exit 1
fi

echo ""
echo "全部通过：$count 个分发包安装/启动/卸载冒烟"

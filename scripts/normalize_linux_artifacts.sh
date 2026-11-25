#!/usr/bin/env bash
# 统一 Linux 分发包的文件名：<包名>-<版本>-linux-<架构>.<deb|rpm>
#
# 为什么需要这一步：electron-builder 的 deb 会用 build.linux.artifactName，
# 而 rpm 在部分版本上仍按 fpm 默认命名输出（形如
# qingci-bot-ce-1.23.0-1.x86_64.rpm，既没有平台标识、架构词也与 deb 不一致）。
# 与其和打包器内部的命名规则纠缠，不如在产物出来后统一重命名一次：
# 规则集中在这一个脚本里，发布页上的每个文件都能一眼看出平台与架构。
#
# 用法: normalize_linux_artifacts.sh <目录> <版本> <架构标识>
#   架构标识: x64 | arm64 | armv7
#
# 幂等：已经是新命名的文件会被跳过，重复执行不会叠加后缀。
set -euo pipefail

Dir="${1:-}"
Version="${2:-}"
Token="${3:-}"

[ -n "$Dir" ] || { echo "ERROR: 缺少目录参数" >&2; exit 2; }
[ -n "$Version" ] || { echo "ERROR: 缺少版本参数" >&2; exit 2; }
[ -n "$Token" ] || { echo "ERROR: 缺少架构标识（x64 / arm64 / armv7）" >&2; exit 2; }
[ -d "$Dir" ] || { echo "ERROR: 目录不存在: $Dir" >&2; exit 2; }

# 版本号统一不带 v 前缀（与包内版本一致）
Version="${Version#v}"

shopt -s nullglob
files=("$Dir"/qingci-bot-ce*.deb "$Dir"/qingci-bot-ce*.rpm)
if [ ${#files[@]} -eq 0 ]; then
  echo "    无 Linux 包需要重命名: $Dir"
  exit 0
fi

for src in "${files[@]}"; do
  base="$(basename "$src")"
  ext="${base##*.}"

  # 已是目标命名（含平台与架构标识）则跳过
  case "$base" in
    *-linux-"$Token"."$ext") continue ;;
  esac

  # 服务端包与桌面包前缀不同，必须保留——否则两者会互相覆盖
  case "$base" in
    qingci-bot-ce-server*) prefix="qingci-bot-ce-server" ;;
    *) prefix="qingci-bot-ce" ;;
  esac

  target="$Dir/${prefix}-${Version}-linux-${Token}.${ext}"
  if [ "$src" = "$target" ]; then
    continue
  fi
  if [ -e "$target" ]; then
    echo "ERROR: 目标文件已存在，拒绝覆盖: $target" >&2
    exit 1
  fi
  mv -f "$src" "$target"
  echo "    renamed: $base -> $(basename "$target")"
done

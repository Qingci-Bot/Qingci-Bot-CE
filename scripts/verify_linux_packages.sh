#!/usr/bin/env bash
# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE
#
# 分发包元数据校验：文件名 / 包名 / 版本 / 架构四者必须与本次发布一致。
#
# 为什么单独成脚本：x64（linux-deb-rpm）与 arm64（linux-arm64）两条构建链
# 要跑完全相同的断言。此前这段逻辑内联在 workflow 里且写死 amd64 / x86_64，
# 加 arm64 时只能复制一份并改两行——迟早漂移。架构改为参数传入。
#
# 用法：
#   scripts/verify_linux_packages.sh <包目录> <版本号(不带 v)> <期望 deb 架构> <期望 rpm 架构>
#   scripts/verify_linux_packages.sh dist/linux 1.22.4-rc1 amd64 x86_64
#   scripts/verify_linux_packages.sh dist/linux 1.22.4-rc1 arm64 aarch64
#
# 期望数量固定为 2 deb + 2 rpm（桌面版 + 服务端版）。
set -euo pipefail

PKG_DIR="${1:-dist/linux}"
VER_IN="${2:-}"
EXPECT_DEB_ARCH="${3:-}"
EXPECT_RPM_ARCH="${4:-}"

[ -n "$VER_IN" ] && [ -n "$EXPECT_DEB_ARCH" ] && [ -n "$EXPECT_RPM_ARCH" ] || {
  echo "用法: $0 <包目录> <版本号(不带 v)> <期望 deb 架构> <期望 rpm 架构>" >&2
  exit 2
}
[ -d "$PKG_DIR" ] || { echo "ERROR: 包目录不存在: $PKG_DIR" >&2; exit 1; }

ver="${VER_IN#v}"
# 预发布：deb/rpm 元数据把 -rc 写成 ~rc（排序在正式版之前），
# 文件名可能两种写法都出现（desktop 取 appInfo.version，server 脚本转 ~）。
ver_pkg="${ver//-rc/~rc}"

echo "== $PKG_DIR (期望架构: deb=$EXPECT_DEB_ARCH rpm=$EXPECT_RPM_ARCH) =="
ls -l "$PKG_DIR"

deb_count=$(ls "$PKG_DIR"/*.deb 2>/dev/null | wc -l)
rpm_count=$(ls "$PKG_DIR"/*.rpm 2>/dev/null | wc -l)
echo "deb=$deb_count rpm=$rpm_count"
[ "$deb_count" -eq 2 ] || { echo "::error::期望 2 个 deb，实际 $deb_count"; exit 1; }
[ "$rpm_count" -eq 2 ] || { echo "::error::期望 2 个 rpm，实际 $rpm_count"; exit 1; }

for deb in "$PKG_DIR"/*.deb; do
  base="$(basename "$deb")"
  pkg="$(dpkg-deb -f "$deb" Package)"
  dver="$(dpkg-deb -f "$deb" Version)"
  darch="$(dpkg-deb -f "$deb" Architecture)"
  echo "deb: $base -> Package=$pkg Version=$dver Arch=$darch"
  echo "     安装路径: $(dpkg-deb -c "$deb" | grep -o '\./opt/[^/]*/' | sort -u | tr '\n' ' ')"
  base_norm="${base//\~/-}"
  case "$base_norm" in
    "qingci-bot-ce_${ver}_${darch}.deb"|"qingci-bot-ce-server_${ver}_${darch}.deb") ;;
    *) echo "::error::deb 文件名不符合 <包名>_<版本>_<架构>.deb 约定: $base"; exit 1 ;;
  esac
  { [ "$dver" = "$ver" ] || [ "$dver" = "$ver_pkg" ]; } \
    || { echo "::error::deb 版本与 tag 不一致: $base ($dver != $ver / $ver_pkg)"; exit 1; }
  [ "$darch" = "$EXPECT_DEB_ARCH" ] \
    || { echo "::error::deb 架构非 $EXPECT_DEB_ARCH: $base ($darch)"; exit 1; }
done

for rpmf in "$PKG_DIR"/*.rpm; do
  base="$(basename "$rpmf")"
  name="$(rpm -qp --qf '%{NAME}' "$rpmf")"
  rver="$(rpm -qp --qf '%{VERSION}' "$rpmf")"
  rrel="$(rpm -qp --qf '%{RELEASE}' "$rpmf")"
  rarch="$(rpm -qp --qf '%{ARCH}' "$rpmf")"
  echo "rpm: $base -> Name=$name Version=$rver Release=$rrel Arch=$rarch"
  base_norm="${base//\~/-}"
  rver_norm="${rver//\~/-}"
  case "$base_norm" in
    "qingci-bot-ce-${rver_norm}-${rrel}.${rarch}.rpm"|"qingci-bot-ce-server-${rver_norm}-${rrel}.${rarch}.rpm") ;;
    *) echo "::error::rpm 文件名不符合 <包名>-<版本>-<release>.<架构>.rpm 约定: $base"; exit 1 ;;
  esac
  { [ "$rver" = "$ver" ] || [ "$rver" = "$ver_pkg" ]; } \
    || { echo "::error::rpm 版本与 tag 不一致: $base ($rver != $ver / $ver_pkg)"; exit 1; }
  [ "$rarch" = "$EXPECT_RPM_ARCH" ] \
    || { echo "::error::rpm 架构非 $EXPECT_RPM_ARCH: $base ($rarch)"; exit 1; }
done
echo "包名与元数据校验通过（${EXPECT_DEB_ARCH} / ${EXPECT_RPM_ARCH}）"

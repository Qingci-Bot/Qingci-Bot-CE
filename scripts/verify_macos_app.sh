#!/usr/bin/env bash
# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE
#
# macOS（Apple Silicon）分发包校验：架构 / 签名 / bundle 元信息 / 内嵌后端可运行。
#
# 为什么这四项必须逐条断言，而不是「能打出 dmg 就算成功」：
#
#  1. **架构**：本项目只发布 arm64（Intel Mac 走 Rosetta 也不受支持）。
#     若某天 x64 产物混进来，用户装得上却跑不动。
#  2. **签名**：Apple Silicon 对 arm64 原生代码强制要求有效签名，与 Gatekeeper
#     无关。未签名（或签名被后续步骤破坏）的包，用户双击只会看到
#     「已损坏，无法打开」——而 CI 里"构建成功"看起来一切正常。
#     本项目没有 Apple Developer 证书，走 ad-hoc 签名，因此这里断言
#     `Signature=adhoc` 且 `codesign --verify` 通过。
#  3. **bundle 元信息**：bundle id 必须与 appId 一致（否则 macOS 会按不同应用
#     对待，导致权限/数据目录错乱）；最低系统版本必须与 arm64 起点一致。
#  4. **内嵌后端可运行**：桌面壳只是壳，真正的 bot 在 resources/backend.zip 里。
#     把 zip 解开跑 `--help`，才能证明「用户装完真的能启动」——元数据和架构
#     全对但 dylib 缺失/签名失效，前面三项都查不出来。
#
# 用法：
#   bash scripts/verify_macos_app.sh <产物目录> <版本号(不带 v)>
#   bash scripts/verify_macos_app.sh dist/electron 1.23.0-rc1
#
# 依赖：macOS 自带 file / codesign / plutil / unzip / lipo。
set -euo pipefail

DIST_DIR="${1:-dist/electron}"
VER_IN="${2:-}"
APP_NAME="Qingci-Bot CE"
EXPECT_BUNDLE_ID="com.qingcibot.ce"
EXPECT_MIN_MACOS="11.0"

[ -n "$VER_IN" ] || {
  echo "用法: $0 <产物目录> <版本号(不带 v)>" >&2
  exit 2
}
[ -d "$DIST_DIR" ] || { echo "ERROR: 产物目录不存在: $DIST_DIR" >&2; exit 1; }

ver="${VER_IN#v}"
fail() { echo "  [FAIL] $*" >&2; exit 1; }
ok() { echo "  [OK] $*"; }

echo "== macOS 产物校验: $DIST_DIR (version=$ver) =="
ls -l "$DIST_DIR" || true

# ---------- 1. 产物数量与命名 ----------
dmg="$DIST_DIR/qingci-bot-ce-${ver}-arm64.dmg"
zip="$DIST_DIR/qingci-bot-ce-${ver}-arm64.zip"
[ -f "$dmg" ] || fail "缺少 dmg: $(basename "$dmg")（命名应为 qingci-bot-ce-<ver>-arm64.dmg）"
[ -f "$zip" ] || fail "缺少 zip: $(basename "$zip")"
ok "dmg / zip 命名与版本一致"

# ---------- 2. app bundle ----------
APP="$DIST_DIR/mac-arm64/${APP_NAME}.app"
[ -d "$APP" ] || fail "未找到 app bundle: $APP"
EXE="$APP/Contents/MacOS/${APP_NAME}"
[ -f "$EXE" ] || fail "app 内缺少主可执行文件: $EXE"
ok "app bundle 结构完整"

# ---------- 3. 架构：只允许 arm64 ----------
archs="$(lipo -archs "$EXE" 2>/dev/null || true)"
[ -n "$archs" ] || fail "无法读取 Mach-O 架构（不是有效二进制？）"
echo "  主程序架构: $archs"
case "$archs" in
  *x86_64*) fail "产物含 x86_64 切片，本项目只发布 Apple Silicon（arm64）" ;;
esac
case "$archs" in
  *arm64*) ok "主程序为 arm64" ;;
  *) fail "主程序不含 arm64 切片: $archs" ;;
esac
file "$EXE" | grep -q "Mach-O" || fail "主程序不是 Mach-O"
ok "Mach-O 格式正确"

# ---------- 4. 签名：ad-hoc 且可验证 ----------
# Apple Silicon 上未签名的 arm64 应用会被内核直接拒绝，用户看到「已损坏」。
sig_info="$(codesign -dv "$APP" 2>&1 || true)"
echo "$sig_info" | grep -q "Signature=adhoc" \
  || fail "未使用 ad-hoc 签名（Apple Silicon 会拒绝启动）: $(echo "$sig_info" | head -3)"
codesign --verify --deep --strict "$APP" 2>/dev/null \
  || fail "codesign --verify --deep --strict 失败（签名无效或已被后续步骤破坏）"
ok "ad-hoc 签名有效（ad-hoc 免证书，用户首次打开需右键「打开」）"

# ---------- 5. bundle 元信息 ----------
plist="$APP/Contents/Info.plist"
[ -f "$plist" ] || fail "缺少 Info.plist"
bid="$(plutil -extract CFBundleIdentifier raw "$plist" 2>/dev/null || true)"
[ "$bid" = "$EXPECT_BUNDLE_ID" ] || fail "CFBundleIdentifier=${bid}，期望 $EXPECT_BUNDLE_ID"
minsys="$(plutil -extract LSMinimumSystemVersion raw "$plist" 2>/dev/null || true)"
[ "$minsys" = "$EXPECT_MIN_MACOS" ] || fail "LSMinimumSystemVersion=${minsys}，期望 $EXPECT_MIN_MACOS"
ok "bundle id / 最低系统版本正确（$bid / macOS $minsys+）"

# ---------- 5.1 声明的最低系统版本不得低于二进制真实下限 ----------
# 构建跑在 macOS 15 上，但我们的兼容目标是 macOS 11+。若二进制实际的
# LC_BUILD_VERSION/minos 高于声明值，旧系统用户会「装得上、一开就崩」，
# 表现与签名失效几乎一样（都是「应用已损坏」），很难区分。
# 这里读回真实 minos，与声明值比对：声明必须 >= 真实值，否则直接失败。
# 版本号比较：v1 >= v2 时返回 0
#
# 特意不用 `sort -V`：那是 GNU coreutils 的扩展，macOS 自带的 BSD sort
# 不认这个选项（会直接报 illegal option），而这个脚本只在 macOS 上跑。
# awk 到处都有，按点分段做数值比较即可（macOS 版本号最多三段）。
version_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    n = split(a, x, "."); m = split(b, y, ".")
    for (i = 1; i <= 3; i++) {
      p = (i <= n) ? x[i] + 0 : 0
      q = (i <= m) ? y[i] + 0 : 0
      if (p > q) exit 0
      if (p < q) exit 1
    }
    exit 0
  }'
}

minos="$(vtool -show-build "$EXE" 2>/dev/null | awk '/minos/{print $2; exit}')"
if [ -n "$minos" ]; then
  echo "  主程序真实 minos: ${minos}（声明 ${minsys}）"
  version_ge "${minsys}" "${minos}" || fail "声明的最低系统 ${minsys} 低于二进制的真实下限 ${minos}（旧系统会一开就崩）——请把 package.json 的 mac.minimumSystemVersion 调到 ${minos} 或更高"
  ok "最低系统版本声明不低于二进制真实下限"
else
  echo "  [WARN] 无法读取 minos（vtool 不可用），跳过该断言"
fi

# ---------- 6. 内嵌后端 + 真跑一次 ----------
backend_zip="$APP/Contents/Resources/backend.zip"
[ -f "$backend_zip" ] || fail "app 内缺少 resources/backend.zip（桌面壳拉不起后端）"
ok "内嵌 backend.zip 存在"

work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

unzip -q "$backend_zip" -d "$work" || fail "backend.zip 解压失败"
backend_bin="$work/qingci-bot-ce"
[ -f "$backend_bin" ] || fail "backend.zip 内未找到主程序 qingci-bot-ce"
chmod +x "$backend_bin"
# 冻结产物的可执行位依赖 zip 权限位还原（桌面壳 ensureBackendExecutable 做同一件事），
# 这里手动补位以验证「解压后能跑」这一最终效果。
"$backend_bin" --help >/dev/null 2>&1 \
  || { echo "  ---- backend --help 输出 ----" >&2; "$backend_bin" --help 2>&1 | tail -20; \
       fail "内嵌后端 --help 执行失败（冻结产物缺库或签名失效）"; }
ok "内嵌后端 --help 退出码 0（arm64 原生可运行）"

# ---------- 6.1 后端自身的架构与最低系统版本 ----------
# 前面查的是 Electron 外壳；真正随项目构建、决定「哪些 macOS 能跑我们的 bot」
# 的是这个后端二进制。两者取更高者，才是整个包的真实系统下限。
backend_archs="$(lipo -archs "$backend_bin" 2>/dev/null || true)"
echo "  后端架构: ${backend_archs}"
case "$backend_archs" in
  *x86_64*) fail "内嵌后端含 x86_64 切片（本项目只发布 Apple Silicon）" ;;
  *arm64*) ok "内嵌后端为 arm64" ;;
  *) fail "内嵌后端不含 arm64 切片: ${backend_archs}" ;;
esac

backend_minos="$(vtool -show-build "$backend_bin" 2>/dev/null | awk '/minos/{print $2; exit}')"
if [ -n "$backend_minos" ]; then
  echo "  后端真实 minos: ${backend_minos}"
  version_ge "${minsys}" "${backend_minos}" \
    || fail "声明的最低系统 ${minsys} 低于后端的真实下限 ${backend_minos}——请把 package.json 的 mac.minimumSystemVersion 调到 ${backend_minos} 或更高"
  ok "最低系统版本声明不低于后端真实下限"
fi

echo "== 全部通过 =="

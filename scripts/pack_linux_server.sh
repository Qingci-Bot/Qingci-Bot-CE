#!/usr/bin/env bash
# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE
#
# Qingci-Bot CE 服务端包构建（deb / rpm，headless + systemd）
#
# 与桌面版的区别：不打包 Electron 壳，只装 PyInstaller 后端 onedir，装到
# $PREFIX/qingci-bot-ce-server，并由 systemd 托管为长驻服务。适用于无桌面
# 环境的服务器（Fedora / RHEL / Ubuntu Server / Debian 等）。
#
# 前置：先跑过 build-linux.sh 产出 dist/qingci-bot-ce/（后端 onedir）。
#
# 用法：
#   bash scripts/pack_linux_server.sh --version 1.20.0
#   bash scripts/pack_linux_server.sh --version 1.20.0 --only deb   # 只出 deb
#   bash scripts/pack_linux_server.sh --prefix /usr/lib/qingci-bot-ce-server
#
# 产物：dist/linux/Qingci-Bot-CE-server-<version>-<arch>.deb / .rpm
#
# 实现说明：不引入 fpm，直接用 dpkg-deb / rpmbuild 原生打包，减少构建机依赖。
# 构建机缺哪个工具就跳过哪种格式（除非用 --only 显式指定）。

set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$Root"

# ── 参数 ──────────────────────────────────────────────────
VERSION=""
ONLY=""
PREFIX="/opt"
PKG_NAME="qingci-bot-ce-server"
BUNDLE_DIR="$Root/dist/qingci-bot-ce"
OUT_DIR="$Root/dist/linux"

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --only)    ONLY="${2:-}"; shift 2 ;;
    --prefix)  PREFIX="${2:-}"; shift 2 ;;
    --bundle)  BUNDLE_DIR="${2:-}"; shift 2 ;;
    --out)     OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) sed -n '4,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1（见 --help）" >&2; exit 1 ;;
  esac
done

if [ -z "$VERSION" ]; then
  # 未显式给出时从 pyproject.toml 读取（单行 version = "x.y.z"）
  VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -n1)"
fi
[ -n "$VERSION" ] || { echo "ERROR: 无法确定版本号，请用 --version 指定" >&2; exit 1; }

[ -d "$BUNDLE_DIR" ] || {
  echo "ERROR: 后端 onedir 不存在: $BUNDLE_DIR" >&2
  echo "       先执行 ./build-linux.sh 产出后端" >&2
  exit 1
}
[ -x "$BUNDLE_DIR/qingci-bot-ce" ] || {
  echo "ERROR: $BUNDLE_DIR/qingci-bot-ce 不是可执行文件（检查 build-linux.sh 是否成功）" >&2
  exit 1
}

# deb 用 Debian 架构名，rpm 用内核机器名，两者不同名（x86_64 -> amd64）
MACHINE="$(uname -m)"
case "$MACHINE" in
  x86_64)  DEB_ARCH="amd64" ;;
  aarch64) DEB_ARCH="arm64" ;;
  armv7l)  DEB_ARCH="armhf" ;;
  *)       DEB_ARCH="$MACHINE" ;;
esac
RPM_ARCH="$MACHINE"

INSTALL_DIR="${PREFIX%/}/${PKG_NAME}"
DATA_DIR="/var/lib/${PKG_NAME}"
LOG_DIR="/var/log/${PKG_NAME}"
SERVICE_USER="qingci-bot"
SERVICE_UNIT="qingci-bot-ce.service"
MAINTAINER="Qingci-Bot <https://github.com/Qingci-Bot>"

PKG_SIZE_KB="$(du -sk "$BUNDLE_DIR" | awk '{print $1}')"

echo "Version:     $VERSION"
echo "Bundle:      $BUNDLE_DIR ($(du -sh "$BUNDLE_DIR" | awk '{print $1}'))"
echo "Install dir: $INSTALL_DIR"
echo "Data dir:    $DATA_DIR"
echo "Output:      $OUT_DIR"
echo ""

# ── 组装运行时文件树（deb / rpm 共用）────────────────────
# 目录布局遵循 FHS：
#   $PREFIX/<pkg>/       程序（升级时整体替换，视为只读）
#   /var/lib/<pkg>/      实例数据（config.yaml / data / plugins），卸载保留
#   /var/log/<pkg>/      服务日志（stdout/stderr 重定向）
#   /etc/systemd/system/ systemd 单元
#
# 只输出「程序 + 单元文件」，控制脚本由各格式自行决定放置方式。
build_payload() {
  local stage="$1"
  mkdir -p "$stage$INSTALL_DIR" "$stage/etc/systemd/system"
  # 后端 onedir 整体拷贝；cp -a 保留权限位（主程序必须可执行）。
  # --no-preserve=timestamps：部分构建环境（受限容器 / 只读源）不允许 utime，
  # 时间戳对本包无意义，跳过可避免打包中断。
  cp -a --no-preserve=timestamps "$BUNDLE_DIR/." "$stage$INSTALL_DIR/"

  # 服务模式下不把实例写进程序目录：QINGCI_DATA_HOME 指向 /var/lib，
  # bot/paths.py 的 instances_dir() 会在其后追加 instances/。
  # 这里预建空目录并附说明，让管理员装完就知道配置在哪。
  mkdir -p "$stage$DATA_DIR/instances" "$stage$LOG_DIR"
  # deb 用 root:root（--root-owner-group）+ postinst 里 chown 修正属主；
  # rpm 在 %files 用 %attr 直接声明属主。这里先把目录权限收紧到 0750，
  # 避免服务账号建立前就存在全局只读窗口。
  chmod 0750 "$stage$DATA_DIR" "$stage$LOG_DIR"
  chmod 0755 "$stage$INSTALL_DIR"
  cat > "$stage$DATA_DIR/README.txt" <<'EOF'
Qingci-Bot CE 实例数据根目录
================================
实例位于: 本目录下 instances/<名称>/
  config.yaml   实例配置（LLM api_key、OneBot 与 Telegram 协议端等）
  data/         数据库、会话历史、运行日志
  plugins/      外部插件

首次启动会自动创建 default 实例并生成默认 config.yaml。
注意：外部 OneBot 协议端（NapCat / LLBot 等）连入前，
      需把 config.yaml 的 onebot.host 改为 0.0.0.0 并设置 access_token。
EOF

  cat > "$stage/etc/systemd/system/$SERVICE_UNIT" <<EOF
[Unit]
Description=Qingci-Bot CE (multi-platform bot framework)
Documentation=https://github.com/Qingci-Bot/Qingci-Bot-CE
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
# 数据根独立于程序目录：升级替换 $INSTALL_DIR 时不触碰实例数据
Environment=QINGCI_DATA_HOME=$DATA_DIR
Environment=PYTHONUNBUFFERED=1
# 插件依赖安装器（pip / uv）与服务账号 HOME 的缓存位置：
# 服务账号 home 设为 $DATA_DIR，显式固定 HOME 让 pip/uv 缓存落在数据目录，
# 不依赖 /etc/passwd 中 home 是否真实存在。
Environment=HOME=$DATA_DIR
# 默认监听 0.0.0.0 以便局域网访问 Web UI / OneBot 反向 WS；
# 仅需本机访问时改为 127.0.0.1（可用 systemctl edit 覆盖 ExecStart）
ExecStart=$INSTALL_DIR/qingci-bot-ce --host 0.0.0.0
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30s
StandardOutput=append:$LOG_DIR/service.log
StandardError=append:$LOG_DIR/service.log

# 基础加固：程序目录只读，仅数据/日志可写。
# 显式列出可写路径——插件安装会把依赖写进 instances/<n>/deps，
# Playwright（若启用渲染）需要写浏览器缓存，二者都在 $DATA_DIR 之下。
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$DATA_DIR $LOG_DIR

[Install]
WantedBy=multi-user.target
EOF
}

# 服务账号创建 + 目录属主修正（deb postinst 与 rpm %post 共用同一份逻辑）
service_setup_script() {
  cat <<EOF
if ! getent group $SERVICE_USER >/dev/null 2>&1; then
  groupadd --system $SERVICE_USER >/dev/null 2>&1 || addgroup --system $SERVICE_USER >/dev/null 2>&1 || true
fi
if ! getent passwd $SERVICE_USER >/dev/null 2>&1; then
  useradd --system --gid $SERVICE_USER --home-dir $DATA_DIR --shell /usr/sbin/nologin $SERVICE_USER >/dev/null 2>&1 \\
    || adduser --system --group --home $DATA_DIR --no-create-home --shell /usr/sbin/nologin $SERVICE_USER >/dev/null 2>&1 \\
    || true
fi
chown -R $SERVICE_USER:$SERVICE_USER $DATA_DIR $LOG_DIR 2>/dev/null || true
systemctl daemon-reload >/dev/null 2>&1 || true
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl enable $SERVICE_UNIT >/dev/null 2>&1 || true
  # 升级（configure 且已有旧版本）时重启以加载新程序；首次安装只 enable，
  # 由管理员确认配置后再 start（默认 config.yaml 无 api_key，直接起也可但不对话）
  if [ -n "\${2:-}" ]; then
    systemctl restart $SERVICE_UNIT >/dev/null 2>&1 || true
  fi
fi
exit 0
EOF
}

service_stop_script() {
  cat <<EOF
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl stop $SERVICE_UNIT >/dev/null 2>&1 || true
  systemctl disable $SERVICE_UNIT >/dev/null 2>&1 || true
fi
exit 0
EOF
}

# ── deb ───────────────────────────────────────────────────
build_deb() {
  local stage="$1"
  local out="$OUT_DIR/${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb"

  mkdir -p "$stage/DEBIAN"
  cat > "$stage/DEBIAN/control" <<EOF
Package: $PKG_NAME
Version: $VERSION
Section: net
Priority: optional
Architecture: $DEB_ARCH
Maintainer: $MAINTAINER
Installed-Size: $PKG_SIZE_KB
Depends: systemd, ca-certificates, tzdata
Recommends: git
Homepage: https://github.com/Qingci-Bot/Qingci-Bot-CE
Description: Qingci-Bot CE server (headless bot framework)
 Multi-platform bot framework built on the OneBot 12 event model, with
 LLM chat, a plugin system and a Web UI. This package installs the
 headless backend plus a systemd unit; the desktop shell is not included.
EOF

  # dpkg 约定：postinst 的 $1=configure，升级时 $2=旧版本号
  {
    echo '#!/bin/sh'
    echo 'set -e'
    echo 'CONFIGURE_ARG="${1:-}" OLD_VERSION="${2:-}"'
    service_setup_script
  } > "$stage/DEBIAN/postinst"

  {
    echo '#!/bin/sh'
    echo 'set -e'
    echo '# $1 = remove | upgrade | deconfigure'
    echo 'case "${1:-}" in'
    echo '  remove|deconfigure)'
    service_stop_script
    echo '  ;;'
    echo 'esac'
    echo 'exit 0'
  } > "$stage/DEBIAN/prerm"

  {
    echo '#!/bin/sh'
    echo 'set -e'
    echo 'if [ "${1:-}" = "purge" ]; then'
    echo "  rm -rf $DATA_DIR $LOG_DIR"
    echo 'fi'
    echo 'if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then'
    echo '  systemctl daemon-reload >/dev/null 2>&1 || true'
    echo 'fi'
    echo 'exit 0'
  } > "$stage/DEBIAN/postrm"

  chmod 0755 "$stage/DEBIAN" "$stage/DEBIAN/postinst" "$stage/DEBIAN/prerm" "$stage/DEBIAN/postrm"
  # 包根目录继承 mktemp 的 0700，会被 dpkg 打进包里；显式放开
  chmod 0755 "$stage"

  mkdir -p "$OUT_DIR"
  # --root-owner-group：非 root 构建时把属主统一记为 root:root（dpkg 期望）
  dpkg-deb --root-owner-group -Zxz --build "$stage" "$out" >/dev/null
  echo "    -> $out"
}

# ── rpm ───────────────────────────────────────────────────
build_rpm() {
  local stage="$1"
  local topdir
  topdir="$(mktemp -d "${TMPDIR:-/tmp}/qingci-rpm.XXXXXX")"
  mkdir -p "$topdir"/{BUILD,RPMS,SOURCES,SPECS,SRPMS,BUILDROOT}

  # rpmbuild 会在 %install 前清空 BUILDROOT，并把目录名规范化成
  # <name>-<version>-build 之类的形式，无法预先可靠复现。
  # 因此把文件树作为「源码」放进 SOURCES，由 %install 阶段复制进 %{buildroot}——
  # 这是 rpm 的标准做法，也不受 BUILDROOT 命名差异影响。
  local srcdir="$topdir/SOURCES/payload"
  mkdir -p "$srcdir"
  cp -a --no-preserve=timestamps "$stage/." "$srcdir/"

  local out
  out="${PKG_NAME}-${VERSION}-1.${RPM_ARCH}.rpm"

  # 目录归属：程序目录与单元文件归 root；数据/日志目录归服务用户
  # （%files 里单独声明 %attr，避免 rpm 校验属性不一致）
  cat > "$topdir/SPECS/${PKG_NAME}.spec" <<EOF
Name:           $PKG_NAME
Version:        $VERSION
Release:        1%{?dist}
Summary:        Qingci-Bot CE server (headless bot framework)
License:        GPL-3.0-or-later
URL:            https://github.com/Qingci-Bot/Qingci-Bot-CE
BuildArch:      $RPM_ARCH
Requires:       systemd
Requires:       ca-certificates
Requires:       tzdata
Recommends:     git
AutoReqProv:    no

# 关闭发行版的 post-install 处理链（strip / brp-mangle-shebangs / brp-compress 等）：
#   - 产物是 PyInstaller 冻结的 ELF 与已归档的 Python 运行时，strip 反而可能
#     破坏 PyInstaller 的引导段与 _internal 内的 .so 调试信息依赖；
#   - brp-mangle-shebangs 会把内容像脚本的文件当成 shell 脚本改写，
#     对冻结产物毫无意义且可能误伤。
%define __os_install_post %{nil}
%global debug_package %{nil}

%description
Multi-platform bot framework built on the OneBot 12 event model, with
LLM chat, a plugin system and a Web UI. This package installs the
headless backend plus a systemd unit; the desktop shell is not included.

%prep

%build

%install
# 文件树由 pack_linux_server.sh 以「源码」形式置于 %{_sourcedir}/payload。
# 用普通递归拷贝 + 显式 chmod：cp -a / -p 会尝试保留属主与时间戳，在受限
# 构建环境（容器内非 root 或只读源）下会被拒绝而令 %install 失败。
# 权限位是唯一有语义的部分——主程序 qingci-bot-ce 必须可执行。
mkdir -p %{buildroot}
cp -R %{_sourcedir}/payload/. %{buildroot}/
# 注意用 %{buildroot} 前缀：%install 中写绝对路径会直接落到构建机根目录。
# 单元文件不能带可执行位（无 shebang，brp-mangle-shebangs 会告警并改动权限）。
chmod 0755 %{buildroot}$INSTALL_DIR/qingci-bot-ce
chmod 0644 %{buildroot}/etc/systemd/system/$SERVICE_UNIT

%files
$INSTALL_DIR
%attr(0750,$SERVICE_USER,$SERVICE_USER) $DATA_DIR
%attr(0750,$SERVICE_USER,$SERVICE_USER) $DATA_DIR/README.txt
%attr(0750,$SERVICE_USER,$SERVICE_USER) $DATA_DIR/instances
%attr(0750,$SERVICE_USER,$SERVICE_USER) $LOG_DIR
%{_unitdir}/$SERVICE_UNIT

%post
$(service_setup_script)

%preun
# \$1 = 0 表示卸载（1 表示升级）
if [ "\$1" = "0" ]; then
$(service_stop_script | sed 's/^/  /')
fi
exit 0

%postun
if [ "\$1" = "0" ]; then
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
exit 0

%changelog
* $(LC_ALL=C date '+%a %b %d %Y') $MAINTAINER - $VERSION-1
- Release $VERSION
EOF

  local log
  if ! log="$(rpmbuild --define "_topdir $topdir" \
                        --define "_unitdir /etc/systemd/system" \
                        --define "_rpmdir $topdir/RPMS" \
                        -bb "$topdir/SPECS/${PKG_NAME}.spec" 2>&1)"; then
    echo "ERROR: rpmbuild 失败，末尾输出：" >&2
    echo "$log" | tail -n 30 >&2
    rm -rf "$topdir"
    return 1
  fi

  # rpmbuild 按架构分目录输出（RPMS/x86_64/xxx.rpm），统一挪到 OUT_DIR
  local built
  built="$(find "$topdir/RPMS" -name '*.rpm' -print -quit)"
  if [ -z "$built" ]; then
    echo "ERROR: rpmbuild 未产出 rpm" >&2
    echo "$log" | tail -n 20 >&2
    rm -rf "$topdir"
    return 1
  fi
  mkdir -p "$OUT_DIR"
  # 跨文件系统 mv 会尝试保留时间戳，在受限环境可能打印告警；结果不受影响，
  # 这里用 cp -f 后删除源文件等价实现，输出更干净。
  cp -f "$built" "$OUT_DIR/$out" && rm -f "$built"
  rm -rf "$topdir"
  echo "    -> $OUT_DIR/$out"
}

# ── 主流程 ─────────────────────────────────────────────────
mkdir -p "$OUT_DIR"

WANT_DEB=0
WANT_RPM=0
case "$ONLY" in
  deb) WANT_DEB=1 ;;
  rpm) WANT_RPM=1 ;;
  "")  WANT_DEB=1; WANT_RPM=1 ;;
  *)   echo "ERROR: --only 仅支持 deb / rpm" >&2; exit 1 ;;
esac

RELEASE_ARCH="$MACHINE"
echo "Arch:        deb=$DEB_ARCH rpm=$RPM_ARCH"

if [ "$WANT_DEB" = "1" ]; then
  if command -v dpkg-deb >/dev/null 2>&1; then
    echo "==> building deb..."
    S="$(mktemp -d "${TMPDIR:-/tmp}/qingci-deb.XXXXXX")"
    trap 'rm -rf "$S"' EXIT
    build_payload "$S"
    build_deb "$S"
    rm -rf "$S"
    trap - EXIT
  else
    [ "$ONLY" = "deb" ] && { echo "ERROR: 未找到 dpkg-deb（Debian/Ubuntu 系工具）" >&2; exit 1; }
    echo "    skip deb: dpkg-deb not found" >&2
  fi
fi

if [ "$WANT_RPM" = "1" ]; then
  if command -v rpmbuild >/dev/null 2>&1; then
    echo "==> building rpm..."
    S="$(mktemp -d "${TMPDIR:-/tmp}/qingci-rpm-tree.XXXXXX")"
    trap 'rm -rf "$S"' EXIT
    build_payload "$S"
    build_rpm "$S"
    trap - EXIT
  else
    [ "$ONLY" = "rpm" ] && { echo "ERROR: 未找到 rpmbuild（rpm-build / rpm 包）" >&2; exit 1; }
    echo "    skip rpm: rpmbuild not found" >&2
  fi
fi

echo ""
echo "Done. Packages under $OUT_DIR:"
ls -lh "$OUT_DIR" 2>/dev/null | tail -n +2 || true

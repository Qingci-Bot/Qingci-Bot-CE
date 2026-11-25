#!/usr/bin/env bash
# 兼容包装：build-linux.sh → build-unix.sh
#
# 1.23 起构建脚本改名为 build-unix.sh（Linux 与 macOS 共用，原名字会让 macOS
# 使用者以为不适用）。为了不让老脚本、老文档、老的本地命令失效，保留本文件
# 作为转发；新代码请直接调用 build-unix.sh。
set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "注意：build-linux.sh 已更名为 build-unix.sh（Linux / macOS 共用），本文件仅作兼容转发。" >&2
exec bash "$Root/build-unix.sh" "$@"

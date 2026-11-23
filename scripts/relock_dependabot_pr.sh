#!/usr/bin/env bash
# Copyright (C) 2026 Zhou Zhe (aka luoqingci)
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms under GPLv3 Section 7: see NOTICE
#
# 接管 dependabot 的 Python 依赖 PR：补上它从不更新的 uv.lock。
#
# 为什么需要：dependabot 只改 pyproject.toml 的版本区间，不动 uv.lock；
# 本项目 CI 用 `uv sync --locked`，所以这类 PR 会在第一步就失败
# （error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided）。
#
# 用法：
#   scripts/relock_dependabot_pr.sh 79             # 预览：切分支、重新锁、显示 diff，不推送
#   scripts/relock_dependabot_pr.sh 79 --yes       # 确认后提交并推送
#   scripts/relock_dependabot_pr.sh 79 --force     # 跳过「必须是 dependabot PR」的校验（自测用）
#
# 退出后如需回到原分支，脚本会在结尾自动切回。
set -euo pipefail

PR="${1:-}"
MODE="${2:-}"
WITH_FORCE=0

[ -n "$PR" ] || { echo "用法: $0 <PR号> [--yes|--force]" >&2; exit 2; }
case "$MODE" in
  ""|--yes) ;;
  --force) WITH_FORCE=1 ;;
  *) echo "ERROR: 未知参数 $MODE（可用 --yes / --force）" >&2; exit 2 ;;
esac

command -v gh >/dev/null 2>&1 || { echo "ERROR: 未找到 gh" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "ERROR: 未找到 uv" >&2; exit 1; }

if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: 工作区不干净，先提交或 stash 再运行（脚本会切分支）" >&2
  exit 1
fi

ORIG_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
restore() {
  git checkout -q "$ORIG_BRANCH" 2>/dev/null || true
}
trap restore EXIT

STATE="$(gh pr view "$PR" --json state --jq .state)"
[ "$STATE" = "OPEN" ] || { echo "ERROR: #$PR 状态为 $STATE，不是 OPEN" >&2; exit 1; }

AUTHOR="$(gh pr view "$PR" --json author --jq .author.login)"
if [ "$AUTHOR" != "app/dependabot" ] && [ "$AUTHOR" != "dependabot[bot]" ]; then
  if [ "$WITH_FORCE" = "1" ]; then
    echo "WARN: #$PR 的作者是 $AUTHOR，非 dependabot（--force 已放行）" >&2
  else
    echo "ERROR: #$PR 的作者是 $AUTHOR，不是 dependabot PR（确认要接管请加 --force）" >&2
    exit 1
  fi
fi

HEAD_REF="$(gh pr view "$PR" --json headRefName --jq .headRefName)"
echo "==> 接管 #$PR（分支 $HEAD_REF，作者 $AUTHOR）"

git fetch -q origin "$HEAD_REF" main
git checkout -q "$HEAD_REF"

# dependabot 的 PR 可能落后 main 很多：先并入 main，减少后续冲突面。
# 已经是最新时 git 会输出「已经是最新的」并以 0 退出。
if ! git merge --no-edit origin/main >/dev/null 2>&1; then
  echo "ERROR: 与 main 合并冲突，需要人工处理（当前停在 $HEAD_REF）" >&2
  trap - EXIT
  exit 1
fi

echo "==> 重新锁定（必须指定官方源：用镜像源会把地址写进 uv.lock，CI 会判为过期）"
uv lock --default-index https://pypi.org/simple

echo "==> uv.lock 变更："
if git diff --quiet -- uv.lock; then
  echo "    （无变更——锁文件已经一致，可能该 PR 不需要接管）"
else
  git diff --stat -- uv.lock | sed 's/^/    /'
  if grep -q "index = " <(git diff -- uv.lock | grep '^+' || true); then
    echo "    WARN: 锁文件的索引行有改动，请确认写的是 pypi.org" >&2
  fi
fi

echo "==> 校验锁文件与依赖一致性"
uv lock --check >/dev/null && echo "    uv lock --check OK"

# 若本次改动碰了 ruff 的钉子，顺带用新版本跑一遍格式/告警检查，
# 避免推送后才发现新版 ruff 引入新规则（0.17.3→0.17.6 期间就踩过）。
if git diff --name-only origin/main...HEAD | grep -q '^pyproject.toml$' &&
  grep -q '^    "ruff==' pyproject.toml; then
  RUFF_VER="$(sed -n 's/^    "ruff==\([^"]*\)".*/\1/p' pyproject.toml | head -1)"
  if [ -n "$RUFF_VER" ]; then
    echo "==> 用 ruff $RUFF_VER 检查（uvx 临时环境，不改动仓库 venv）"
    if uvx "ruff@$RUFF_VER" check . >/dev/null 2>&1; then
      echo "    ruff check OK"
    else
      echo "    ruff check 未通过，请先处理再推（uvx ruff@$RUFF_VER check .）" >&2
      exit 1
    fi
    if uvx "ruff@$RUFF_VER" format --check . >/dev/null 2>&1; then
      echo "    ruff format OK"
    else
      echo "    ruff format 需要调整（uvx ruff@$RUFF_VER format .）" >&2
      exit 1
    fi
  fi
fi

if [ "$MODE" != "--yes" ]; then
  echo ""
  echo "预览模式：未提交、未推送。确认无误后重新运行："
  echo "    $0 $PR --yes"
  exit 0
fi

git add uv.lock
git commit -q -m "chore(deps): 同步 uv.lock（接管 dependabot #$PR）

dependabot 只改 pyproject 的版本区间，不改 uv.lock；本项目 CI 用
\`uv sync --locked\`，因此需要补一次 \`uv lock --default-index https://pypi.org/simple\`。"
git push -q origin "$HEAD_REF"
echo ""
echo "已推送。重新触发 CI："
echo "    gh pr checks $PR --watch"

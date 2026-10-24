"""文档一致性测试 — Markdown 代码块必须可解析、导入的符号必须真实存在

文档示例是用户复制的第一份代码，比源码更容易过时。本测试把文档纳入测试范围，
挡住最常见的两类漂移：

1. 代码块语法错误（含被误写进引用块 `>` 前缀、缩进错乱）
2. 导入了已不存在/已改名的符号（如 `ctx.message.segments` 这类字段改名的残留）

覆盖仓库根目录与 docs/ 下的面向开发者文档；`web/node_modules` 等第三方文档不扫。
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [
    "README.md",
    "PLUGIN_DEV.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/CODING_STANDARDS.md",
    "docs/PROJECT_STRUCTURE.md",
]

_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
# 代码块可能嵌在引用块里（每行以 "> " 开头），解析前剥掉前缀
_QUOTE_PREFIX = re.compile(r"^> ?", re.MULTILINE)


def _blocks(doc: str) -> list[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    return [_QUOTE_PREFIX.sub("", b) for b in _PY_BLOCK.findall(text)]


def _all_blocks() -> list[tuple[str, int, str]]:
    out = []
    for doc in DOCS:
        if not (ROOT / doc).is_file():
            continue
        for i, b in enumerate(_blocks(doc), 1):
            out.append((doc, i, b))
    return out


def test_docs_exist():
    """兜底：文档清单里的文件必须存在（防止改名后测试静默空转）"""
    missing = [d for d in DOCS if not (ROOT / d).is_file()]
    assert not missing, f"文档不存在：{missing}"


@pytest.mark.parametrize("doc,index,block", _all_blocks(), ids=lambda v: str(v)[:40])
def test_code_block_parses(doc: str, index: int, block: str):
    """每个 python 代码块都必须是合法 Python"""
    try:
        ast.parse(block)
    except SyntaxError as e:
        raise AssertionError(
            f"{doc} 第 {index} 个 python 代码块语法错误: {e.msg} (line {e.lineno})"
        ) from e


def test_documented_symbols_exist():
    """代码块中 `from bot/api/qingci_plugin_sdk... import X` 的 X 必须真实存在"""
    missing: list[str] = []
    for doc, index, block in _all_blocks():
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # 由 test_code_block_parses 报错
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith(("bot", "api", "qingci_plugin_sdk")):
                continue
            try:
                module = importlib.import_module(node.module)
            except Exception as e:  # noqa: BLE001 - 导入失败本身就是问题
                missing.append(f"{doc}#{index}: 无法导入 {node.module}（{type(e).__name__}）")
                continue
            for alias in node.names:
                if alias.name != "*" and not hasattr(module, alias.name):
                    missing.append(f"{doc}#{index}: {node.module} 无符号 {alias.name}")
    assert not missing, "文档引用了不存在的符号：\n" + "\n".join(missing)


def test_no_escaped_backticks():
    """表格里不能用 `\\`\\`` 表示根路径——渲染出来是字面反斜杠，读者看不懂"""
    bad: list[str] = []
    for doc in DOCS:
        if not (ROOT / doc).is_file():
            continue
        for lineno, line in enumerate((ROOT / doc).read_text(encoding="utf-8").splitlines(), 1):
            if "\\`\\`" in line:
                bad.append(f"{doc}:{lineno}: {line.strip()[:60]}")
    assert not bad, "文档含转义反引号（应改为 `/` 或明确路径）：\n" + "\n".join(bad)


_MD_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")
# 代码块里形如 foo["k"](...) 的调用会被误判成 Markdown 链接，扫描前先剔除围栏代码块
_FENCED = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)


def _prose_lines(doc: str) -> list[tuple[int, str]]:
    """返回去掉围栏代码块后的 (行号, 行内容)，行号与原文对齐（被剔除的行补空行）"""
    text = (ROOT / doc).read_text(encoding="utf-8")
    masked = _FENCED.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return list(enumerate(masked.splitlines(), 1))


def test_relative_links_exist():
    """相对路径的文档链接必须指向真实文件（防止引用已删除/从未存在的文档）"""
    bad: list[str] = []
    for doc in DOCS:
        if not (ROOT / doc).is_file():
            continue
        base = (ROOT / doc).parent
        for lineno, line in _prose_lines(doc):
            for target in _MD_LINK.findall(line):
                path = target.split("#", 1)[0]
                if not path:  # 纯锚点
                    continue
                if not (base / path).exists():
                    bad.append(f"{doc}:{lineno}: {target}")
    assert not bad, "文档链接指向不存在的文件：\n" + "\n".join(bad)

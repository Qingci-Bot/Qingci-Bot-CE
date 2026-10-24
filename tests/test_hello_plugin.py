"""示例插件与 @llm_tool 使用方式的回归测试

SDK 的 `@llm_tool` **只接受模块级自由函数**：装饰器收集到的是未绑定函数，宿主按
`handler(**args)` 零参调用；若装饰在类方法上，SDK 会拒绝收集并只留一条 WARNING，
表现为「工具静默不存在」——`plugins/hello` 的 `get_time` 就曾这样失效过一个版本。

本文件挡两件事：
1. 示例插件的工具确实能被收集（端到端，不只查语法）
2. 仓库里不再出现「把 @llm_tool 装饰在类方法上」的写法（结构性，覆盖未来新增插件）
"""

import ast
import importlib
from pathlib import Path

from qingci_plugin_sdk.llm_tool import begin_tool_collection, end_tool_collection

ROOT = Path(__file__).resolve().parent.parent
# 扫描范围：外部示例插件 + 内置插件
PLUGIN_DIRS = [ROOT / "plugins", ROOT / "bot" / "plugin" / "builtin"]


def test_hello_llm_tools_collected():
    """hello 插件的两个工具都能被收集（get_time 曾在类方法上，被 SDK 拒绝）"""
    collector = begin_tool_collection()
    try:
        importlib.import_module("plugins.hello")
    finally:
        end_tool_collection()
    assert {t.name for t in collector} == {"get_time", "echo"}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for base in PLUGIN_DIRS:
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def test_no_llm_tool_on_class_methods():
    """@llm_tool 不得装饰类方法（SDK 会拒绝收集，工具静默失效）"""
    offenders: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for deco in item.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    name = getattr(target, "id", None) or getattr(target, "attr", None)
                    if name == "llm_tool":
                        offenders.append(
                            f"{path.relative_to(ROOT)}: {node.name}.{item.name} "
                            "（改用模块级函数，或 self.tool_registry.register）"
                        )
    assert not offenders, "@llm_tool 装饰在类方法上，工具不会注册：\n" + "\n".join(offenders)

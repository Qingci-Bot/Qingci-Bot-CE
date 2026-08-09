"""Function Calling 工具注册表

提供工具的注册、Schema 导出与执行能力：
- 注册项包含名称、描述、JSON Schema 参数定义与处理函数
- get_openai_tools() 导出 OpenAI tools 格式，直接传给 LLM 适配器
- execute() 执行工具并捕获一切异常，错误以文本结果回传给模型，
  避免工具异常中断 Function Calling 循环

内置工具均为只读操作（查询时间、随机一言），不执行任何危险动作。
"""

import inspect
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger("qingci-bot.llm.tools")


class ToolRegistry:
    """Function Calling 工具注册表"""

    def __init__(self):
        # name -> {"description": str, "parameters": dict, "handler": Callable}
        self._tools: dict[str, dict] = {}

    # ============ 注册 ============

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
    ) -> None:
        """注册一个工具

        Args:
            name: 工具名（唯一，建议使用小写下划线风格）
            description: 工具描述（供模型判断何时调用）
            parameters: JSON Schema 格式的参数定义
            handler: 处理函数，可为同步或异步；参数名需与 Schema 对应

        Raises:
            ValueError: 工具名重复或为空
        """
        if not name:
            raise ValueError("工具名不能为空")
        if name in self._tools:
            raise ValueError(f"工具已存在: {name}")
        self._tools[name] = {
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
            "handler": handler,
        }
        logger.info(f"工具已注册: {name}")

    def tool(
        self,
        name: Optional[str] = None,
        description: str = "",
        parameters: Optional[dict] = None,
    ) -> Callable:
        """装饰器形式的注册入口

        用法:
            @registry.tool(description="...", parameters={...})
            def my_tool(arg: str) -> str: ...
        """
        def decorator(func: Callable) -> Callable:
            self.register(
                name=name or func.__name__,
                description=description or (func.__doc__ or "").strip(),
                parameters=parameters or {"type": "object", "properties": {}},
                handler=func,
            )
            return func
        return decorator

    def unregister(self, name: str) -> bool:
        """注销工具，返回是否成功"""
        return self._tools.pop(name, None) is not None

    def unregister_by_prefix(self, prefix: str) -> int:
        """按前缀批量注销工具（如 MCP 工具的 mcp_ 前缀），返回注销数量"""
        names = [n for n in self._tools if n.startswith(prefix)]
        for n in names:
            self._tools.pop(n, None)
        if names:
            logger.info(f"按前缀注销工具 {len(names)} 个: {prefix}*")
        return len(names)

    # ============ 查询 ============

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools(self) -> list[dict]:
        """列出工具元信息（不含 handler），供管理命令展示"""
        return [
            {"name": name, "description": spec["description"]}
            for name, spec in self._tools.items()
        ]

    def get_openai_tools(self) -> list[dict]:
        """导出 OpenAI tools 格式的工具定义列表"""
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for name, spec in self._tools.items()
        ]

    # ============ 执行 ============

    async def execute(self, name: str, arguments: Optional[dict] = None) -> str:
        """执行工具并返回字符串结果

        任何异常（工具不存在、参数不匹配、执行错误）均被捕获，
        以错误文本返回，供模型自行修正，不向上抛出。
        """
        arguments = arguments or {}
        spec = self._tools.get(name)
        if spec is None:
            logger.warning(f"工具不存在: {name}")
            return f"工具调用失败：不存在名为 {name} 的工具。"
        try:
            result = spec["handler"](**arguments)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, str):
                return result
            # 非字符串结果统一 JSON 序列化（失败则 str 兜底）
            try:
                return json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(result)
        except TypeError as e:
            logger.warning(f"工具参数错误: {name}, args={arguments}, err={e}")
            return f"工具 {name} 参数错误：{e}"
        except Exception as e:
            logger.exception(f"工具执行异常: {name}")
            return f"工具 {name} 执行失败：{e}"


# ============ 内置只读工具 ============

# 随机一言素材（本地静态数据，无网络依赖）
_RANDOM_QUOTES: list[str] = [
    "千里之行，始于足下。",
    "学而不思则罔，思而不学则殆。",
    "不积跬步，无以至千里。",
    "天行健，君子以自强不息。",
    "工欲善其事，必先利其器。",
    "纸上得来终觉浅，绝知此事要躬行。",
    "海纳百川，有容乃大。",
    "路漫漫其修远兮，吾将上下而求索。",
]


def get_current_time(timezone_offset_hours: int = 8) -> str:
    """查询当前时间

    Args:
        timezone_offset_hours: 时区偏移（小时），默认东八区（北京时间）
    """
    try:
        offset = int(timezone_offset_hours)
    except (TypeError, ValueError):
        offset = 8
    tz = timezone(timedelta(hours=offset))
    now = datetime.now(tz)
    return (
        f"当前时间（UTC{'+' if offset >= 0 else ''}{offset}）："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}，星期{'一二三四五六日'[now.weekday()]}"
    )


def random_quote() -> str:
    """随机返回一句励志名言/古诗词（只读，无副作用）"""
    return random.choice(_RANDOM_QUOTES)


def register_builtin_tools(registry: ToolRegistry) -> None:
    """向注册表注册内置只读工具（幂等：已存在则跳过）"""
    if not registry.has("get_current_time"):
        registry.register(
            name="get_current_time",
            description="查询当前日期与时间。当用户询问现在几点、今天日期时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "timezone_offset_hours": {
                        "type": "integer",
                        "description": "时区偏移小时数，默认 8（北京时间）",
                    }
                },
                "required": [],
            },
            handler=get_current_time,
        )
    if not registry.has("random_quote"):
        registry.register(
            name="random_quote",
            description="随机返回一句励志名言或古诗词。当用户想要一句激励的话时使用。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=random_quote,
        )

"""内置插件基类 — 宿主侧类型收窄

SDK 的 ``PluginBase`` 把框架注入的宿主组件分成两套标注：

- ``bot`` / ``db`` / ``config`` / ``connection`` / ``llm`` 标为 ``object | None``
- ``scheduler`` / ``tool_registry`` / ``knowledge_store`` / ``session_state`` /
  ``event_bus`` 标为 ``Any | None``

前者只是「不想依赖宿主类型」的写法，但代价是插件里 ``self.llm.chat()`` 这类访问
会被 mypy 判为 ``object`` 无该属性（SDK 自身在 ``base.py`` 里也是先
``bot: Any = self.bot`` 再使用）。内置插件统一继承本类把这几项收窄为 ``Any``，
避免每个插件重复标注。

外部插件同样会遇到这个问题；根治需要 SDK 把这五项改成 ``Any | None``
（与其余注入项一致），在 SDK 侧修改前，外部插件可自行声明同名属性收窄。
"""

from typing import Any

from ..base import PluginBase


class BuiltinPlugin(PluginBase):
    """内置插件基类：收窄 SDK 中标注为 object 的注入项"""

    bot: Any
    db: Any
    config: Any
    connection: Any
    llm: Any

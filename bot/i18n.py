"""轻量国际化（i18n）模块（框架侧）

提供零依赖的翻译查找与格式化，供框架与内置插件加载多语言资源。

使用方式：
    from bot.i18n import I18n

    i18n = I18n("zh-CN")
    i18n.load_dir("/path/to/plugin/i18n")   # 加载 i18n/<locale>.json
    i18n.t("hello", name="世界")            # -> "你好，世界"

翻译文件约定：插件数据目录下 i18n/<locale>.json，形如
    { "hello": "你好，{name}" }
未命中的 key 原样返回 key 本身，避免崩溃。

> 与 `qingci_plugin_sdk/i18n.py`（插件侧）区分：SDK 的 I18n 面向外部插件
> 声明式多语言（插件基类自动注入 `self.i18n` / `self._`）；本模块为框架侧
> 翻译器（内置插件/框架文案用），两侧实现独立、互不依赖。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("qingci-bot.i18n")


class I18n:
    """轻量翻译器"""

    def __init__(self, locale: str = "zh-CN", translations: dict | None = None):
        self.locale = locale
        self._data: dict[str, str] = dict(translations or {})

    @property
    def locale(self) -> str:
        return self._locale

    @locale.setter
    def locale(self, value: str) -> None:
        self._locale = value

    def t(self, key: str, **kwargs) -> str:
        """翻译 key，支持 {placeholder} 格式化。

        未命中的 key 原样返回（便于发现缺失资源而不崩溃）。
        """
        template = self._data.get(key, key)
        if kwargs and isinstance(template, str) and "{" in template:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return template
        return template

    # 便捷别名：插件内可 self._ = self.i18n.t
    def __call__(self, key: str, **kwargs) -> str:
        return self.t(key, **kwargs)

    def load_dir(self, directory: str | Path, locale: str | None = None) -> bool:
        """从目录加载 i18n/<locale>.json（locale 为空用当前 locale）。

        Returns:
            True 表示成功加载了翻译文件
        """
        locale = locale or self.locale
        path = Path(directory) / "i18n" / f"{locale}.json"
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data.update(data)
                return True
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"i18n 翻译文件加载失败 {path}: {e}")
        return False

    def set(self, key: str, value: str) -> None:
        """手动设置单条翻译"""
        self._data[key] = value


def load_plugin_i18n(plugin) -> I18n:
    """为插件创建 I18n 实例并加载其翻译资源。

    插件翻译目录约定：插件模块同级的 i18n/ 目录（如 plugins/<name>/i18n/）。
    """
    i18n = I18n("zh-CN")
    module = getattr(type(plugin), "__module__", "")
    if not module:
        return i18n
    try:
        import importlib

        mod = importlib.import_module(module)
        mod_path = getattr(mod, "__file__", None)
        if mod_path:
            i18n.load_dir(Path(mod_path).parent)
    except Exception:
        logger.debug(f"加载插件 {getattr(plugin, 'name', '')} i18n 资源失败", exc_info=True)
    return i18n

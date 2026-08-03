"""内置知识库管理插件 - /kb 命令（批次 3：轻量 RAG）

命令（权限与现有管理员命令体系一致，均为 SUPERUSER）：
- /kb add <名称> <内容>   新增知识文档（写入知识库目录并重建索引）
- /kb list                列出知识库文档
- /kb search <关键词>     检索知识库（调试用，返回命中片段）
- /kb remove <名称>       删除知识文档
- /kb reload              重新索引知识库目录

rag.enabled 关闭时 knowledge_store 为 None，命令统一提示未启用。
"""

import logging

from ..base import PluginBase
from ..matcher import MatcherContext, on_command
from ..permission import SUPERUSER

logger = logging.getLogger("qingci-bot.plugin.knowledge")


class KnowledgePlugin(PluginBase):
    """知识库管理插件（Matcher 新式 API）"""

    name = "knowledge"
    version = "1.0.0"
    author = "Qingci-Bot"
    description = "轻量知识库管理：文档增删、检索与索引"

    async def on_load(self):
        logger.info("知识库插件已加载")
        self.matchers.append(
            on_command(
                "kb", permission=SUPERUSER, priority=1,
                description="知识库管理: /kb add|list|search|remove|reload",
            )(self._cmd_kb)
        )

    async def on_unload(self):
        logger.info("知识库插件已卸载")

    # ============ Matcher handler ============

    async def _cmd_kb(self, ctx: MatcherContext) -> str:
        """/kb 子命令分发"""
        store = self.knowledge_store
        if store is None:
            return "知识库未启用（请在配置中开启 rag.enabled）。"

        args = ctx.args.strip()
        if not args:
            return "格式: /kb add|list|search|remove|reload ..."

        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if action == "add":
            return await self._kb_add(store, rest, ctx.user_id)
        if action == "list":
            return self._kb_list(store)
        if action == "search":
            return self._kb_search(store, rest)
        if action == "remove":
            return self._kb_remove(store, rest.strip())
        if action == "reload":
            count = store.reload()
            return f"知识库已重新索引，共 {count} 个分块。"
        return "格式: /kb add|list|search|remove|reload ..."

    async def _kb_add(self, store, rest: str, user_id: int) -> str:
        """/kb add <名称> <内容>"""
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "格式: /kb add <名称> <内容>"
        name, content = parts[0].strip(), parts[1].strip()
        try:
            path = store.add_document(name, content)
        except ValueError as e:
            return f"添加失败：{e}"
        except OSError:
            logger.exception(f"写入知识文件失败: name={name}, user_id={user_id}")
            return "写入知识文件失败，请稍后再试。"
        logger.info(f"知识库新增文档: {path.name}, user_id={user_id}")
        return f"已添加知识文档《{name}》（分块 {store.chunk_count} 个）。"

    def _kb_list(self, store) -> str:
        """/kb list"""
        docs = store.list_documents()
        if not docs:
            return "知识库为空。"
        lines = [f"{d['name']}（{d['chunks']} 块）" for d in docs]
        return f"知识库文档（{len(docs)} 篇）:\n" + "\n".join(lines)

    def _kb_search(self, store, query: str) -> str:
        """/kb search <关键词>"""
        query = query.strip()
        if not query:
            return "格式: /kb search <关键词>"
        hits = store.search(query)
        if not hits:
            return "未检索到相关知识。"
        lines = []
        for i, chunk in enumerate(hits, 1):
            # 片段截断展示，避免单条回复过长
            text = chunk.text if len(chunk.text) <= 100 else chunk.text[:100] + "..."
            lines.append(f"{i}. [《{chunk.source}》] {text}")
        return "检索结果:\n" + "\n".join(lines)

    def _kb_remove(self, store, name: str) -> str:
        """/kb remove <名称>"""
        if not name:
            return "格式: /kb remove <名称>"
        if store.remove_document(name):
            logger.info(f"知识库删除文档: {name}")
            return f"已删除知识文档《{name}》。"
        return f"未找到文档《{name}》。"

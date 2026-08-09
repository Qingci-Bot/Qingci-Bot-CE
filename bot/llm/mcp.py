"""MCP (Model Context Protocol) 集成 - 借鉴 AstrBot 的 MCP 支持

连接外部 MCP 服务器（stdio / HTTP 传输），将服务器暴露的工具注册进
ToolRegistry，复用现有 Function Calling 循环（chat_with_tools）。

- 工具名使用 mcp_{server}_{tool} 前缀避免冲突
- 连接/调用失败仅记录日志，不影响主链路
- mcp 包为可选依赖：未安装时 setup 直接跳过（日志提示）
"""

import json
import logging

logger = logging.getLogger("qingci-bot.llm.mcp")


class MCPBridge:
    """MCP 服务器管理：连接服务器、将工具注册进 ToolRegistry"""

    def __init__(self):
        # (server_name, ClientSession) 列表，close 时统一释放
        self._sessions: list[tuple[str, object]] = []
        # 底层传输上下文（stdio_client / streamablehttp_client），用于关闭
        self._transports: list = []
        self._connected_servers: list[str] = []

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connected_servers)

    async def connect_servers(self, servers_cfg) -> int:
        """连接所有配置的服务器，返回成功连接并列出工具的服务器数"""
        total = 0
        for cfg in servers_cfg:
            if not cfg.name:
                logger.warning("MCP 服务器未配置 name，跳过")
                continue
            try:
                count = await self._connect_one(cfg)
                if count > 0:
                    total += 1
                    logger.info(
                        f"MCP 服务器已连接: {cfg.name}（{count} 个工具）"
                    )
            except Exception:
                logger.exception(f"MCP 服务器连接失败: {cfg.name}")
        return total

    async def _connect_one(self, cfg) -> int:
        """连接单个服务器并返回其工具数"""
        if cfg.command:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args or []),
                env=dict(cfg.env) if cfg.env else None,
            )
            transport = stdio_client(params)
            read, write = await transport.__aenter__()
            session = await ClientSession(read, write).__aenter__()
        elif cfg.url:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            transport = streamablehttp_client(cfg.url)
            read, write = await transport.__aenter__()
            session = await ClientSession(read, write).__aenter__()
        else:
            logger.warning(
                f"MCP 服务器 {cfg.name} 未配置 command 或 url，跳过"
            )
            return 0

        await session.initialize()
        result = await session.list_tools()
        tools = getattr(result, "tools", None) or []
        self._sessions.append((cfg.name, session))
        self._transports.append(transport)
        self._connected_servers.append(cfg.name)
        return len(tools)

    async def register_tools(self, registry) -> int:
        """将各服务器工具注册进 ToolRegistry，返回注册数量"""
        count = 0
        for server_name, session in self._sessions:
            result = await session.list_tools()
            tools = getattr(result, "tools", None) or []
            for tool in tools:
                tool_name = getattr(tool, "name", "")
                if not tool_name:
                    continue
                full_name = f"mcp_{server_name}_{tool_name}"
                if registry.has(full_name):
                    continue
                description = getattr(tool, "description", "") or ""
                schema = getattr(tool, "inputSchema", None)
                if hasattr(schema, "model_dump"):  # pydantic v2 模型
                    schema = schema.model_dump()
                schema = schema or {"type": "object", "properties": {}}
                registry.register(
                    name=full_name,
                    description=description,
                    parameters=schema,
                    handler=self._make_handler(session, tool_name),
                )
                count += 1
        return count

    @staticmethod
    def _make_handler(session, tool_name: str):
        """构造 MCP 工具 handler（async），经 ToolRegistry.execute 的 await 支持调用"""

        async def _handler(**arguments):
            try:
                result = await session.call_tool(tool_name, arguments)
            except Exception as e:
                logger.exception(f"MCP 工具调用失败: {tool_name}")
                return f"工具 {tool_name} 调用失败：{e}"
            # 优先提取文本内容块
            content = getattr(result, "content", None) or []
            texts = []
            for block in content:
                text = getattr(block, "text", None)
                if text is not None:
                    texts.append(str(text))
                elif isinstance(block, dict) and block.get("text"):
                    texts.append(str(block["text"]))
            if texts:
                return "\n".join(texts)
            # 结构化内容兜底
            structured = getattr(result, "structuredContent", None)
            if structured:
                try:
                    return json.dumps(structured, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    return str(structured)
            return f"工具 {tool_name} 返回空结果"

        return _handler

    async def close(self) -> None:
        """关闭所有 MCP 会话与底层传输（幂等）"""
        for _, session in self._sessions:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                logger.exception("关闭 MCP 会话失败")
        self._sessions.clear()
        for transport in self._transports:
            try:
                await transport.__aexit__(None, None, None)
            except Exception:
                logger.exception("关闭 MCP 传输失败")
        self._transports.clear()
        self._connected_servers.clear()
        logger.info("MCP 连接已关闭")

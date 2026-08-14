"""内置图片生成插件 - /image 或 /画图 命令

调用 litellm.aimage_generation 生成图片：
- 独立于主 LLM 配置，读取 config.image（ImageConfig）
- image.api_key 为空时回退 llm.api_key
- 成功后以 CQ 图片段（MessageDispatcher.build_cq_image）回复
- 开关关闭（image.enabled=False，默认）时零行为变化，仅回复未启用提示
"""

import logging
from typing import cast

from ...core.tasks import spawn_background_task
from ..base import PluginBase
from ..matcher import MatcherContext, on_command
from ..rule import rate_limit

logger = logging.getLogger("qingci-bot.plugin.imagegen")


class ImageGenPlugin(PluginBase):
    """AI 绘图插件（Matcher 新式 API）"""

    name = "imagegen"
    version = "1.0.0"
    author = "Qingci-Bot CE"
    description = "AI 绘图"

    async def on_load(self):
        logger.info("图片生成插件已加载")
        self.matchers.append(
            on_command(
                ("image", "画图"),
                rule=rate_limit(),
                description="AI 绘图: /image <提示词>",
                priority=1,
            )(self._cmd_image)
        )

    async def on_unload(self):
        logger.info("图片生成插件已卸载")

    async def _cmd_image(self, ctx: MatcherContext) -> str:
        """处理 /image <提示词> 命令"""
        prompt = (ctx.args or "").strip()
        if not prompt:
            return "用法: /image <提示词>，例如: /image 一只在竹林里喝茶的熊猫"

        image_cfg = self.bot.config.image if self.bot and self.bot.config else None
        if image_cfg is None or not image_cfg.enabled:
            return "图片生成未启用，请先在设置中开启 image.enabled"

        # api_key 优先用 image.api_key，为空时回退主 LLM 的 api_key
        api_key = image_cfg.api_key
        if not api_key and self.bot.config.llm:
            api_key = self.bot.config.llm.api_key or ""
        if not api_key:
            return (
                "图片生成失败：未配置 image.api_key（且无可回退的 llm.api_key），请先在设置中配置"
            )

        try:
            import litellm

            kwargs = {"model": image_cfg.model or "dall-e-3", "prompt": prompt}
            if api_key:
                kwargs["api_key"] = api_key
            if image_cfg.api_url:
                kwargs["api_base"] = image_cfg.api_url
            resp = await litellm.aimage_generation(**kwargs)

            # 提取第一张图片的 URL（兼容 dict / 对象两种返回形态）
            url = ""
            data = getattr(resp, "data", None) if resp is not None else None
            if data:
                item = data[0]
                if isinstance(item, dict):
                    url = item.get("url") or ""
                else:
                    url = getattr(item, "url", "") or ""
            if not url:
                logger.warning(f"图片生成返回无图片地址: user_id={ctx.user_id}")
                return "图片生成失败：接口未返回图片地址，请稍后重试"
        except Exception as e:
            logger.exception(f"图片生成失败: user_id={ctx.user_id}")
            return f"图片生成失败: {e}"

        # 用量记录（fire-and-forget）：图片生成通常不返回 token usage，
        # 此处记一条 prompt_tokens=0 的 image 来源记录，仅用于调用次数统计；
        # 失败仅记日志，不影响回复主链路
        db = getattr(self.bot, "db", None)
        if db is not None:
            session_key = (
                f"group:{ctx.group_id}:{ctx.user_id}"
                if ctx.message_type == "group"
                else f"private:{ctx.user_id}"
            )
            try:
                spawn_background_task(
                    db.save_usage(
                        session_key=session_key,
                        user_id=ctx.user_id,
                        model=image_cfg.model or "dall-e-3",
                        prompt_tokens=0,
                        completion_tokens=0,
                        source="image",
                    ),
                    name="save_usage_image",
                )
            except Exception:
                logger.warning("提交图片生成用量记录失败", exc_info=True)

        from ...core.dispatcher import MessageDispatcher

        return cast(str, MessageDispatcher.build_cq_image(url))

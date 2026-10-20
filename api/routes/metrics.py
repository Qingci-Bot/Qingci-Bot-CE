"""运行指标导出接口（Prometheus 文本格式）"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from api.auth import require_auth
from bot.metrics import CONTENT_TYPE, render_metrics

logger = logging.getLogger("qingci-bot.api.metrics")

# 鉴权与其他管理接口一致：指标含调用量/错误量/插件列表，属于运营数据，
# 不应在未配置 api_key 时对非环回来源开放。
# Prometheus 侧通过 X-API-Key 请求头或 ?token= 抓取（见 README 部署说明）。
router = APIRouter(dependencies=[Depends(require_auth)])


@router.get(
    "",
    response_class=PlainTextResponse,
    summary="Prometheus 指标导出",
)
async def metrics() -> PlainTextResponse:
    """导出进程内运行指标（不查数据库，可安全高频抓取）"""
    from bot.core.bot import get_bot

    bot = None
    try:
        bot = get_bot()
    except RuntimeError:
        # 未装配 Bot 的 API-only 进程：仍返回 up=0，便于区分"进程在但 Bot 未起"
        bot = None
    return PlainTextResponse(render_metrics(bot), media_type=CONTENT_TYPE)

from .bot import router as bot_router  # noqa: F401
from .config import router as config_router  # noqa: F401
from .plugin import router as plugin_router  # noqa: F401
from .log import router as log_router  # noqa: F401

__all__ = [
    "bot_router",
    "config_router",
    "plugin_router",
    "log_router",
]

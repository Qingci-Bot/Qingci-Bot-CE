from .backup import router as backup_router  # noqa: F401
from .bot import router as bot_router  # noqa: F401
from .command import router as command_router  # noqa: F401
from .config import router as config_router  # noqa: F401
from .group import router as group_router  # noqa: F401
from .instances import router as instances_router  # noqa: F401
from .log import router as log_router  # noqa: F401
from .login import router as login_router  # noqa: F401
from .market import router as market_router  # noqa: F401
from .plugin import router as plugin_router  # noqa: F401

__all__ = [
    "bot_router",
    "config_router",
    "plugin_router",
    "log_router",
    "group_router",
    "login_router",
    "backup_router",
    "command_router",
    "instances_router",
    "market_router",
]

from .auth import router as auth_router  # noqa: F401
from .backup import router as backup_router  # noqa: F401
from .bot import router as bot_router  # noqa: F401
from .command import router as command_router  # noqa: F401
from .config import router as config_router  # noqa: F401
from .group import router as group_router  # noqa: F401
from .log import router as log_router  # noqa: F401
from .plugin import router as plugin_router  # noqa: F401

__all__ = [
    "bot_router",
    "config_router",
    "plugin_router",
    "log_router",
    "group_router",
    "auth_router",
    "backup_router",
    "command_router",
]

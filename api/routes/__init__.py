from fastapi import APIRouter

from .bot import router as bot_router
from .config import router as config_router
from .plugin import router as plugin_router
from .log import router as log_router
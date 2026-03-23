from __future__ import annotations

from .logger import setup_logging
from .main import create_app
from .settings import Settings

settings = Settings()
setup_logging(settings)
app = create_app(settings)

__all__ = ["app", "settings"]

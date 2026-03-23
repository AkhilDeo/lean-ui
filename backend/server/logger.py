from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install

from .settings import Environment, Settings

console = Console()
LOG_FORMAT = "[job_id={extra[job_id]}] {message}"


def setup_logging(settings: Settings) -> None:
    logger.remove()
    logger.configure(extra={"job_id": "-", "task_id": "-", "snippet_id": "-", "endpoint": "-"})
    install(show_locals=True)

    if settings.environment == Environment.prod:
        # Add console handler FIRST to ensure logging always works
        logger.add(
            RichHandler(
                console=console,
                show_time=False,
                markup=True,
                show_level=True,
                rich_tracebacks=True,
            ),
            colorize=True,
            level=settings.log_level,
            format=LOG_FORMAT,
            backtrace=True,
            diagnose=True,
        )
    else:
        logger.add(
            RichHandler(
                console=console,
                show_time=False,
                markup=True,
                show_level=True,
                rich_tracebacks=True,
            ),
            colorize=True,
            level=settings.log_level,
            format=LOG_FORMAT,
            backtrace=True,
            diagnose=True,
        )

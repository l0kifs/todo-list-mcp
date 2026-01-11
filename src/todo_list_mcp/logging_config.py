import sys
from pathlib import Path

from loguru import logger

from todo_list_mcp.settings import Settings


def setup_logging(settings: Settings) -> None:
    """
    Configure logging for the application.
    """

    # Remove default handler to avoid duplicate logs
    logger.remove()

    # Console handler - for development/debugging
    logger.add(
        sys.stderr,
        level=settings.logging_level,
        format=settings.logging_format,
        colorize=False,
        backtrace=True,
        diagnose=True,
        enqueue=True,
        catch=True,
    )

    # File handler - for persistent logs
    logger.add(
        Path(settings.app_data_dir) / "logs" / "app.log",
        level=settings.logging_level,
        rotation=settings.logging_rotation,
        retention=settings.logging_retention,
        compression=settings.logging_compression,
        serialize=True,
        enqueue=True,
        backtrace=True,
        diagnose=True,
        catch=True,
    )

    # Configure common context (can be overridden per module)
    logger.configure(extra={"app": settings.app_name, "version": settings.app_version})

    # Log startup message
    logger.info(
        "Logging system initialized",
        log_level=settings.logging_level,
    )

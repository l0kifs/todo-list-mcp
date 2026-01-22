from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = (
    "todo-list-mcp"  # Application name constant. Should be in format "kebab-case".
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=f"{APP_NAME.upper().replace('-', '_')}__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application settings
    app_name: str = Field(default=APP_NAME, description="Application name")
    app_version: str = Field(default="0.3.0", description="Application version")
    app_data_dir: str = Field(
        default=Path.home().joinpath(f".{APP_NAME}").as_posix(),
        description="Data directory path",
    )

    # SQLite database settings
    database_url: str = Field(
        default_factory=lambda: f"sqlite:///{Path.home().joinpath(f'.{APP_NAME}', 'todo_list.db').as_posix()}",
        description="SQLite database URL (e.g., sqlite:///path/to/db.sqlite)",
    )

    # Logging settings
    logging_level: str = Field(
        default="DEBUG", description="Logging level (e.g., DEBUG, INFO, WARNING, ERROR)"
    )
    logging_format: str = Field(
        default="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
        description="Logging format string",
    )
    logging_rotation: str = Field(default="10 MB", description="Log file rotation size")
    logging_retention: str = Field(
        default="10 days", description="Log file retention period"
    )
    logging_compression: str = Field(
        default="zip", description="Log file compression method"
    )


def get_settings() -> Settings:
    """Retrieve application settings."""
    return Settings()

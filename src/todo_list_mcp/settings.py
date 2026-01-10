from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "todo-list-mcp"  # Application name constant. Should be in format "kebab-case".


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
    app_version: str = Field(default="0.1.6", description="Application version")
    app_data_dir: str = Field(
        default=Path.home().joinpath(f".{APP_NAME}").as_posix(),
        description="Data directory path",
    )

    # Logging settings
    logging_level: str = Field(
        default="DEBUG", description="Logging level (e.g., DEBUG, INFO, WARNING, ERROR)"
    )
    logging_format: str = Field(
        default="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
        description="Logging format string",
    )

    # GitHub settings
    github_repo_owner: str = Field(
        default="mcp-github-user", description="GitHub repository owner"
    )
    github_repo_name: str = Field(
        default="todo-list-mcp", description="GitHub repository name"
    )
    github_api_token: str | None = Field(
        default=None, description="GitHub API token for authentication"
    )


def get_settings() -> Settings:
    """Retrieve application settings."""
    return Settings()

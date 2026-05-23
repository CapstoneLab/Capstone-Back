from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ubuntu_ssh_host: str = Field(default="10.0.0.10", alias="UBUNTU_SSH_HOST")
    ubuntu_ssh_port: int = Field(default=22, alias="UBUNTU_SSH_PORT")
    ubuntu_ssh_user: str = Field(default="ubuntu", alias="UBUNTU_SSH_USER")
    ubuntu_ssh_password: str = Field(default="", alias="UBUNTU_SSH_PASSWORD")
    ubuntu_runner_path: str = Field(
        default="/opt/ci-engine/ubuntu_runner.py", alias="UBUNTU_RUNNER_PATH"
    )
    ubuntu_runner_repo_arg: str = Field(default="repo", alias="UBUNTU_RUNNER_REPO_ARG")
    ubuntu_working_dir: str = Field(default="", alias="UBUNTU_WORKING_DIR")
    ubuntu_shell_prelude: str = Field(
        default='export PATH="$HOME/.local/bin:$PATH" && export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"',
        alias="UBUNTU_SHELL_PRELUDE",
    )
    ubuntu_python_command: str = Field(default="python3", alias="UBUNTU_PYTHON_COMMAND")
    ssh_connect_timeout: int = Field(default=15, alias="SSH_CONNECT_TIMEOUT")

    windows_callback_base_url: str = Field(
        default="http://127.0.0.1:8000", alias="WINDOWS_CALLBACK_BASE_URL"
    )
    results_dir: str = Field(default="results", alias="RESULTS_DIR")

    database_url: str = Field(
        default="postgresql+asyncpg://ciuser:cipass@localhost:5432/cidb",
        alias="DATABASE_URL",
    )

    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    github_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/auth/github/callback",
        alias="GITHUB_REDIRECT_URI",
    )
    github_oauth_scopes: str = Field(
        default="read:user user:email repo", alias="GITHUB_OAUTH_SCOPES"
    )
    frontend_redirect_url: str = Field(
        default="http://127.0.0.1:3000/auth/callback",
        alias="FRONTEND_REDIRECT_URL",
    )

    jwt_secret: str = Field(default="change-me-in-prod", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60 * 24 * 7, alias="JWT_EXPIRE_MINUTES")
    token_encryption_key: str = Field(default="", alias="TOKEN_ENCRYPTION_KEY")
    frontend_origin: str = Field(default="", alias="FRONTEND_ORIGIN")


@lru_cache
def get_settings() -> Settings:
    return Settings()

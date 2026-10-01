from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://postchief:postchief@localhost:5432/postchief"
    redis_url: str = "redis://localhost:6379/0"
    signing_key: SecretStr
    encryption_key: SecretStr
    public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    secure_cookies: bool = True
    media_dir: str = "data/media"
    bootstrap_email: str = ""
    bootstrap_password: SecretStr = SecretStr("")
    github_app_id: str = ""
    github_app_slug: str = ""
    github_private_key: SecretStr = SecretStr("")
    github_webhook_secret: SecretStr = SecretStr("")
    meta_client_id: str = ""
    meta_client_secret: SecretStr = SecretStr("")
    threads_client_id: str = ""
    threads_client_secret: SecretStr = SecretStr("")
    linkedin_client_id: str = ""
    linkedin_client_secret: SecretStr = SecretStr("")
    meta_api_version: str = "v25.0"
    linkedin_api_version: str = "202609"

    @field_validator("signing_key")
    @classmethod
    def strong_signing_key(cls, value: SecretStr):
        if len(value.get_secret_value()) < 32:
            raise ValueError("Signing key requires at least 32 characters")
        return value

    @field_validator("encryption_key")
    @classmethod
    def valid_encryption_key(cls, value: SecretStr):
        Fernet(value.get_secret_value().encode())
        return value

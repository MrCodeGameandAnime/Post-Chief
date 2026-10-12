from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator, model_validator, Field
from urllib.parse import urlsplit
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
    mcp_enabled: bool = False
    mcp_client_id: str = Field(default='post-chief-chatgpt', min_length=1, max_length=200)
    mcp_redirect_uris: list[str] = Field(default_factory=list, max_length=10)
    media_dir: str = "data/media"
    web_dir: str = "web"
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
    x_client_id: str = ""
    x_client_secret: SecretStr = SecretStr("")
    pinterest_client_id: str = ""
    pinterest_client_secret: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_ads_developer_token: SecretStr = SecretStr("")
    tiktok_ads_app_id: str = ""
    tiktok_ads_secret: SecretStr = SecretStr("")
    tiktok_ads_authorization_url: str = ""
    twitch_client_id: str = ""
    twitch_client_secret: SecretStr = SecretStr("")
    tiktok_client_key: str = ""
    tiktok_client_secret: SecretStr = SecretStr("")
    tiktok_business_client_id: str = ""
    tiktok_business_client_secret: SecretStr = SecretStr("")
    tiktok_business_authorization_url: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: SecretStr = SecretStr("")
    meta_api_version: str = "v25.0"
    linkedin_api_version: str = "202609"
    linkedin_scopes: str = "openid profile w_member_social"

    @field_validator('mcp_redirect_uris')
    @classmethod
    def registered_callbacks(cls, values):
        for value in values:
            url = urlsplit(value)
            secure = url.scheme == 'https' or (url.scheme == 'http' and url.hostname == '127.0.0.1')
            if (not secure or not url.hostname or url.username or
                    url.password or url.fragment or '*' in value or len(value) > 2000 or
                    (url.port is not None and not 1 <= url.port <= 65535) or
                    any(character.isspace() for character in value)):
                raise ValueError('MCP callbacks require HTTPS or literal 127.0.0.1 HTTP, without credentials or fragments')
        if len(set(values)) != len(values):
            raise ValueError('MCP callbacks must be unique')
        return values

    @model_validator(mode='after')
    def canonical_mcp_origin(self):
        if not self.mcp_enabled: return self
        url = urlsplit(self.public_url)
        secure = url.scheme == 'https' or (url.scheme == 'http' and
            url.hostname in ('localhost', '127.0.0.1', '::1'))
        if (not secure or not url.hostname or url.username or url.password or
                url.query or url.fragment or url.path not in ('', '/') or
                any(character.isspace() for character in self.public_url)):
            raise ValueError('Enabled MCP requires a canonical HTTPS origin, or local loopback HTTP')
        if self.frontend_url.rstrip('/') != self.public_url.rstrip('/'):
            raise ValueError('MCP consent requires FRONTEND_URL to match PUBLIC_URL')
        return self

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

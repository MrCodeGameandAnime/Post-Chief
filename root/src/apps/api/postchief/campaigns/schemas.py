from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROVIDERS = {"facebook", "instagram", "threads", "x", "bluesky", "linkedin"}


def aware(value: datetime | None):
    if value is not None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Schedule must include a timezone offset")
        return value.astimezone(timezone.utc)
    return None


class Override(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = Field(default=None, max_length=20000)
    asset_ids: list[str] | None = Field(default=None, max_length=20)


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    account_ids: list[str] = Field(min_length=1, max_length=20)
    asset_ids: list[str] = Field(default_factory=list, max_length=20)
    overrides: dict[str, Override] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    github_path: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip()

    @field_validator("account_ids", "asset_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate destinations or assets are not allowed")
        return value

    @field_validator("scheduled_at")
    @classmethod
    def aware_time(cls, value):
        return aware(value)

    @field_validator("overrides")
    @classmethod
    def known_providers(cls, value):
        if set(value) - PROVIDERS:
            raise ValueError("Unknown provider override")
        return value

    @model_validator(mode="after")
    def has_content(self):
        if not self.body.strip() and not self.asset_ids:
            raise ValueError("Campaign requires text or media")
        return self


class CampaignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=20000)
    account_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)
    asset_ids: list[str] | None = Field(default=None, max_length=20)
    overrides: dict[str, Override] | None = None
    scheduled_at: datetime | None = None
    github_path: str | None = Field(default=None, max_length=1000)

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class ErrorReason(StrEnum):
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_REVOKED = "AUTH_REVOKED"
    RATE_LIMITED = "RATE_LIMITED"
    MEDIA_INVALID = "MEDIA_INVALID"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PERMISSION_MISSING = "PERMISSION_MISSING"
    ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"
    UNKNOWN = "UNKNOWN"


class ProviderError(Exception):
    def __init__(self, reason: ErrorReason, message: str, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.reason, self.message, self.retryable, self.uncertain = reason, message, retryable, uncertain

    def to_dict(self):
        action = "RECONNECT" if self.reason in (ErrorReason.AUTH_EXPIRED, ErrorReason.AUTH_REVOKED) else "REVIEW"
        result = {"reason": self.reason.value, "retryable": self.retryable, "action_required": action, "message": self.message}
        if self.uncertain:
            result["action_required"] = "RECONCILE"
        return result


@dataclass(frozen=True)
class Capabilities:
    text: bool = True
    image: bool = False
    video: bool = False
    carousel: bool = False
    analytics: bool = False
    comments: bool = False
    replies: bool = False
    messages: bool = False


@dataclass
class PublishResult:
    provider_id: str
    url: str | None = None
    state: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


class PublicationPending(Exception):
    def __init__(self, state: dict, retry_after: int = 30):
        self.state, self.retry_after = state, retry_after
        super().__init__("Provider is processing media")


@dataclass
class Media:
    path: str
    mime_type: str
    byte_size: int
    alt_text: str = ""
    url: str | None = None


class SocialProvider(Protocol):
    capabilities: Capabilities
    def validate(self, body: str, media: list[Media]) -> None: ...
    async def publish(self, credentials: dict, body: str, media: list[Media], key: str, state: dict) -> PublishResult: ...
    async def get_post_metrics(self, credentials: dict, provider_id: str) -> dict: ...
    async def delete(self, credentials: dict, provider_id: str) -> None: ...

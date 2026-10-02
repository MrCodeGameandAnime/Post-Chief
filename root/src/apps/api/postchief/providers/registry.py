from postchief.providers.bluesky import BlueskyProvider
from provider_contracts import ProviderError, ErrorReason

PROVIDERS = {"bluesky":BlueskyProvider}


def get_provider(name, client, settings=None):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ProviderError(ErrorReason.PERMISSION_MISSING,"Provider is not implemented")
    return cls(client)

from postchief.providers.bluesky import BlueskyProvider
from postchief.providers.meta import FacebookProvider, InstagramProvider, ThreadsProvider
from postchief.providers.linkedin import LinkedInProvider
from postchief.providers.x import XProvider
from postchief.providers.pinterest import PinterestProvider
from provider_contracts import ProviderError, ErrorReason

PROVIDERS = {"bluesky":BlueskyProvider,"facebook":FacebookProvider,"instagram":InstagramProvider,"threads":ThreadsProvider,"linkedin":LinkedInProvider,"x":XProvider}
PROVIDERS['pinterest'] = PinterestProvider


def get_provider(name, client, settings=None):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ProviderError(ErrorReason.PERMISSION_MISSING,"Provider is not implemented")
    return cls(client) if name == 'bluesky' else cls(client,settings)

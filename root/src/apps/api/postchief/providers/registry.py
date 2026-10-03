from postchief.providers.bluesky import BlueskyProvider
from postchief.providers.meta import FacebookProvider, InstagramProvider, ThreadsProvider
from postchief.providers.linkedin import LinkedInProvider
from postchief.providers.x import XProvider
from postchief.providers.pinterest import PinterestProvider
from postchief.providers.youtube import YouTubeProvider
from postchief.providers.tiktok import TikTokProvider
from postchief.providers.tiktok_business import TikTokBusinessProvider
from postchief.providers.gbp import GoogleBusinessProvider
from postchief.providers.web_analytics import WebAnalyticsProvider
from postchief.providers.blog import BlogProvider
from provider_contracts import ProviderError, ErrorReason

PROVIDERS = {"bluesky":BlueskyProvider,"facebook":FacebookProvider,"instagram":InstagramProvider,"threads":ThreadsProvider,"linkedin":LinkedInProvider,"x":XProvider}
PROVIDERS['pinterest'] = PinterestProvider
PROVIDERS['youtube'] = YouTubeProvider
PROVIDERS['tiktok'] = TikTokProvider
PROVIDERS['tiktok_business'] = TikTokBusinessProvider
PROVIDERS['gbp'] = GoogleBusinessProvider
PROVIDERS['web'] = WebAnalyticsProvider
PROVIDERS['blog'] = BlogProvider


def get_provider(name, client, settings=None):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ProviderError(ErrorReason.PERMISSION_MISSING,"Provider is not implemented")
    return cls(client) if name == 'bluesky' else cls(client,settings)

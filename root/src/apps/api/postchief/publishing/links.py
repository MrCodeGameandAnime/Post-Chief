import re
from urllib.parse import urlsplit


def instagram_permalink(value):
    """Only native public post links may become publication links."""
    if not isinstance(value,str) or len(value)>2048: return None
    try: parts=urlsplit(value)
    except ValueError: return None
    if parts.scheme!='https' or parts.netloc not in ('www.instagram.com','instagram.com'): return None
    if parts.query or parts.fragment or not re.fullmatch(r'/(?:p|reel|tv)/[A-Za-z0-9_-]+/',parts.path): return None
    return value

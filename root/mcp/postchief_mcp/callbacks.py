"""Exact web callbacks and RFC 8252 ports for registered literal loopback callbacks."""
from urllib.parse import urlsplit


def callback_allowed(value: str, registered: list[str]) -> bool:
    if value in registered:
        return True
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                parsed.username or parsed.password or parsed.fragment or
                parsed.port is None or not 1 <= parsed.port <= 65535):
            return False
        # Only an explicitly registered, port-free literal IPv4 loopback URI
        # permits an ephemeral port. Its path and query remain exact.
        without_port = parsed._replace(netloc='127.0.0.1').geturl()
        return without_port in registered
    except ValueError:
        return False

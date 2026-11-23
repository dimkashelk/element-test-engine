"""Validate destinations and pin each HTTPS connection to a public DNS result."""
import ipaddress
from socket import getaddrinfo, SOCK_STREAM

import httpx


class UnsafeWebhook(ValueError):
    pass


def public_address(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved


def webhook_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise UnsafeWebhook('Invalid webhook URL')
    try:
        url = httpx.URL(value)
        if (url.scheme != 'https' or not url.host or url.userinfo or '#' in value
                or '%' in url.host or '\\' in value or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError()
        try:
            ipaddress.ip_address(url.host)
        except ValueError:
            if url.host.lower().rstrip('.') == 'localhost' or url.host.lower().rstrip('.').endswith('.localhost'):
                raise ValueError()
        else:
            if not public_address(url.host):
                raise ValueError()
        return url
    except (ValueError, httpx.InvalidURL):
        raise UnsafeWebhook('Webhook requires a public HTTPS destination') from None


def pinned_destination(value):
    url = webhook_url(value)
    addresses = getaddrinfo(url.host, url.port or 443, type=SOCK_STREAM)
    if not addresses:
        raise OSError('No webhook addresses')
    if any(not public_address(item[4][0]) for item in addresses):
        raise UnsafeWebhook('Webhook resolved to a non-public address')
    # httpcore uses sni_hostname for certificate verification as well as TLS SNI.
    # The URL's literal IP prevents a second DNS lookup from rebinding the target.
    return url.copy_with(host=addresses[0][4][0]), url.netloc.decode('ascii'), url.host

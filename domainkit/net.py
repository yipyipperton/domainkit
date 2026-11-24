"""URL and IP checks used before every request and every redirect hop.

A destination is allowed only when every resolved address is a public global
unicast address. One private address on a mixed DNS response rejects the host.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from domainkit.errors import FetchError, UnsafeDestination

# Ranges that must never be contacted. is_global is not enough on its own:
# multicast is still "global" in some Python versions, and a few special-use
# addresses are exempted from is_private.
_BLOCKED_NETWORKS: tuple[tuple[str, ipaddress.IPv4Network | ipaddress.IPv6Network], ...] = (
    ("this-network", ipaddress.ip_network("0.0.0.0/8")),
    ("private", ipaddress.ip_network("10.0.0.0/8")),
    ("cgnat", ipaddress.ip_network("100.64.0.0/10")),
    ("loopback", ipaddress.ip_network("127.0.0.0/8")),
    ("link-local", ipaddress.ip_network("169.254.0.0/16")),
    ("private", ipaddress.ip_network("172.16.0.0/12")),
    ("ietf-protocol", ipaddress.ip_network("192.0.0.0/24")),
    ("documentation", ipaddress.ip_network("192.0.2.0/24")),
    ("private", ipaddress.ip_network("192.168.0.0/16")),
    ("benchmarking", ipaddress.ip_network("198.18.0.0/15")),
    ("documentation", ipaddress.ip_network("198.51.100.0/24")),
    ("documentation", ipaddress.ip_network("203.0.113.0/24")),
    ("multicast", ipaddress.ip_network("224.0.0.0/4")),
    ("reserved", ipaddress.ip_network("240.0.0.0/4")),
    ("broadcast", ipaddress.ip_network("255.255.255.255/32")),
    ("unspecified", ipaddress.ip_network("::/128")),
    ("loopback", ipaddress.ip_network("::1/128")),
    ("ipv4-mapped", ipaddress.ip_network("::ffff:0:0/96")),
    ("nat64", ipaddress.ip_network("64:ff9b::/96")),
    ("nat64", ipaddress.ip_network("64:ff9b:1::/48")),
    ("discard", ipaddress.ip_network("100::/64")),
    ("teredo", ipaddress.ip_network("2001::/32")),
    ("documentation", ipaddress.ip_network("2001:db8::/32")),
    ("6to4", ipaddress.ip_network("2002::/16")),
    ("unique-local", ipaddress.ip_network("fc00::/7")),
    ("link-local", ipaddress.ip_network("fe80::/10")),
    ("site-local", ipaddress.ip_network("fec0::/10")),
    ("multicast", ipaddress.ip_network("ff00::/8")),
)

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_OBFUSCATED_PART = re.compile(r"(?:0x[0-9a-f]+|\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedURL:
    """A URL that passed scheme, hostname, and IP checks."""

    url: str
    scheme: str
    host: str
    port: int
    ips: tuple[str, ...]
    pinned_ip: str
    origin: str


def ip_block_reason(ip: str) -> str | None:
    """Return a human-readable reason when an IP must not be contacted."""

    raw = ip.split("%", 1)[0].strip()
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return f"invalid IP address {ip!r}"

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        mapped = str(addr.ipv4_mapped)
        inner = ip_block_reason(mapped)
        if inner:
            return f"IPv4-mapped address {addr} embeds a blocked address ({inner})"
        return f"IPv4-mapped address {addr} is blocked"

    if isinstance(addr, ipaddress.IPv6Address):
        nat64 = ipaddress.ip_network("64:ff9b::/96")
        if addr in nat64:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
            inner = ip_block_reason(str(embedded))
            if inner:
                return f"NAT64 address {addr} embeds a blocked address ({inner})"
            return f"NAT64 address {addr} is blocked"
        sixto4 = ipaddress.ip_network("2002::/16")
        if addr in sixto4:
            embedded = ipaddress.IPv4Address((int(addr) >> 80) & 0xFFFFFFFF)
            inner = ip_block_reason(str(embedded))
            if inner:
                return f"6to4 address {addr} embeds a blocked address ({inner})"
            return f"6to4 address {addr} is blocked"

    for label, network in _BLOCKED_NETWORKS:
        if addr.version == network.version and addr in network:
            return f"{addr} is in blocked {label} network {network}"

    if (
        addr.is_multicast
        or addr.is_reserved
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_private
        or not addr.is_global
        or (isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local)
    ):
        return f"{addr} is not a public global address"
    return None


def canonical_host(host: str) -> str:
    """Lowercase a hostname, strip a trailing dot, and convert IDNA to ASCII."""

    cleaned = host.strip().lower().rstrip(".")
    if not cleaned or "%" in cleaned or "\\" in cleaned:
        raise UnsafeDestination(f"invalid hostname {host!r}")
    try:
        ascii_host = cleaned.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeDestination(f"invalid hostname {host!r}") from exc
    if not _HOST_RE.fullmatch(ascii_host):
        raise UnsafeDestination(f"invalid hostname {ascii_host!r}")
    return ascii_host


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_blocked_hostname(host: str) -> bool:
    """Hostnames that are unsafe even before DNS, including IP obfuscation."""

    if host in _BLOCKED_HOSTS:
        return True
    suffixes = (".localhost", ".local", ".internal", ".localdomain")
    if any(host.endswith(suffix) for suffix in suffixes):
        return True
    if host.isdigit() and len(host) >= 6:
        return True
    parts = host.split(".")
    if 1 < len(parts) <= 4 and all(_OBFUSCATED_PART.fullmatch(part) for part in parts):
        if not is_ip_literal(host):
            return True
    return False


def default_resolver(host: str, port: int) -> list[str]:
    """Resolve a hostname and return unique IP strings. Does not connect."""

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"DNS lookup failed for {host}") from exc
    ips: list[str] = []
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        if address not in ips:
            ips.append(address)
    if not ips:
        raise FetchError(f"DNS lookup returned no addresses for {host}")
    return ips


def _format_netloc(host: str, port: int, scheme: str) -> str:
    default = 443 if scheme == "https" else 80
    display = f"[{host}]" if ":" in host else host
    if port != default:
        return f"{display}:{port}"
    return display


def origin_of(scheme: str, host: str, port: int) -> str:
    return f"{scheme}://{_format_netloc(host, port, scheme)}"


def validate_destination(url: str, resolver=default_resolver) -> ResolvedURL:
    """Validate one URL and resolve every address it can point at.

    ``resolver`` is injectable so tests can simulate DNS without the network.
    The returned ``pinned_ip`` is the address a transport must connect to.
    """

    if not isinstance(url, str) or not url.strip():
        raise UnsafeDestination("URL is empty")
    if any(ord(char) < 32 or char.isspace() for char in url) or "\\" in url:
        raise UnsafeDestination("URL contains whitespace, a control character, or a backslash")
    if len(url) > 2048:
        raise UnsafeDestination("URL is too long")

    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise UnsafeDestination(f"invalid URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UnsafeDestination(f"unsupported URL scheme {scheme or '(missing)'!r}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeDestination("URLs with credentials are not allowed")
    if parts.hostname is None:
        raise UnsafeDestination("URL is missing a hostname")
    if "%" in parts.netloc:
        raise UnsafeDestination("encoded characters in the hostname are not allowed")

    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeDestination(f"invalid URL port: {exc}") from exc
    if port == 0:
        raise UnsafeDestination("port 0 is not allowed")
    resolved_port = port or (443 if scheme == "https" else 80)

    try:
        host = canonical_host(parts.hostname)
    except UnsafeDestination:
        raise
    if is_blocked_hostname(host):
        raise UnsafeDestination(f"blocked hostname {host}")

    if is_ip_literal(host):
        reason = ip_block_reason(host)
        if reason:
            raise UnsafeDestination(reason)
        ips = (host,)
    else:
        try:
            found = resolver(host, resolved_port)
        except FetchError:
            raise
        except UnsafeDestination:
            raise
        if not found:
            raise FetchError(f"DNS lookup returned no addresses for {host}")
        checked: list[str] = []
        for ip in found:
            reason = ip_block_reason(ip)
            if reason:
                raise UnsafeDestination(f"{host} resolved to a blocked address ({reason})")
            if ip not in checked:
                checked.append(ip)
        ips = tuple(checked)

    path = parts.path or "/"
    rebuilt = urlunsplit((scheme, _format_netloc(host, resolved_port, scheme), path, parts.query, ""))
    return ResolvedURL(
        url=rebuilt,
        scheme=scheme,
        host=host,
        port=resolved_port,
        ips=ips,
        pinned_ip=ips[0],
        origin=origin_of(scheme, host, resolved_port),
    )

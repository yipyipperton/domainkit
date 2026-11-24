"""Turn loose domain input into one homepage URL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from domainkit.errors import InvalidDomain, UnsafeDestination
from domainkit.net import canonical_host, is_ip_literal

_DOMAIN_RE_HOST = (
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


@dataclass(frozen=True)
class NormalizedDomain:
    """Normalized company domain and the first URL to request."""

    raw: str
    domain: str
    host: str
    fetch_url: str


def _strip_www(host: str) -> str:
    labels = host.split(".")
    if labels[0] == "www" and len(labels) >= 3:
        return ".".join(labels[1:])
    return host


def normalize_domain(value: str) -> NormalizedDomain:
    """Accept ``example.com``, ``https://example.com/``, or ``www.example.com``.

    A leading ``www`` is removed from the reported domain and kept on the
    first request, so a www-only site still answers. Paths and queries are
    ignored: enrichment always starts at the site root. An explicit ``http``
    scheme is preserved; a bare domain uses https.
    """

    if not isinstance(value, str):
        raise InvalidDomain("domain must be a string")
    raw = value.strip()
    if not raw:
        raise InvalidDomain("domain is empty")
    if any(ord(char) < 32 or char.isspace() for char in raw) or "\\" in raw:
        raise InvalidDomain("domain contains whitespace or a control character")

    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidDomain(f"invalid domain: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise InvalidDomain("only http and https domains are supported")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidDomain("domains with credentials are not supported")
    if parts.hostname is None:
        raise InvalidDomain("domain is missing a hostname")

    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidDomain(f"invalid port: {exc}") from exc
    if port == 0:
        raise InvalidDomain("port 0 is not allowed")

    try:
        host = canonical_host(parts.hostname)
    except UnsafeDestination as exc:
        raise InvalidDomain(str(exc)) from exc

    if is_ip_literal(host):
        raise InvalidDomain("IP addresses are not company domains")

    if not re.fullmatch(_DOMAIN_RE_HOST, host):
        raise InvalidDomain(f"{host} is not a public domain name")

    domain = _strip_www(host)
    if not re.fullmatch(_DOMAIN_RE_HOST, domain):
        raise InvalidDomain(f"{domain} is not a public domain name")

    resolved_port = port or (443 if scheme == "https" else 80)
    default_port = 443 if scheme == "https" else 80
    netloc = host if resolved_port == default_port else f"{host}:{resolved_port}"
    fetch_url = f"{scheme}://{netloc}/"
    return NormalizedDomain(raw=raw, domain=domain, host=host, fetch_url=fetch_url)

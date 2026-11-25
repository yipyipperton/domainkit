"""Safe HTTP fetch: robots.txt, timeouts, size caps, and pinned public IPs.

Redirects are followed manually. Every hop is validated again, including DNS,
so a public page cannot redirect the client onto a private address.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
import urllib3
import urllib3.util.connection

from domainkit.constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CRAWL_DELAY,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_PER_HOST_DELAY,
    DEFAULT_TIMEOUT,
    USER_AGENT,
)
from domainkit.errors import FetchError, ResponseTooLarge, RobotsDenied, UnsafeDestination
from domainkit.net import ResolvedURL, default_resolver, ip_block_reason, validate_destination

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
Resolver = Callable[[str, int], list[str]]


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    requested_url: str


@dataclass
class RobotsPolicy:
    parser: RobotFileParser
    status: str
    http_status: int | None
    detail: str
    crawl_delay: float | None
    url: str


def header_get(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def normalize_headers(headers) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        normalized[str(key).lower()] = value if isinstance(value, str) else str(value)
    return normalized


def read_limited(chunks, max_bytes: int) -> bytes:
    """Read an iterable of bytes and abort past ``max_bytes``."""

    collected: list[bytes] = []
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        if not isinstance(chunk, bytes):
            raise FetchError("response body was not bytes")
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"response exceeded {max_bytes} bytes")
        collected.append(chunk)
    return b"".join(collected)


def reject_declared_length(value: str | None, max_bytes: int) -> None:
    if value is None:
        return
    try:
        declared = int(value)
    except (TypeError, ValueError):
        return
    if declared > max_bytes:
        raise ResponseTooLarge(f"Content-Length {declared} exceeds {max_bytes} bytes")


def make_session() -> requests.Session:
    """A session that ignores HTTP(S)_PROXY so a proxy cannot bypass IP checks."""

    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    return session


def _connect_pinned(conn, pinned_ip: str):
    """Open a TCP connection to an already-checked address."""

    try:
        return urllib3.util.connection.create_connection(
            (pinned_ip, conn.port),
            conn.timeout,
            source_address=getattr(conn, "source_address", None),
            socket_options=getattr(conn, "socket_options", None),
        )
    except socket.timeout as exc:
        raise urllib3.exceptions.ConnectTimeoutError(
            conn, f"timed out connecting to {pinned_ip}"
        ) from exc
    except OSError as exc:
        raise urllib3.exceptions.NewConnectionError(
            conn, f"failed to connect to {pinned_ip}: {exc}"
        ) from exc


class _PinnedAdapter(requests.adapters.HTTPAdapter):
    """Connect to a pinned IP while keeping the original hostname for TLS."""

    def __init__(self, pinned_ip: str, **kwargs) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pinned = self._pinned_ip

        class PinnedHTTP(urllib3.connection.HTTPConnection):
            def _new_conn(self):  # type: ignore[override]
                return _connect_pinned(self, pinned)

        class PinnedHTTPS(urllib3.connection.HTTPSConnection):
            def _new_conn(self):  # type: ignore[override]
                return _connect_pinned(self, pinned)

        self.poolmanager = urllib3.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = {
            "http": type("_PinnedHTTPPool", (urllib3.HTTPConnectionPool,), {"ConnectionCls": PinnedHTTP}),
            "https": type(
                "_PinnedHTTPSPool",
                (urllib3.HTTPSConnectionPool,),
                {"ConnectionCls": PinnedHTTPS},
            ),
        }


class RequestsTransport:
    """Production transport. Refuses a pinned IP that is not public."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        connect_timeout: float,
        max_bytes: int,
        pinned_ip: str,
    ) -> RawResponse:
        reason = ip_block_reason(pinned_ip)
        if reason:
            raise UnsafeDestination(reason)

        session = make_session()
        adapter = _PinnedAdapter(pinned_ip)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        try:
            try:
                # Session.send prepares a next redirect even when redirects are
                # disabled, reading its body first. Use the pinned adapter directly.
                prepared = session.prepare_request(requests.Request(method, url, headers=headers))
                response = adapter.send(
                    prepared,
                    timeout=(connect_timeout, timeout),
                    stream=True,
                    verify=True,
                    proxies={},
                )
            except requests.exceptions.Timeout as exc:
                raise FetchError(f"timed out fetching {url}") from exc
            except requests.exceptions.RequestException as exc:
                raise FetchError(f"request failed for {url}: {exc}") from exc
            try:
                header_map = normalize_headers(response.headers)
                if response.status_code in _REDIRECT_STATUSES:
                    return RawResponse(response.status_code, header_map, b"", url)
                reject_declared_length(header_map.get("content-length"), max_bytes)
                try:
                    body = read_limited(response.iter_content(chunk_size=65536), max_bytes)
                except requests.exceptions.Timeout as exc:
                    raise FetchError(f"timed out fetching {url}") from exc
                except requests.exceptions.RequestException as exc:
                    raise FetchError(f"request failed for {url}: {exc}") from exc
            finally:
                response.close()
        finally:
            session.close()
        return RawResponse(status=response.status_code, headers=header_map, body=body, url=url)


def _allow_parser() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Allow: /"])
    parser.allow_all = True
    return parser


def _deny_parser() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    parser.disallow_all = True
    return parser


@dataclass
class SafeClient:
    """Fetch public pages for one enrichment run.

    Pass a ``transport`` and ``resolver`` in tests. The defaults use DNS and
    ``requests``, pin the connection to a checked IP, and ignore environment
    proxies.
    """

    transport: RequestsTransport | None = None
    resolver: Resolver | None = None
    user_agent: str = USER_AGENT
    timeout: float = DEFAULT_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_bytes: int = DEFAULT_MAX_BYTES
    per_host_delay: float = DEFAULT_PER_HOST_DELAY
    max_crawl_delay: float = DEFAULT_MAX_CRAWL_DELAY
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    standard_ports_only: bool = False
    max_requests: int | None = None
    warnings: list[str] = field(default_factory=list)
    _request_count: int = field(default=0, init=False)
    _robots: dict[str, RobotsPolicy] = field(default_factory=dict)
    _last_request: dict[str, float] = field(default_factory=dict)
    _crawl_warned: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.transport is None:
            self.transport = RequestsTransport()
        if self.resolver is None:
            self.resolver = default_resolver

    def _resolve(self, url: str) -> ResolvedURL:
        return validate_destination(url, self.resolver, standard_ports_only=self.standard_ports_only)

    def get(self, url: str) -> FetchResult:
        return self._fetch(url, check_robots=True)

    def robot_policy(self, url: str) -> RobotsPolicy | None:
        try:
            resolved = self._resolve(url)
        except (UnsafeDestination, FetchError):
            return None
        return self._robots.get(resolved.origin)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        }

    def _fetch(self, url: str, *, check_robots: bool) -> FetchResult:
        current = url
        seen: set[str] = set()
        redirects = 0
        while True:
            if self.max_requests is not None and self._request_count >= self.max_requests:
                raise FetchError("hosted lookup reached its request limit")
            resolved = self._resolve(current)
            current = resolved.url
            if current in seen:
                raise FetchError(f"redirect loop at {current}")
            seen.add(current)
            if check_robots:
                policy = self._ensure_robots(resolved)
                if not policy.parser.can_fetch(self.user_agent, current):
                    raise RobotsDenied(current, self._deny_reason(resolved, policy))
            if self.max_requests is not None and self._request_count >= self.max_requests:
                raise FetchError("hosted lookup reached its request limit")
            self._request_count += 1
            self._pace(resolved)
            assert self.transport is not None
            response = self.transport.request(
                "GET",
                current,
                headers=self._headers(),
                timeout=self.timeout,
                connect_timeout=self.connect_timeout,
                max_bytes=self.max_bytes,
                pinned_ip=resolved.pinned_ip,
            )
            location = header_get(response.headers, "location")
            if response.status in _REDIRECT_STATUSES:
                if not location or not location.strip():
                    raise FetchError(f"redirect from {current} is missing a Location header")
                redirects += 1
                if redirects > self.max_redirects:
                    raise FetchError(f"exceeded {self.max_redirects} redirects from {url}")
                current = urljoin(current, location.strip())
                continue
            if len(response.body) > self.max_bytes:
                raise ResponseTooLarge(f"response exceeded {self.max_bytes} bytes")
            return FetchResult(
                url=current,
                status=response.status,
                headers=normalize_headers(response.headers),
                body=response.body,
                requested_url=url,
            )

    def _deny_reason(self, resolved: ResolvedURL, policy: RobotsPolicy) -> str:
        if policy.status == "unavailable":
            return (
                f"robots.txt for {resolved.origin} is unavailable ({policy.detail}); "
                "not fetching the page"
            )
        if policy.status == "denied":
            return f"robots.txt for {resolved.origin} denies access ({policy.detail})"
        return f"robots.txt disallows {resolved.url}"

    def _ensure_robots(self, resolved: ResolvedURL) -> RobotsPolicy:
        cached = self._robots.get(resolved.origin)
        if cached is not None:
            return cached
        robots_url = resolved.origin + "/robots.txt"
        try:
            result = self._fetch(robots_url, check_robots=False)
        except UnsafeDestination:
            raise
        except FetchError as exc:
            policy = RobotsPolicy(
                parser=_deny_parser(),
                status="unavailable",
                http_status=None,
                detail=str(exc),
                crawl_delay=None,
                url=robots_url,
            )
            self._robots[resolved.origin] = policy
            self.warnings.append(self._deny_reason(resolved, policy))
            return policy

        if result.status in {401, 403, 429} or 500 <= result.status <= 599:
            policy = RobotsPolicy(
                parser=_deny_parser(),
                status="denied" if result.status in {401, 403} else "unavailable",
                http_status=result.status,
                detail=f"HTTP {result.status}",
                crawl_delay=None,
                url=robots_url,
            )
            self._robots[resolved.origin] = policy
            self.warnings.append(self._deny_reason(resolved, policy))
            return policy

        if result.status >= 400:
            policy = RobotsPolicy(
                parser=_allow_parser(),
                status="absent",
                http_status=result.status,
                detail=f"HTTP {result.status}",
                crawl_delay=None,
                url=robots_url,
            )
            self._robots[resolved.origin] = policy
            return policy

        text = result.body.decode("utf-8", errors="replace")
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        delay = parser.crawl_delay(self.user_agent)
        policy = RobotsPolicy(
            parser=parser,
            status="ok",
            http_status=result.status,
            detail="parsed",
            crawl_delay=float(delay) if delay else None,
            url=result.url,
        )
        self._robots[resolved.origin] = policy
        return policy

    def _pace(self, resolved: ResolvedURL) -> None:
        minimum = self.per_host_delay
        policy = self._robots.get(resolved.origin)
        if policy and policy.crawl_delay:
            delay = policy.crawl_delay
            if delay > self.max_crawl_delay:
                if resolved.host not in self._crawl_warned:
                    self.warnings.append(
                        f"crawl-delay {delay:g}s for {resolved.host} capped at {self.max_crawl_delay:g}s"
                    )
                    self._crawl_warned.add(resolved.host)
                delay = self.max_crawl_delay
            minimum = max(minimum, delay)
        now = self.clock()
        last = self._last_request.get(resolved.host)
        if last is not None and minimum > 0:
            wait = minimum - (now - last)
            if wait > 0:
                self.sleeper(wait)
                now = self.clock()
        self._last_request[resolved.host] = now

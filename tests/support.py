"""Local HTTP stand-ins. Tests never open a real socket."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from domainkit.fetch import RawResponse, SafeClient

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC_IP = "93.184.216.34"
NORTHWIND_INPUT = "https://www.Northwind.example/about"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@dataclass
class FakeTransport:
    routes: dict[str, RawResponse | Exception]
    calls: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    seen_headers: list[dict[str, str]] = field(default_factory=list)

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
        self.calls.append(url)
        self.pins.append(pinned_ip)
        self.seen_headers.append(dict(headers))
        if url not in self.routes:
            return RawResponse(status=404, headers={}, body=b"not found", url=url)
        item = self.routes[url]
        if isinstance(item, Exception):
            raise item
        return item


def response(status: int, headers: dict | None = None, body: bytes = b"") -> RawResponse:
    return RawResponse(status=status, headers=dict(headers or {}), body=body, url="")


def html_response(body: bytes, **headers: str) -> RawResponse:
    merged = {"content-type": "text/html; charset=utf-8"}
    merged.update({key.lower(): value for key, value in headers.items()})
    return response(200, merged, body)


def northwind_transport() -> FakeTransport:
    homepage = html_response(
        load_fixture("homepage.html"),
        Server="cloudflare",
        **{"cf-ray": "abc123"},
    )
    return FakeTransport(
        routes={
            "https://www.northwind.example/": response(
                301,
                {"Location": "https://northwind.example/"},
                b"",
            ),
            "https://northwind.example/robots.txt": response(200, {"content-type": "text/plain"}, load_fixture("robots.txt")),
            "https://northwind.example/": homepage,
            "https://northwind.example/contact": html_response(load_fixture("contact.html")),
            "https://northwind.example/about": html_response(load_fixture("about.html")),
        }
    )


def northwind_client() -> tuple[SafeClient, FakeTransport]:
    transport = northwind_transport()
    client = SafeClient(
        transport=transport,
        resolver=lambda host, port: [PUBLIC_IP],
        per_host_delay=0,
        timeout=2,
        connect_timeout=1,
    )
    return client, transport


def public_resolver(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise AssertionError(f"unexpected DNS lookup for {host}")
        return list(mapping[host])

    return resolve

import socket

import pytest

from domainkit.constants import USER_AGENT
from domainkit.errors import FetchError, ResponseTooLarge, UnsafeDestination
from domainkit.fetch import (
    RawResponse,
    RequestsTransport,
    SafeClient,
    make_session,
    read_limited,
    reject_declared_length,
)
from tests.support import FakeTransport, public_resolver, response

PUBLIC = "93.184.216.34"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(routes, resolver=None, **kwargs) -> tuple[SafeClient, FakeTransport]:
    transport = FakeTransport(routes)
    settings = {"per_host_delay": 0, "timeout": 2, "connect_timeout": 1}
    settings.update(kwargs)
    safe = SafeClient(
        transport=transport,
        resolver=resolver or (lambda host, port: [PUBLIC]),
        **settings,
    )
    return safe, transport


def test_user_agent_identifies_the_tool_and_not_a_person():
    assert USER_AGENT.startswith("DomainKit/")
    assert "robots.txt" in USER_AGENT
    assert "@" not in USER_AGENT


def test_private_resolution_never_calls_the_transport():
    safe, transport = client(
        {},
        resolver=public_resolver({"evil.example": ["8.8.8.8", "10.0.0.9"]}),
    )
    with pytest.raises(UnsafeDestination, match="10.0.0.9"):
        safe.get("https://evil.example/")
    assert transport.calls == []


def test_blocked_ip_literal_never_calls_the_transport():
    safe, transport = client({})
    with pytest.raises(UnsafeDestination):
        safe.get("http://169.254.169.254/latest/meta-data")
    assert transport.calls == []


def test_redirect_to_private_ip_is_not_followed():
    safe, transport = client(
        {
            "https://public.example/": response(302, {"Location": "http://10.0.0.8/secret"}),
        }
    )
    with pytest.raises(UnsafeDestination, match="10.0.0.8"):
        safe.get("https://public.example/")
    assert transport.calls == [
        "https://public.example/robots.txt",
        "https://public.example/",
    ]


def test_redirect_to_private_hostname_is_not_followed():
    safe, transport = client(
        {
            "https://public.example/": response(302, {"Location": "http://intranet.example/secret"}),
        },
        resolver=public_resolver(
            {
                "public.example": [PUBLIC],
                "intranet.example": ["192.168.1.20"],
            }
        ),
    )
    with pytest.raises(UnsafeDestination, match="192.168.1"):
        safe.get("https://public.example/")
    assert "http://intranet.example/secret" not in transport.calls
    assert "https://intranet.example/robots.txt" not in transport.calls


@pytest.mark.parametrize(
    "location",
    ["file:///etc/passwd", "javascript:alert(1)", "ftp://files.example/a", "gopher://example.com/"],
)
def test_redirect_scheme_is_rejected(location):
    safe, transport = client(
        {"https://public.example/": response(302, {"Location": location})}
    )
    with pytest.raises(UnsafeDestination):
        safe.get("https://public.example/")
    assert transport.calls == ["https://public.example/robots.txt", "https://public.example/"]


def test_relative_redirect_stays_on_the_same_host():
    safe, transport = client(
        {
            "https://public.example/old": response(302, {"Location": "/done"}),
            "https://public.example/done": response(200, {"content-type": "text/html"}, b"<html>ok</html>"),
        }
    )
    result = safe.get("https://public.example/old")
    assert result.url == "https://public.example/done"
    assert result.body == b"<html>ok</html>"
    assert transport.calls == [
        "https://public.example/robots.txt",
        "https://public.example/old",
        "https://public.example/done",
    ]


def test_five_redirects_can_land_and_a_sixth_cannot():
    routes = {"https://public.example/robots.txt": response(404)}
    for index in range(5):
        routes[f"https://public.example/{index}"] = response(302, {"Location": f"/{index + 1}"})
    routes["https://public.example/5"] = response(200, {"content-type": "text/html"}, b"landed")
    safe, transport = client(routes, max_redirects=5)
    result = safe.get("https://public.example/0")
    assert result.url == "https://public.example/5"
    assert "https://public.example/6" not in transport.calls

    routes["https://public.example/5"] = response(302, {"Location": "/6"})
    safe, transport = client(routes, max_redirects=5)
    with pytest.raises(FetchError, match="exceeded 5 redirects"):
        safe.get("https://public.example/0")
    assert "https://public.example/6" not in transport.calls


def test_redirect_loop_stops_without_another_request():
    safe, transport = client(
        {
            "https://public.example/a": response(302, {"Location": "/b"}),
            "https://public.example/b": response(302, {"Location": "/a"}),
        }
    )
    with pytest.raises(FetchError, match="redirect loop"):
        safe.get("https://public.example/a")
    assert transport.calls == [
        "https://public.example/robots.txt",
        "https://public.example/a",
        "https://public.example/b",
    ]


def test_redirect_without_location_is_an_error():
    safe, _transport = client({"https://public.example/": response(302, {}, b"")})
    with pytest.raises(FetchError, match="Location"):
        safe.get("https://public.example/")


def test_oversized_body_is_rejected():
    safe, _transport = client(
        {"https://public.example/": response(200, {"content-type": "text/html"}, b"x" * 50)},
        max_bytes=20,
    )
    with pytest.raises(ResponseTooLarge):
        safe.get("https://public.example/")


def test_read_limited_and_declared_length():
    assert read_limited([b"abc", b"de"], 5) == b"abcde"
    with pytest.raises(ResponseTooLarge):
        read_limited([b"abc", b"de"], 4)
    reject_declared_length("4", 4)
    with pytest.raises(ResponseTooLarge):
        reject_declared_length("5", 4)
    reject_declared_length("nope", 1)


def test_user_agent_is_sent():
    safe, transport = client(
        {"https://public.example/": response(200, {"content-type": "text/plain"}, b"hi")}
    )
    safe.get("https://public.example/")
    assert transport.seen_headers
    assert all(item["User-Agent"] == USER_AGENT for item in transport.seen_headers)


def test_per_host_delay_is_applied_between_requests():
    clock = Clock()
    safe, _transport = client(
        {"https://public.example/": response(200, {"content-type": "text/plain"}, b"hi")},
        per_host_delay=0.5,
        sleeper=clock.sleep,
        clock=clock.monotonic,
    )
    safe.get("https://public.example/")
    safe.get("https://public.example/")
    assert clock.slept == [0.5, 0.5]


def test_session_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
    session = make_session()
    assert session.trust_env is False
    assert session.proxies == {}


def test_requests_transport_refuses_a_private_pin_without_connecting():
    transport = RequestsTransport()
    with pytest.raises(UnsafeDestination):
        transport.request(
            "GET",
            "http://example.com/",
            headers={"User-Agent": USER_AGENT},
            timeout=1,
            connect_timeout=1,
            max_bytes=1000,
            pinned_ip="127.0.0.1",
        )


def test_requests_transport_connects_only_to_the_pinned_ip(monkeypatch):
    seen = []

    def fake_create_connection(address, *args, **kwargs):
        seen.append(address)
        raise OSError("stopped before a real connection")

    monkeypatch.setattr("urllib3.util.connection.create_connection", fake_create_connection)
    transport = RequestsTransport()
    with pytest.raises(FetchError):
        transport.request(
            "GET",
            "http://example.com/health",
            headers={"User-Agent": USER_AGENT},
            timeout=1,
            connect_timeout=1,
            max_bytes=1000,
            pinned_ip=PUBLIC,
        )
    assert seen
    assert all(address[0] == PUBLIC for address in seen)
    assert all(address[0] != "example.com" for address in seen)

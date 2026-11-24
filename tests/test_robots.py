import pytest

from domainkit.errors import FetchError, RobotsDenied
from domainkit.fetch import SafeClient
from tests.support import PUBLIC_IP, FakeTransport, response


def make(routes, **kwargs) -> tuple[SafeClient, FakeTransport]:
    transport = FakeTransport(routes)
    settings = {"per_host_delay": 0}
    settings.update(kwargs)
    client = SafeClient(
        transport=transport,
        resolver=lambda host, port: [PUBLIC_IP],
        **settings,
    )
    return client, transport


def test_missing_robots_allows_the_page():
    client, transport = make(
        {"https://public.example/": response(200, {"content-type": "text/html"}, b"<html>ok</html>")}
    )
    result = client.get("https://public.example/")
    assert result.status == 200
    assert transport.calls[0] == "https://public.example/robots.txt"
    policy = client.robot_policy("https://public.example/")
    assert policy is not None
    assert policy.status == "absent"


def test_disallow_skips_the_page_itself():
    client, transport = make(
        {
            "https://public.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nDisallow: /\n",
            )
        }
    )
    with pytest.raises(RobotsDenied, match="disallows"):
        client.get("https://public.example/")
    assert transport.calls == ["https://public.example/robots.txt"]


def test_path_rule_blocks_only_that_path():
    client, transport = make(
        {
            "https://public.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nDisallow: /contact\n",
            ),
            "https://public.example/": response(200, {"content-type": "text/html"}, b"<html>home</html>"),
        }
    )
    assert client.get("https://public.example/").status == 200
    with pytest.raises(RobotsDenied, match="/contact"):
        client.get("https://public.example/contact")
    assert "https://public.example/contact" not in transport.calls


def test_specific_user_agent_beats_the_star_group():
    robots = (
        b"User-agent: DomainKit\n"
        b"Disallow: /secret\n"
        b"\n"
        b"User-agent: *\n"
        b"Disallow: /contact\n"
    )
    client, transport = make(
        {
            "https://public.example/robots.txt": response(200, {"content-type": "text/plain"}, robots),
            "https://public.example/": response(200, {"content-type": "text/html"}, b"<html></html>"),
            "https://public.example/contact": response(200, {"content-type": "text/html"}, b"<html></html>"),
        }
    )
    assert client.get("https://public.example/contact").status == 200
    with pytest.raises(RobotsDenied):
        client.get("https://public.example/secret")
    assert "https://public.example/secret" not in transport.calls


def test_server_error_on_robots_denies_the_site():
    client, transport = make(
        {"https://down.example/robots.txt": response(503, {"content-type": "text/plain"}, b"unavailable")}
    )
    with pytest.raises(RobotsDenied, match="unavailable"):
        client.get("https://down.example/")
    assert transport.calls == ["https://down.example/robots.txt"]
    assert client.robot_policy("https://down.example/").status == "unavailable"


def test_robots_fetch_failure_denies_the_site():
    client, transport = make(
        {"https://down.example/robots.txt": FetchError("timed out fetching robots")}
    )
    with pytest.raises(RobotsDenied, match="unavailable"):
        client.get("https://down.example/")
    assert transport.calls == ["https://down.example/robots.txt"]


def test_forbidden_robots_denies_the_site():
    client, _transport = make({"https://private.example/robots.txt": response(403, {}, b"")})
    with pytest.raises(RobotsDenied, match="403"):
        client.get("https://private.example/")


def test_crawl_delay_is_honored_and_capped():
    class Clock:
        def __init__(self):
            self.now = 0.0
            self.slept = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.slept.append(seconds)
            self.now += seconds

    clock = Clock()
    client, _transport = make(
        {
            "https://slow.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nCrawl-delay: 2\nDisallow:\n",
            ),
            "https://slow.example/": response(200, {"content-type": "text/html"}, b"<html></html>"),
        },
        per_host_delay=0,
        sleeper=clock.sleep,
        clock=clock.monotonic,
    )
    client.get("https://slow.example/")
    assert clock.slept == [2.0]

    clock = Clock()
    client, _transport = make(
        {
            "https://slow.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nCrawl-delay: 9\nDisallow:\n",
            ),
            "https://slow.example/": response(200, {"content-type": "text/html"}, b"<html></html>"),
        },
        per_host_delay=0,
        max_crawl_delay=5,
        sleeper=clock.sleep,
        clock=clock.monotonic,
    )
    client.get("https://slow.example/")
    assert clock.slept == [5.0]
    assert any("capped" in warning for warning in client.warnings)

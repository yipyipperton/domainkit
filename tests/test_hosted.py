import pytest
from fastapi.testclient import TestClient

from app import app
from domainkit.api import create_app
from domainkit.constants import USER_AGENT
from domainkit.enricher import enrich
from domainkit.errors import FetchError, UnsafeDestination
from domainkit.fetch import SafeClient
from tests.support import NORTHWIND_INPUT, FakeTransport, northwind_client, response


def test_vercel_entrypoint_serves_the_existing_lookup_ui_and_health():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="lookup"' in page.text
        assert page.headers["referrer-policy"] == "no-referrer"
        assert client.get("/health").json() == {"status": "ok"}


def test_hosted_lookup_serves_structured_data_and_blocks_recursion():
    web = create_app(hosted=True)
    crawl, transport = northwind_client()
    web.state.enrich = lambda domain: enrich(domain, client=crawl)
    with TestClient(web) as client:
        r = client.get("/enrich", params={"domain": NORTHWIND_INPUT})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert r.json()["company_name"]["value"] == "Northwind Labs"
        assert r.json()["emails"]
        calls = transport.calls[:]
        r = client.get("/enrich", params={"domain": NORTHWIND_INPUT},
                       headers={"User-Agent": USER_AGENT})
        assert r.status_code == 403
        assert transport.calls == calls


def test_hosted_lookup_is_rate_limited_but_health_is_not():
    web = create_app(hosted=True)
    web.state.enrich = lambda domain: {"domain": domain}
    with TestClient(web) as client:
        for _ in range(30):
            assert client.get("/enrich", params={"domain": "example.com"}).status_code == 200
        r = client.get("/enrich", params={"domain": "example.com"})
        assert r.status_code == 429
        assert r.headers["retry-after"] == "60"
        assert client.get("/health").status_code == 200


def test_hosted_redirect_is_restricted_to_standard_ports():
    start = "https://company.example/"
    transport = FakeTransport({start: response(302, {"location": "https://company.example:8443/"})})
    safe = SafeClient(transport=transport, resolver=lambda *_: ["8.8.8.8"],
                      standard_ports_only=True, per_host_delay=0)
    with pytest.raises(UnsafeDestination, match="blocked port"):
        safe.get(start)
    assert transport.calls == [start + "robots.txt", start]


def test_request_budget_counts_robots_lookup():
    start = "https://company.example/"
    transport = FakeTransport({})
    safe = SafeClient(transport=transport, resolver=lambda *_: ["8.8.8.8"], max_requests=1)
    with pytest.raises(FetchError, match="request limit"):
        safe.get(start)
    assert transport.calls == [start + "robots.txt"]

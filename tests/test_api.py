from fastapi.testclient import TestClient

from domainkit.api import create_app
from domainkit.enricher import enrich
from domainkit.fetch import SafeClient
from tests.support import NORTHWIND_INPUT, FakeTransport, northwind_client


def test_health():
    client = TestClient(create_app())
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_missing_domain_is_rejected():
    response = TestClient(create_app()).get("/enrich")
    assert response.status_code == 422


def test_private_and_invalid_domains_are_400():
    client = TestClient(create_app())
    for domain in ("http://127.0.0.1", "10.0.0.1", "localhost", "not a domain", "file://example.com"):
        response = client.get("/enrich", params={"domain": domain})
        assert response.status_code == 400, domain
        assert response.json()["detail"]


def test_hostname_resolving_to_a_private_address_is_400_and_not_fetched():
    transport = FakeTransport({})
    safe = SafeClient(transport=transport, resolver=lambda host, port: ["10.1.2.3"], per_host_delay=0)
    app = create_app()
    app.state.enrich = lambda domain: enrich(domain, client=safe)
    response = TestClient(app).get("/enrich", params={"domain": "evil.example"})
    assert response.status_code == 400
    assert "10.1.2.3" in response.json()["detail"]
    assert transport.calls == []


def test_enrich_endpoint_returns_fixture_extraction():
    safe, _transport = northwind_client()
    app = create_app()
    app.state.enrich = lambda domain: enrich(domain, client=safe)
    response = TestClient(app).get("/enrich", params={"domain": NORTHWIND_INPUT})
    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "northwind.example"
    assert body["final_url"] == "https://northwind.example/"
    assert body["company_name"]["value"] == "Northwind Labs"
    assert body["company_name"]["inferred"] is False
    assert body["emails"]
    assert body["error"] is None

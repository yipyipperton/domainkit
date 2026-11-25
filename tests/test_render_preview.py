import pytest
from fastapi.testclient import TestClient

from render_app import create_preview
from tests.support import NORTHWIND_INPUT, northwind_client
from domainkit.enricher import enrich


def test_preview_has_no_default_password():
    for invalid in ("", "short", "é" * 32):
        with pytest.raises(RuntimeError, match="DOMAINKIT_PREVIEW_PASSWORD"):
            create_preview(invalid)


def test_preview_requires_password_but_allows_health():
    with TestClient(create_preview("not-a-real-password")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 401
        assert client.get("/enrich", params={"domain": NORTHWIND_INPUT}).status_code == 401
        assert client.get("/", auth=("domainkit", "wrong password")).status_code == 401
        assert client.get("/", auth=("domainkit", "not-a-real-password")).status_code == 200


def test_preview_runs_full_crawler_and_rejects_cross_site_lookup():
    app = create_preview("not-a-real-password")
    crawl, _ = northwind_client()
    app.state.enrich = lambda target: enrich(target, client=crawl)
    with TestClient(app) as client:
        auth = ("domainkit", "not-a-real-password")
        params = {"domain": NORTHWIND_INPUT}
        blocked = client.get("/enrich", params=params, auth=auth, headers={"Sec-Fetch-Site": "cross-site"})
        assert blocked.status_code == 403
        response = client.get("/enrich", params=params, auth=auth)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        info = response.json()
        assert info["company_name"]["value"] == "Northwind Labs"
        assert info["emails"] and info["phones"]
        assert len(info["pages"]) > 1

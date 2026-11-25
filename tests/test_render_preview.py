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


def test_private_preview_admits_regular_repeat_lookups():
    app = create_preview("not-a-real-password")
    app.state.enrich = lambda domain: {"domain": domain}
    with TestClient(app) as client:
        for _ in range(31):
            response = client.get("/enrich", params={"domain": "example.com"},
                                  auth=("domainkit", "not-a-real-password"))
            assert response.status_code == 200


def test_private_preview_queues_a_fourth_lookup_without_increasing_crawl_concurrency():
    import asyncio
    import threading

    import httpx

    app = create_preview("not-a-real-password")
    reached_three = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    entered = 0
    in_progress = 0
    maximum = 0

    def crawl(domain):
        nonlocal entered, in_progress, maximum
        with lock:
            entered += 1
            in_progress += 1
            maximum = max(maximum, in_progress)
            if entered == 3:
                reached_three.set()
        try:
            assert release.wait(5)
            return {"domain": domain}
        finally:
            with lock:
                in_progress -= 1

    app.state.enrich = crawl

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            auth=("domainkit", "not-a-real-password"),
        ) as client:
            pending = [asyncio.create_task(client.get("/enrich", params={"domain": "example.com"}))
                       for _ in range(4)]
            try:
                assert await asyncio.to_thread(reached_three.wait, 3)
                assert all(not task.done() for task in pending)
            finally:
                release.set()
            responses = await asyncio.gather(*pending)
            assert [response.status_code for response in responses] == [200] * 4

    asyncio.run(run())
    assert entered == 4 and maximum == 3


def test_preview_exposes_deep_search_only_to_authenticated_client():
    app = create_preview("not-a-real-password")
    app.state.enrich_deep = lambda domain: {"domain": domain, "crawl": {"mode": "deep"}}
    with TestClient(app) as client:
        auth = ("domainkit", "not-a-real-password")
        assert 'id="deep-search"' in client.get("/", auth=auth).text
        assert client.get("/enrich?domain=example.com&deep=true").status_code == 401
        result = client.get("/enrich?domain=example.com&deep=true", auth=auth)
        assert result.status_code == 200
        assert result.json()["crawl"]["mode"] == "deep"

import json

from domainkit.public_index import add_public_sources


def test_failed_crawl_keeps_error_and_adds_only_sourced_matching_addresses(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("DOMAINKIT_PUBLIC_INDEX", json.dumps({"roofing.test": [
        {"email": "office@roofing.test", "url": "https://directory.test/company"},
        {"email": "office@roofing.test", "url": "https://roofing.test/contact"},
        {"email": "wrong@other.test", "url": "https://directory.test/company"},
        {"email": "fake@roofing.test", "url": "javascript:alert(1)"},
    ]}))
    output = add_public_sources({"domain": "roofing.test", "emails": [], "error": "HTTPS challenge"})
    assert output["error"] == "HTTPS challenge"
    assert output["emails"] == []
    assert output["public_index"]["status"] == "saved_sources"
    rows = output["public_index"]["emails"]
    assert [item["value"] for item in rows] == ["office@roofing.test"]
    assert len(rows[0]["sources"]) == 2
    assert rows[0]["verification"] == "not_checked"


def test_optional_search_uses_evidence_not_inference(monkeypatch):
    monkeypatch.delenv("DOMAINKIT_PUBLIC_INDEX", raising=False)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test")
    class Reply:
        status_code = 200
        content = b"example"

        def json(self):
            return {"web": {"results": [{
                "url": "https://directory.test/source",
                "description": "<b>hello</b>@roofing.test",
                "extra_snippets": ["unrelated@other.test"],
            }]}}

    def get(url, **kwargs):
        assert url == "https://api.search.brave.com/res/v1/web/search"
        assert kwargs["allow_redirects"] is False
        return Reply()

    monkeypatch.setattr("domainkit.public_index.requests.get", get)
    index = add_public_sources({"domain": "roofing.test", "emails": []})["public_index"]
    assert index["status"] == "search_results"
    assert index["emails"][0]["value"] == "hello@roofing.test"

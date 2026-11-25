from pathlib import Path

from fastapi.testclient import TestClient

from domainkit.api import create_app

PAGE = Path("domainkit/page.html").read_text(encoding="utf-8")


def test_home_page_is_html_and_matches_the_shipped_file():
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == PAGE
    assert "Find email addresses published on a company’s website." in response.text
    assert "<h1>DomainKit</h1>" in response.text
    assert 'id="domain"' in response.text
    assert 'id="enrich"' in response.text
    assert ">Find emails</button>" in response.text
    assert "Copy JSON" in response.text
    assert "Raw JSON" in response.text
    assert "Not found" in response.text
    assert 'class="badge">inferred</span>' in response.text
    for label in (
        "Company",
        "Title",
        "Description",
        "Published emails",
        "Phones",
        "Social links",
        "Address",
        "Structured metadata",
        "Technologies",
        "Contact forms",
        "Booking links",
        "Chat widgets",
        "Pages fetched",
    ):
        assert label in response.text


def test_home_page_explains_blocked_robots_and_timeout_failures():
    html = TestClient(create_app()).get("/").text
    assert "That address is private or blocked. DomainKit only fetches public websites." in html
    assert "robots.txt does not allow DomainKit to read that site." in html
    assert "The site took too long to respond." in html
    assert "/enrich?domain=" in html


def test_home_page_does_not_replace_the_enrich_endpoint():
    client = TestClient(create_app())
    missing = client.get("/enrich")
    assert missing.status_code == 422
    assert missing.headers["content-type"].startswith("application/json")
    blocked = client.get("/enrich", params={"domain": "http://127.0.0.1"})
    assert blocked.status_code == 400
    assert "IP addresses are not company domains" in blocked.json()["detail"]

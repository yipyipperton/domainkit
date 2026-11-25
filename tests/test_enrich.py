import pytest

from domainkit.enricher import discover_pages, enrich
from domainkit.errors import FetchError, InvalidDomain, UnsafeDestination
from domainkit.fetch import SafeClient
from tests.support import (
    FakeTransport,
    NORTHWIND_INPUT,
    PUBLIC_IP,
    northwind_client,
    response,
)
from tests.test_fetch import client


def test_fixture_site_merges_homepage_contact_and_about():
    safe, transport = northwind_client()
    result = enrich(NORTHWIND_INPUT, client=safe)

    assert result["input"] == NORTHWIND_INPUT
    assert result["domain"] == "northwind.example"
    assert result["final_url"] == "https://northwind.example/"
    assert result["error"] is None
    assert result["company_name"]["value"] == "Northwind Labs"
    assert result["company_name"]["source"] == "json-ld"
    assert result["company_name"]["inferred"] is False
    assert result["website_title"]["value"] == "Northwind Labs | Home"
    assert result["meta_description"]["value"] == "Northwind Labs builds product analytics for B2B teams."
    assert result["meta_description"]["source"] == "meta"
    assert [item["value"] for item in result["emails"]] == [
        "hello@northwind.example",
        "press@northwind.example",
        "sales@northwind.example",
        "careers@northwind.example",
    ]
    assert "name@example.com" not in {item["value"] for item in result["emails"]}
    assert [item["value"] for item in result["phones"]] == [
        "+1-415-555-0148",
        "+1-415-555-0142",
        "(415) 555-0199",
    ]
    assert result["address"]["value"]["street"] == "100 Harbor Way"
    assert result["address"]["value"]["locality"] == "Sample City"
    assert result["address"]["inferred"] is False
    assert result["social_links"]["linkedin"][0]["source"] == "json-ld"
    assert result["social_links"]["twitter"]
    assert result["social_links"]["facebook"]
    assert result["social_links"]["instagram"]
    assert result["social_links"]["youtube"]
    assert result["social_links"]["tiktok"]
    assert result["social_links"]["github"]
    assert result["social_links"]["discord"]
    assert result["social_links"]["pinterest"] == []
    assert result["social_links"]["crunchbase"] == []
    assert all("intent" not in item["value"] for item in result["social_links"]["twitter"])
    assert all("sharer" not in item["value"] for item in result["social_links"]["facebook"])
    assert {item["provider"] for item in result["booking_links"]} == {"Calendly", "HubSpot Meetings"}
    assert {item["provider"] for item in result["live_chat"]} == {"Intercom", "Tawk"}
    assert {item["name"] for item in result["technologies"]} >= {
        "WordPress",
        "Google Analytics",
        "HubSpot",
        "jQuery",
        "Cloudflare",
    }
    assert [item["action"] for item in result["contact_forms"]] == ["https://northwind.example/thanks"]
    assert result["open_graph"]["og:site_name"] == "Northwind Labs"
    assert result["twitter"]["twitter:site"] == "@northwindlabs"
    assert result["json_ld"][0]["name"] == "Northwind Labs"
    assert result["robots"]["status"] == "ok"
    assert [page["url"] for page in result["pages"]] == [
        "https://northwind.example/",
        "https://northwind.example/contact",
        "https://northwind.example/about",
    ]
    assert transport.calls == [
        "https://www.northwind.example/robots.txt",
        "https://www.northwind.example/",
        "https://northwind.example/robots.txt",
        "https://northwind.example/",
        "https://northwind.example/contact",
        "https://northwind.example/about",
    ]
    assert "https://northwind.example/private" not in transport.calls
    assert "https://northwind.example/contact-us" not in transport.calls
    assert set(transport.pins) == {PUBLIC_IP}


def test_max_pages_one_stops_after_the_homepage():
    safe, transport = northwind_client()
    result = enrich("northwind.example", client=safe, max_pages=1)
    assert [page["url"] for page in result["pages"]] == ["https://northwind.example/"]
    assert "https://northwind.example/contact" not in transport.calls


def test_robots_disallow_is_recorded_and_not_fetched():
    safe, transport = client(
        {
            "https://public.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nDisallow: /contact\n",
            ),
            "https://public.example/": response(
                200,
                {"content-type": "text/html"},
                b"<html><head><title>Public</title></head><body><a href='/contact'>Contact</a></body></html>",
            ),
        }
    )
    result = enrich("public.example", client=safe)
    assert result["website_title"]["value"] == "Public"
    assert result["company_name"]["inferred"] is True
    assert any(page["url"].endswith("/contact") and page["ok"] is False for page in result["pages"])
    assert "https://public.example/contact" not in transport.calls


def test_homepage_blocked_by_robots_returns_null_fields():
    safe, _transport = client(
        {
            "https://public.example/robots.txt": response(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nDisallow: /\n",
            )
        }
    )
    result = enrich("public.example", client=safe)
    assert result["error"] is None
    assert result["final_url"] is None
    assert result["company_name"] is None
    assert result["emails"] == []
    assert result["pages"][0]["ok"] is False
    assert "robots.txt" in result["pages"][0]["error"]


def test_homepage_fetch_failure_is_an_error_field_not_an_exception():
    safe, _transport = client({"https://public.example/robots.txt": FetchError("timed out fetching robots")})
    result = enrich("public.example", client=safe)
    assert result["domain"] == "public.example"
    assert result["company_name"] is None
    assert "timed out" in result["pages"][0]["error"]


def test_private_redirect_from_a_public_homepage_is_raised():
    safe, transport = client(
        {"https://public.example/": response(302, {"Location": "http://127.0.0.1/admin"})}
    )
    with pytest.raises(UnsafeDestination):
        enrich("public.example", client=safe)
    assert "http://127.0.0.1/admin" not in transport.calls


def test_ip_input_is_rejected_by_the_public_api():
    with pytest.raises(InvalidDomain):
        enrich("http://10.0.0.1")


def test_pages_without_obvious_links_use_a_short_guess_list():
    pages = discover_pages([], "https://northwind.example/", 3)
    assert pages == [
        "https://northwind.example/contact",
        "https://northwind.example/contact-us",
        "https://northwind.example/about",
    ]


def test_linked_pages_win_over_guesses():
    pages = discover_pages(
        ["https://northwind.example/about", "https://other.example/contact", "https://northwind.example/team"],
        "https://northwind.example/",
        3,
    )
    assert pages == [
        "https://northwind.example/about",
        "https://northwind.example/team",
        "https://northwind.example/contact",
    ]


def test_refused_homepage_is_not_reported_as_empty_success():

    home = "https://blocked.example/"
    transport = FakeTransport({home: response(403, {"content-type": "text/html"}, b"<html>Forbidden</html>")})
    client = SafeClient(transport=transport, resolver=lambda *_: ["8.8.8.8"], per_host_delay=0)
    result = enrich(home, client=client)
    assert result["error"] == "The website refused this request (HTTP 403)."
    assert result["pages"][0]["ok"] is False
    assert result["emails"] == []


def test_challenge_response_is_not_parsed_as_the_homepage():

    home = "https://challenged.example/"
    transport = FakeTransport({
        home: response(403, {"content-type": "text/html", "cf-mitigated": "challenge"},
                       b"<html><title>Just a moment</title></html>")
    })
    client = SafeClient(transport=transport, resolver=lambda *_: ["8.8.8.8"], per_host_delay=0)
    result = enrich(home, client=client)
    assert result["error"] == "The website required a browser security check (Cloudflare challenge)."
    assert result["website_title"] is None
    assert transport.calls == [home + "robots.txt", home]

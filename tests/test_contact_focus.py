from domainkit.enricher import enrich
from domainkit.extract import clean_email, extract_page
from domainkit.fetch import SafeClient
from tests.support import FakeTransport, PUBLIC_IP, html_response, response

HOST = "https://bluepeak.example"


def test_more_published_email_formats_without_inventing_addresses():
    out = extract_page("""<html>
    <script type="application/ld+json">
      {"@type":"Organization","name":"Blue Peak",
       "contactPoint":{"@type":"ContactPoint","email":"mailto:sales@bluepeak.example"}}
    </script>
    <meta itemprop="email" content="office@bluepeak.example">
    <p>Reach us at marketing [at] bluepeak [dot] example.</p>
    <a href="mailto:front%40bluepeak.example?body=extras@bluepeak.example">Write</a>
    </html>""", HOST + "/contact")
    emails = {item["value"]: item["source"] for item in out["emails"]}
    assert emails == {
        "sales@bluepeak.example": "json-ld",
        "office@bluepeak.example": "microdata",
        "marketing@bluepeak.example": "published text",
        "front@bluepeak.example": "mailto",
    }
    assert clean_email("x@broken-.example") is None
    assert clean_email("x@broken..example") is None


def test_contact_search_follows_contact_trail_and_collects_source_pages():
    transport = FakeTransport({
        HOST + "/": html_response(b'<html><a href="/directory/">Directory</a><a href="/x01/">Contact us</a>'
                                  b'<a href="https://other.example/contact">Other</a></html>'),
        HOST + "/x01/": html_response(b'<html><a href="mailto:info@bluepeak.example">Email</a></html>'),
        HOST + "/directory/": html_response(b'<html><a href="/directory/staff/">Staff</a></html>'),
        HOST + "/directory/staff/": html_response(b'<html>info@bluepeak.example office@bluepeak.example '
                                                  b'noreply@bluepeak.example contact@other.example</html>'),
    })
    client = SafeClient(transport=transport, resolver=lambda *_: [PUBLIC_IP], per_host_delay=0)
    result = enrich("bluepeak.example", max_pages=6, client=client, contact_focus=True)
    assert transport.calls[2:5] == [HOST + "/x01/", HOST + "/directory/", HOST + "/directory/staff/"]
    assert not any("other.example" in url for url in transport.calls)
    first, *_ = result["emails"]
    assert first["value"] == "info@bluepeak.example"
    assert first["domain_match"] and first["kind"] == "role"
    assert first["verification"] == "not_checked"
    assert [s["page"] for s in first["sources"]] == [HOST + "/x01/", HOST + "/directory/staff/"]
    assert result["emails"][-1]["kind"] == "system"


def test_explicit_page_checked_without_user_query_or_forbidden_page():
    seed = HOST + "/people/alex"
    transport = FakeTransport({
        HOST + "/robots.txt": response(200, {}, b"User-agent: *\nDisallow: /private-team\n"),
        HOST + "/": html_response(b'<html><a href="/private-team">Staff</a></html>'),
        seed: html_response(b'<html>alex@bluepeak.example</html>'),
    })
    client = SafeClient(transport=transport, resolver=lambda *_: [PUBLIC_IP], per_host_delay=0)
    result = enrich(seed + "?tracking=remove#bio", client=client, max_pages=3, contact_focus=True)
    assert transport.calls[-1] == seed
    assert result["emails"][0]["value"] == "alex@bluepeak.example"
    assert not any("private-team" in url or "?" in url for url in transport.calls)


def test_deep_search_reads_index_and_reaches_more_than_six_relevant_pages():
    from pathlib import PurePosixPath

    paths = [f"/locations/branch-{i}" for i in range(12)]
    routes = {
        HOST + "/": html_response(b"<html><title>Blue Peak</title></html>"),
        HOST + "/robots.txt": response(200, {}, f"User-agent: *\nDisallow: /locations/branch-10\nSitemap: {HOST}/sitemap_index.xml".encode()),
        HOST + "/sitemap_index.xml": response(200, {}, (
            f"<sitemapindex><sitemap><loc>{HOST}/page-sitemap.xml</loc></sitemap>"
            "<sitemap><loc>https://other.example/sitemap.xml</loc></sitemap></sitemapindex>"
        ).encode()),
        HOST + "/page-sitemap.xml": response(200, {}, (
            "<urlset>" + "".join(f"<url><loc>{HOST}{path}</loc></url>" for path in paths)
            + "<url><loc>https://other.example/contact</loc></url></urlset>"
        ).encode()),
    }
    for path in paths:
        mailbox = PurePosixPath(path).name
        routes[HOST + path] = html_response(f"<html>{mailbox}@bluepeak.example</html>".encode())
    transport = FakeTransport(routes)
    client = SafeClient(transport=transport, resolver=lambda *_: [PUBLIC_IP], per_host_delay=0)
    result = enrich(HOST, max_pages=20, client=client, deep=True)
    assert result["crawl"] == {"mode": "deep", "page_limit": 20, "sitemaps_read": 2}
    assert len(result["emails"]) == 11
    assert not any("other.example" in u or "branch-10" in u for u in transport.calls)


def test_deep_search_does_not_follow_sitemap_redirect_outside_website():
    transport = FakeTransport({
        HOST + "/": html_response(b"<html><title>Blue Peak</title></html>"),
        HOST + "/sitemap.xml": response(302, {"Location": "https://other.example/map.xml"}),
    })
    client = SafeClient(transport=transport, resolver=lambda *_: [PUBLIC_IP], per_host_delay=0)
    result = enrich(HOST, max_pages=20, client=client, deep=True)
    assert result["crawl"]["sitemaps_read"] == 0
    assert not any("other.example" in u for u in transport.calls)

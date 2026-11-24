from domainkit.extract import extract_page


def names(page, key):
    return [item["value"] if isinstance(item, dict) and "value" in item else item for item in page[key]]


def test_jsonld_name_beats_title_and_open_graph():
    html = """
    <html><head>
      <title>NW | Home</title>
      <meta property="og:site_name" content="Northwind">
      <script type="application/ld+json">
        {"@type": "Organization", "name": "Northwind Labs"}
      </script>
    </head><body></body></html>
    """
    page = extract_page(html, "https://northwind.example/")
    assert page["company_name"]["value"] == "Northwind Labs"
    assert page["company_name"]["source"] == "json-ld"
    assert page["company_name"]["inferred"] is False
    assert page["company_name"]["confidence"] == "high"
    assert page["website_title"]["value"] == "NW | Home"
    assert page["website_title"]["inferred"] is False


def test_open_graph_name_is_inferred_when_jsonld_is_absent():
    html = """
    <html><head>
      <title>Northwind Labs | Home</title>
      <meta property="og:site_name" content="Northwind Labs">
    </head></html>
    """
    page = extract_page(html, "https://northwind.example/")
    assert page["company_name"] == {
        "value": "Northwind Labs",
        "source": "opengraph",
        "confidence": "medium",
        "inferred": True,
        "page": "https://northwind.example/",
    }


def test_title_name_is_a_low_confidence_inference():
    html = "<html><head><title>Acme Widgets | Official Site</title></head></html>"
    page = extract_page(html, "https://acme.example/")
    assert page["company_name"]["value"] == "Acme Widgets"
    assert page["company_name"]["source"] == "title"
    assert page["company_name"]["inferred"] is True
    assert page["company_name"]["confidence"] == "low"


def test_generic_title_does_not_become_a_company_name():
    page = extract_page("<html><head><title>Home</title></head></html>", "https://acme.example/")
    assert page["company_name"] is None
    assert page["website_title"]["value"] == "Home"


def test_missing_fields_stay_empty_and_description_is_not_copied_from_the_title():
    page = extract_page("<html><head><title>Acme Widgets</title></head><body></body></html>", "https://acme.example/")
    assert page["meta_description"] is None
    assert page["emails"] == []
    assert page["phones"] == []
    assert page["address"] is None
    assert all(value == [] for value in page["social_links"].values())


def test_emails_phones_and_placeholders():
    html = """
    <html><body>
      <a href="mailto:hello@northwind.example?subject=Hi">hello@northwind.example</a>
      <p>name@example.com logo@2x.png press@northwind.example</p>
      <a href="tel:+1-415-555-0142">Call</a>
      <p>(415) 555-0199</p>
    </body></html>
    """
    page = extract_page(html, "https://northwind.example/")
    emails = [item["value"] for item in page["emails"]]
    assert emails == ["hello@northwind.example", "press@northwind.example"]
    assert page["emails"][0]["source"] == "mailto"
    assert page["emails"][1]["source"] == "text"
    phones = [(item["value"], item["source"]) for item in page["phones"]]
    assert phones == [("+1-415-555-0142", "tel"), ("(415) 555-0199", "text")]


def test_share_links_are_ignored_and_profiles_are_kept():
    html = """
    <html><body>
      <a href="https://twitter.com/intent/tweet?text=Hi">Share</a>
      <a href="https://www.facebook.com/sharer/sharer.php?u=https://northwind.example">Share</a>
      <a href="https://www.linkedin.com/company/northwind-labs">LinkedIn</a>
      <a href="https://x.com/northwindlabs">X</a>
      <a href="https://github.com/northwind-labs">GitHub</a>
    </body></html>
    """
    page = extract_page(html, "https://northwind.example/")
    assert page["social_links"]["facebook"] == []
    assert page["social_links"]["linkedin"][0]["value"].endswith("/company/northwind-labs")
    assert page["social_links"]["twitter"][0]["value"] == "https://x.com/northwindlabs"
    assert page["social_links"]["github"][0]["source"] == "html-link"


def test_invalid_jsonld_is_skipped_without_dropping_the_page():
    html = """
    <html><head>
      <script type="application/ld+json">{not json</script>
      <title>Kept</title>
    </head></html>
    """
    page = extract_page(html, "https://northwind.example/")
    assert page["website_title"]["value"] == "Kept"
    assert page["json_ld"] == []
    assert "skipped invalid JSON-LD" in page["warnings"]


def test_microdata_organization():
    html = """
    <html><body>
      <div itemscope itemtype="https://schema.org/Organization">
        <span itemprop="name">Micro Org</span>
        <span itemprop="email">hello@micro.example</span>
        <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
          <span itemprop="streetAddress">1 Main</span>
          <span itemprop="addressLocality">Town</span>
        </div>
      </div>
    </body></html>
    """
    page = extract_page(html, "https://micro.example/")
    assert page["company_name"]["value"] == "Micro Org"
    assert page["company_name"]["source"] == "microdata"
    assert page["company_name"]["inferred"] is False
    assert page["emails"][0]["value"] == "hello@micro.example"
    assert page["address"]["value"]["street"] == "1 Main"
    assert page["address"]["value"]["locality"] == "Town"
    assert page["address"]["source"] == "microdata"


def test_forms_booking_chat_and_technology_signatures():
    html = """
    <html><head>
      <meta name="generator" content="WordPress 6.5.5">
      <script src="https://www.googletagmanager.com/gtag/js?id=G-TEST"></script>
      <script src="https://widget.intercom.io/widget/abc"></script>
    </head><body>
      <form action="/search" role="search"><input type="search" name="q"></form>
      <form action="/thanks" method="post">
        <input type="email" name="email">
        <textarea name="message"></textarea>
      </form>
      <a href="https://calendly.com/northwind-labs/intro">Book</a>
      <script src="https://embed.tawk.to/test/default"></script>
    </body></html>
    """
    page = extract_page(html, "https://northwind.example/contact", {"server": "cloudflare"})
    assert [form["action"] for form in page["contact_forms"]] == ["https://northwind.example/thanks"]
    assert page["contact_forms"][0]["method"] == "post"
    assert page["booking_links"][0]["provider"] == "Calendly"
    providers = [item["provider"] for item in page["live_chat"]]
    assert providers == ["Intercom", "Tawk"]
    tech = {item["name"]: item for item in page["technologies"]}
    assert "WordPress" in tech
    assert tech["WordPress"]["source"] == "html"
    assert "Google Analytics" in tech
    assert tech["Cloudflare"]["source"] == "header"

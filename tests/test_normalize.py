import pytest

from domainkit.errors import InvalidDomain
from domainkit.normalize import normalize_domain


def test_bare_domain_uses_https_apex():
    parsed = normalize_domain("example.com")
    assert parsed.domain == "example.com"
    assert parsed.host == "example.com"
    assert parsed.fetch_url == "https://example.com/"


def test_trailing_dot_and_case_are_normalized():
    parsed = normalize_domain("EXAMPLE.COM.")
    assert parsed.domain == "example.com"
    assert parsed.fetch_url == "https://example.com/"


def test_www_is_kept_for_the_request_and_removed_from_the_domain():
    parsed = normalize_domain("https://www.example.com/about?ref=1")
    assert parsed.domain == "example.com"
    assert parsed.host == "www.example.com"
    assert parsed.fetch_url == "https://www.example.com/"


def test_explicit_http_is_preserved():
    parsed = normalize_domain("http://www.example.com")
    assert parsed.fetch_url == "http://www.example.com/"
    assert parsed.domain == "example.com"


def test_non_default_port_is_preserved():
    parsed = normalize_domain("https://example.com:8443/a")
    assert parsed.fetch_url == "https://example.com:8443/"
    assert parsed.domain == "example.com"


def test_default_port_is_omitted():
    parsed = normalize_domain("https://example.com:443/a")
    assert parsed.fetch_url == "https://example.com/"


def test_www_is_not_stripped_from_a_two_label_name():
    parsed = normalize_domain("www.com")
    assert parsed.domain == "www.com"
    assert parsed.fetch_url == "https://www.com/"


def test_idna_host_is_punycode():
    parsed = normalize_domain("https://www.bücher.example/x")
    assert parsed.domain == "xn--bcher-kva.example"
    assert parsed.fetch_url == "https://www.xn--bcher-kva.example/"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a domain",
        "localhost",
        "example",
        "file://example.com",
        "http://",
        "http://127.0.0.1",
        "https://8.8.8.8/path",
        "https://user:pass@example.com",
        "example.com:99999",
        "http://example.com:0/",
    ],
)
def test_rejects_unusable_input(value):
    with pytest.raises(InvalidDomain):
        normalize_domain(value)

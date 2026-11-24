import socket

import pytest

from domainkit.errors import FetchError, UnsafeDestination
from domainkit.net import default_resolver, ip_block_reason, validate_destination

PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2001:4860:4860::8888"


@pytest.mark.parametrize(
    "ip",
    [
        "0.0.0.0",
        "10.1.2.3",
        "100.64.1.1",
        "127.0.0.1",
        "127.0.0.2",
        "169.254.169.254",
        "172.16.5.4",
        "192.0.0.9",
        "192.0.2.1",
        "192.168.1.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "fec0::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
    ],
)
def test_blocked_addresses(ip):
    assert ip_block_reason(ip)


@pytest.mark.parametrize("ip", [PUBLIC_V4, "1.1.1.1", "93.184.216.34", PUBLIC_V6])
def test_public_addresses_are_allowed(ip):
    assert ip_block_reason(ip) is None


def test_mixed_dns_answer_is_rejected_before_connect():
    def resolver(host, port):
        assert host == "mixed.example"
        return [PUBLIC_V4, "10.0.0.2"]

    with pytest.raises(UnsafeDestination, match="10.0.0.2"):
        validate_destination("https://mixed.example/path", resolver)


def test_every_public_address_is_returned_and_the_first_is_pinned():
    resolved = validate_destination(
        "https://example.com/a",
        lambda host, port: [PUBLIC_V4, PUBLIC_V6],
    )
    assert resolved.ips == (PUBLIC_V4, PUBLIC_V6)
    assert resolved.pinned_ip == PUBLIC_V4
    assert resolved.url == "https://example.com/a"
    assert resolved.origin == "https://example.com"


def test_ip_literal_does_not_use_dns():
    def resolver(host, port):
        raise AssertionError("DNS should not be consulted for an IP literal")

    with pytest.raises(UnsafeDestination, match="127.0.0.1"):
        validate_destination("http://127.0.0.1/admin", resolver)

    resolved = validate_destination("http://8.8.8.8/dns", resolver)
    assert resolved.pinned_ip == PUBLIC_V4
    assert resolved.url == "http://8.8.8.8/dns"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/",
        "http://0x7f.0.0.1/",
        "http://2130706433/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://printer.local/",
        "http://app.internal/",
        "http://localhost/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "http://example.com\\@127.0.0.1/",
        "http://127.0.0.1%2F@example.com/",
        "http://example.com%2eevil/",
        "https://example.com:0/",
    ],
)
def test_rejects_unsafe_urls_without_a_connection(url):
    def resolver(host, port):
        raise AssertionError(f"DNS should not run for {url}")

    with pytest.raises(UnsafeDestination):
        validate_destination(url, resolver)


def test_default_resolver_uses_every_address(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "example.com"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, port)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC_V6, port, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert default_resolver("example.com", 443) == [PUBLIC_V4, PUBLIC_V6]


def test_default_resolver_dns_failure(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError, match="DNS lookup failed"):
        default_resolver("missing.example", 443)

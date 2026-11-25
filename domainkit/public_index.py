"""Private source-backed seed index and optional Brave Search API fallback."""

from __future__ import annotations

import json
import os
import re
from html import unescape
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from domainkit.extract import _EMAIL_RE, clean_email

SEARCH_API = "https://api.search.brave.com/res/v1/web/search"


def _source_url(value) -> str | None:
    try:
        if not isinstance(value, str) or len(value) > 1500 or "\\" in value:
            return None
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username is not None or url.port not in {None, 443}:
            return None
        return value
    except ValueError:
        return None


def _observations(domain: str, entries, method: str) -> list[dict]:
    emails: dict[str, list[dict]] = {}
    for address, url in entries:
        email = clean_email(address)
        url = _source_url(url)
        if not email or not url or email.rsplit("@", 1)[1] != domain:
            continue
        refs = emails.setdefault(email, [])
        if len(refs) < 5 and not any(ref["page"] == url for ref in refs):
            refs.append({"page": url, "source": method})
    return [{"value": address, "source": method, "page": refs[0]["page"],
             "sources": refs, "inferred": False, "domain_match": True,
             "verification": "not_checked"} for address, refs in list(emails.items())[:20]]


def _stored(domain: str) -> list[dict]:
    try:
        values = json.loads(os.getenv("DOMAINKIT_PUBLIC_INDEX", "{}"))
        records = values.get(domain, []) if isinstance(values, dict) else []
        if not isinstance(records, list):
            return []
        return _observations(domain, ((v["email"], v["url"]) for v in records[:80]
                                      if isinstance(v, dict) and isinstance(v.get("email"), str)
                                      and isinstance(v.get("url"), str)), "saved public source")
    except (ValueError, TypeError):
        return []


def _search(domain: str, token: str) -> list[dict]:
    response = requests.get(SEARCH_API, headers={"X-Subscription-Token": token, "Accept": "application/json"},
                            params={"q": f'"{domain}" email contact', "count": 10, "extra_snippets": "true"},
                            timeout=(3, 6), allow_redirects=False)
    if response.status_code != 200 or len(response.content) > 2_000_000:
        return []
    web = response.json().get("web", {})
    entries = []
    for hit in (web.get("results", []) if isinstance(web, dict) else [])[:10]:
        if not isinstance(hit, dict):
            continue
        snippets = hit.get("extra_snippets", [])
        texts = [hit.get("description", "")] + (snippets[:5] if isinstance(snippets, list) else [])
        for source in texts:
            if not isinstance(source, str):
                continue
            plain = unescape(BeautifulSoup(source[:4096], "html.parser").get_text())
            entries.extend((match.group(), hit.get("url")) for match in _EMAIL_RE.finditer(plain))
    return _observations(domain, entries, "Brave Search excerpt")


def add_public_sources(result: dict) -> dict:
    """Present separately from the live results; never claim a failed crawl succeeded."""
    domain = result.get("domain")
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9.-]{3,253}", domain):
        return result
    if not result.get("error") and any(item.get("domain_match") for item in result.get("emails", [])):
        return result
    found = _stored(domain)
    token = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    status = "saved_sources" if found else "no_saved_sources"
    if token and not found:
        try:
            found = _search(domain, token)
            status = "search_results" if found else "no_matching_search_results"
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            status = "search_unavailable"
    result["public_index"] = {"status": status, "emails": found}
    return result

"""Fetch a homepage and a few same-site pages, then merge what was observed."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from domainkit.constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_PER_HOST_DELAY,
    DEFAULT_TIMEOUT,
    MAX_PAGES_LIMIT,
    SOCIAL_PLATFORMS,
    USER_AGENT,
)
from domainkit.errors import DomainKitError, FetchError, RobotsDenied, UnsafeDestination
from domainkit.extract import canonical_url, empty_social, extract_page, phone_key, prefer_field
from domainkit.fetch import FetchResult, SafeClient
from domainkit.normalize import normalize_domain

_CONTACT_RE = re.compile(r"/(?:contact|contact-us|contactus)(?:/|$)", re.IGNORECASE)
_ABOUT_RE = re.compile(r"/(?:about|about-us|aboutus|team|company)(?:/|$)", re.IGNORECASE)


def _site_key(host: str | None) -> str:
    if not host:
        return ""
    cleaned = host.lower().rstrip(".")
    if cleaned.startswith("www."):
        return cleaned[4:]
    return cleaned


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _empty(raw: str, domain: str | None) -> dict:
    return {
        "input": raw,
        "domain": domain,
        "final_url": None,
        "error": None,
        "company_name": None,
        "website_title": None,
        "meta_description": None,
        "emails": [],
        "phones": [],
        "social_links": empty_social(),
        "address": None,
        "open_graph": None,
        "twitter": None,
        "json_ld": [],
        "technologies": [],
        "contact_forms": [],
        "booking_links": [],
        "live_chat": [],
        "pages": [],
        "robots": None,
        "warnings": [],
    }


def error_result(raw: str, message: str, domain: str | None = None) -> dict:
    result = _empty(raw, domain)
    result["error"] = message
    return result


def looks_like_html(headers: dict[str, str], body: bytes) -> bool:
    media = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media in {"text/html", "application/xhtml+xml"}:
        return True
    if media and media not in {"text/plain", "application/octet-stream"}:
        return False
    snippet = body.lstrip()[:400].lower()
    return (
        snippet.startswith(b"<!doctype html")
        or snippet.startswith(b"<html")
        or b"<head" in snippet
        or b"<body" in snippet
    )


def discover_pages(links: list[str], final_url: str, limit: int) -> list[str]:
    """Pick a few obvious same-site pages. Linked pages win over guesses."""

    if limit <= 0:
        return []
    parts = urlsplit(final_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return []
    origin = f"{parts.scheme}://{parts.netloc}"
    home = final_url.rstrip("/") or final_url
    contact: list[str] = []
    about: list[str] = []
    for link in links:
        parsed = urlsplit(link)
        if parsed.scheme not in {"http", "https"}:
            continue
        if _site_key(parsed.hostname) != _site_key(parts.hostname):
            continue
        path = parsed.path or "/"
        if any(piece == ".." for piece in path.split("/")):
            continue
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if path in {"", "/"}:
            continue
        target = origin + path
        if target.rstrip("/") == home:
            continue
        if _CONTACT_RE.search(path) and target not in contact:
            contact.append(target)
        elif _ABOUT_RE.search(path) and target not in about:
            about.append(target)

    extras: list[str] = []

    def add(url: str) -> None:
        if url.rstrip("/") != home and url not in extras:
            extras.append(url)

    for url in contact:
        add(url)
    for url in about:
        add(url)
    if not contact:
        add(origin + "/contact")
        add(origin + "/contact-us")
    if not about:
        add(origin + "/about")
        add(origin + "/about-us")
    return extras[:limit]


_CONTACT_PATHS = (
    (88, re.compile(r"(?:^|[-/_])(?:contact(?:-?us)?|get-in-touch|reach-us)(?:$|[-/_.])", re.I)),
    (82, re.compile(r"(?:^|[-/_])(?:staff|team|leadership|management|our-people|directory|executives)(?:$|[-/_.])", re.I)),
    (74, re.compile(r"(?:^|[-/_])(?:locations?|offices?|branches|departments|support|sales|press|media)(?:$|[-/_.])", re.I)),
    (54, re.compile(r"(?:^|[-/_])(?:about(?:-?us)?|company|who-we-are)(?:$|[-/_.])", re.I)),
)
_ROLE_LOCAL = re.compile(r"^(?:info|hello|contact|office|sales|admin|support|service|help|press|media|marketing|careers|jobs|booking|reception|enquiries|inquiries|team|billing|accounts)(?:[.+_-]|$)")
_SYSTEM_LOCAL = re.compile(r"^(?:no[._-]?reply|do[._-]?not[._-]?reply|postmaster|webmaster|abuse|privacy|security)(?:[.+_-]|$)")


def _contact_target(raw: str, home: str) -> str | None:
    try:
        parts, root = urlsplit(raw), urlsplit(home)
        path = unquote(parts.path or "/")
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or _site_key(parts.hostname) != _site_key(root.hostname)
            or parts.username is not None
            or not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or any(ord(char) < 32 for char in path)
            or any(segment in {".", ".."} for segment in path.split("/"))
            or path.rstrip("/") == root.path.rstrip("/")
        ):
            return None
        return urlunsplit((root.scheme, root.netloc, parts.path, "", ""))
    except ValueError:
        return None


def _contact_targets(links: list[tuple[str, str]], home: str):
    for link, label in links:
        target = _contact_target(link, home)
        if target is None:
            continue
        path = unquote(urlsplit(target).path)
        label_key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        for weight, pattern in _CONTACT_PATHS:
            if pattern.search(path) or pattern.search(label_key):
                yield target, weight - min(path.strip("/").count("/"), 4)
                break


def _contact_emails(merged: list[dict], observations: list[dict], host: str) -> list[dict]:
    sources: dict[str, list[dict]] = {}
    for page in observations:
        for item in page.get("emails", []):
            seen = sources.setdefault(item["value"], [])
            if len(seen) < 6 and not any(source["page"] == item["page"] for source in seen):
                seen.append({"page": item["page"], "source": item["source"]})

    enriched = []
    for item in merged:
        local, _, domain = item["value"].partition("@")
        kind = "system" if _SYSTEM_LOCAL.search(local) else "role" if _ROLE_LOCAL.search(local) else "other"
        same = domain == host or domain.endswith("." + host)
        enriched.append({
            **item,
            "sources": sources[item["value"]],
            "kind": kind,
            "domain_match": same,
            "verification": "not_checked",
        })
    return sorted(enriched, key=lambda item: (item["kind"] == "system", not item["domain_match"],
                                              item["confidence"] != "high", -len(item["sources"])))


def _merge_maps(current: dict | None, new: dict | None) -> dict | None:
    if not new:
        return current
    if current is None:
        return dict(new)
    merged = dict(current)
    for key, value in new.items():
        merged.setdefault(key, value)
    return merged


def merge_observations(pages: list[dict]) -> dict:
    """Merge homepage-first observations. Equal confidence keeps the earlier page."""

    merged = {
        "company_name": None,
        "website_title": None,
        "meta_description": None,
        "emails": [],
        "phones": [],
        "social_links": empty_social(),
        "address": None,
        "open_graph": None,
        "twitter": None,
        "json_ld": [],
        "technologies": [],
        "contact_forms": [],
        "booking_links": [],
        "live_chat": [],
        "warnings": [],
    }
    seen_json: set[str] = set()
    seen_tech: set[str] = set()
    seen_forms: set[tuple] = set()
    seen_booking: set[tuple] = set()
    seen_chat: set[str] = set()

    for page in pages:
        merged["company_name"] = prefer_field(merged["company_name"], page.get("company_name"))
        merged["website_title"] = prefer_field(merged["website_title"], page.get("website_title"))
        merged["meta_description"] = prefer_field(merged["meta_description"], page.get("meta_description"))
        merged["address"] = prefer_field(merged["address"], page.get("address"))
        merged["open_graph"] = _merge_maps(merged["open_graph"], page.get("open_graph"))
        merged["twitter"] = _merge_maps(merged["twitter"], page.get("twitter"))
        for email in page.get("emails") or []:
            _upsert_obs(merged["emails"], email, lambda item: item["value"].lower())
        for phone in page.get("phones") or []:
            _upsert_obs(merged["phones"], phone, lambda item: phone_key(item["value"]))
        for platform in SOCIAL_PLATFORMS:
            for link in (page.get("social_links") or {}).get(platform) or []:
                _upsert_obs(
                    merged["social_links"][platform],
                    link,
                    lambda item: canonical_url(item["value"]),
                )
        for node in page.get("json_ld") or []:
            marker = json.dumps(node, sort_keys=True, default=str)
            if marker in seen_json or len(merged["json_ld"]) >= 20:
                continue
            seen_json.add(marker)
            merged["json_ld"].append(node)
        for tech in page.get("technologies") or []:
            name = tech.get("name")
            if not name or name in seen_tech:
                continue
            seen_tech.add(name)
            merged["technologies"].append(tech)
        for form in page.get("contact_forms") or []:
            marker = (form.get("page"), form.get("action"), form.get("method"))
            if marker in seen_forms:
                continue
            seen_forms.add(marker)
            merged["contact_forms"].append(form)
        for booking in page.get("booking_links") or []:
            marker = (booking.get("provider"), booking.get("url"))
            if marker in seen_booking:
                continue
            seen_booking.add(marker)
            merged["booking_links"].append(booking)
        for chat in page.get("live_chat") or []:
            provider = chat.get("provider")
            if not provider or provider in seen_chat:
                continue
            seen_chat.add(provider)
            merged["live_chat"].append(chat)
        merged["warnings"].extend(page.get("warnings") or [])
    merged["warnings"] = _dedupe(merged["warnings"])
    return merged


def _upsert_obs(items: list, obs: dict, key) -> None:
    marker = key(obs)
    for index, existing in enumerate(items):
        if key(existing) == marker:
            items[index] = prefer_field(existing, obs)
            return
    items.append(obs)


def _page_record(url: str, *, status: int | None, ok: bool, error: str | None) -> dict:
    return {"url": url, "status": status, "ok": ok, "error": error}


def _robots_view(client: SafeClient, url: str | None) -> dict | None:
    if not url:
        return None
    policy = client.robot_policy(url)
    if policy is None:
        return None
    return {
        "url": policy.url,
        "status": policy.status,
        "http_status": policy.http_status,
        "crawl_delay_seconds": policy.crawl_delay,
    }


def _absorb(result: dict, fetched: FetchResult) -> dict | None:
    if fetched.status != 200:
        result["pages"].append(
            _page_record(fetched.url, status=fetched.status, ok=False, error=f"HTTP {fetched.status}")
        )
        return None
    if not looks_like_html(fetched.headers, fetched.body):
        result["pages"].append(
            _page_record(fetched.url, status=fetched.status, ok=False, error="response is not HTML")
        )
        result["warnings"].append(f"{fetched.url} is not HTML")
        return None
    result["pages"].append(_page_record(fetched.url, status=fetched.status, ok=True, error=None))
    return extract_page(fetched.body, fetched.url, fetched.headers)


def enrich(
    domain: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    max_pages: int = DEFAULT_MAX_PAGES,
    per_host_delay: float = DEFAULT_PER_HOST_DELAY,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str | None = None,
    client: SafeClient | None = None,
    contact_focus: bool = False,
) -> dict:
    """Enrich one public company domain.

    Raises ``InvalidDomain`` for input that is not a domain, and
    ``UnsafeDestination`` when the domain or a homepage redirect points at a
    private, loopback, link-local, or otherwise reserved address. A failed
    fetch of a public host is returned in ``error`` instead of raised.
    Fields that were not published on the pages stay ``null``.
    """

    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES_LIMIT:
        raise DomainKitError(f"max_pages must be an integer from 1 to {MAX_PAGES_LIMIT}")

    normalized = normalize_domain(domain)
    if client is None:
        client = SafeClient(
            user_agent=user_agent or USER_AGENT,
            timeout=timeout,
            connect_timeout=connect_timeout,
            max_redirects=max_redirects,
            max_bytes=max_bytes,
            per_host_delay=per_host_delay,
        )
    warning_start = len(client.warnings)
    result = _empty(normalized.raw, normalized.domain)

    try:
        homepage = client.get(normalized.fetch_url)
    except RobotsDenied as exc:
        result["pages"].append(_page_record(exc.url, status=None, ok=False, error=str(exc)))
        result["warnings"].append(str(exc))
        result["robots"] = _robots_view(client, normalized.fetch_url)
        result["warnings"] = _dedupe(result["warnings"] + client.warnings[warning_start:])
        return result
    except UnsafeDestination:
        raise
    except FetchError as exc:
        result["error"] = str(exc)
        result["pages"].append(_page_record(normalized.fetch_url, status=None, ok=False, error=str(exc)))
        result["robots"] = _robots_view(client, normalized.fetch_url)
        result["warnings"] = _dedupe(result["warnings"] + client.warnings[warning_start:])
        return result

    result["final_url"] = homepage.url
    observations: list[dict] = []
    home_extract = _absorb(result, homepage)
    if home_extract is not None:
        observations.append(home_extract)
        home_host = _site_key(urlsplit(homepage.url).hostname)
        links = home_extract.get("links") or []
        candidates: dict[str, tuple[int, str]] = {}
        attempted = {homepage.url.rstrip("/")}
        if contact_focus:
            for candidate, weight in _contact_targets(home_extract.get("navigation_links", []), homepage.url):
                candidates[candidate.rstrip("/")] = (weight, candidate)
            raw = normalized.raw if "://" in normalized.raw else "https://" + normalized.raw
            seed = _contact_target(raw, homepage.url)
            if seed:
                candidates[seed.rstrip("/")] = (100, seed)
            home_parts = urlsplit(homepage.url)
            for fallback in ("/contact", "/contact-us", "/about", "/team", "/locations"):
                target = _contact_target(urlunsplit((home_parts.scheme, home_parts.netloc, fallback, "", "")), homepage.url)
                if target:
                    candidates.setdefault(target.rstrip("/"), (10, target))
        else:
            for extra in discover_pages(links, homepage.url, max_pages - 1):
                candidates[extra.rstrip("/")] = (0, extra)
        for _ in range(max_pages - 1):
            if not candidates:
                break
            key = max(candidates, key=lambda candidate: candidates[candidate][0])
            _, extra = candidates.pop(key)
            attempted.add(key)
            try:
                fetched = client.get(extra)
            except RobotsDenied as exc:
                result["pages"].append(_page_record(exc.url, status=None, ok=False, error=str(exc)))
                result["warnings"].append(str(exc))
                continue
            except UnsafeDestination as exc:
                result["pages"].append(_page_record(extra, status=None, ok=False, error=str(exc)))
                result["warnings"].append(f"blocked {extra}: {exc}")
                continue
            except FetchError as exc:
                result["pages"].append(_page_record(extra, status=None, ok=False, error=str(exc)))
                result["warnings"].append(str(exc))
                continue
            if _site_key(urlsplit(fetched.url).hostname) != home_host:
                result["pages"].append(
                    _page_record(
                        fetched.url,
                        status=fetched.status,
                        ok=False,
                        error="redirected off the company site",
                    )
                )
                result["warnings"].append(f"{extra} redirected off the company site")
                continue
            extracted = _absorb(result, fetched)
            if extracted is not None:
                observations.append(extracted)
                if contact_focus:
                    fetched_key = fetched.url.rstrip("/")
                    attempted.add(fetched_key)
                    candidates.pop(fetched_key, None)
                    for candidate, weight in _contact_targets(extracted.get("navigation_links", []), homepage.url):
                        candidate_key = candidate.rstrip("/")
                        previous = candidates.get(candidate_key)
                        if candidate_key not in attempted and (previous is None or weight > previous[0]):
                            candidates[candidate_key] = (weight, candidate)

    if observations:
        merged = merge_observations(observations)
        if contact_focus:
            merged["emails"] = _contact_emails(merged["emails"], observations, _site_key(urlsplit(homepage.url).hostname))
        for key, value in merged.items():
            if key == "warnings":
                result["warnings"].extend(value)
            else:
                result[key] = value
    result["robots"] = _robots_view(client, result["final_url"] or normalized.fetch_url)
    result["warnings"] = _dedupe(result["warnings"] + client.warnings[warning_start:])
    return result

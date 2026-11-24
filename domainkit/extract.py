"""Turn one HTML page into observations. Nothing here is invented.

Missing values stay absent so the merger can leave them null. Inferred is
reserved for a company name taken from a proxy such as og:site_name or a
title, rather than from structured organization data.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from domainkit.constants import SOCIAL_PLATFORMS

_CONFIDENCE = {"high": 3, "medium": 2, "low": 1}
_MAX_EMAILS = 50
_MAX_PHONES = 50
_MAX_SOCIAL = 20
_MAX_JSONLD = 20
_MAX_FORMS = 20
_MAX_TECH = 40
_JSONLD_CHARS = 500_000

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?![\w.])"
)
_ASSET_LABELS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "webp",
    "css",
    "js",
    "ico",
    "bmp",
    "tif",
    "tiff",
    "woff",
    "woff2",
    "map",
}
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "example.edu")
_PLACEHOLDER_LOCALS = {"example", "yourname", "youremail", "your-email"}
_GENERIC_TITLE = {
    "home",
    "homepage",
    "welcome",
    "official site",
    "official website",
    "website",
    "site",
    "index",
}

_SOCIAL_HOSTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("linkedin", ("linkedin.com",)),
    ("twitter", ("twitter.com", "x.com", "mobile.twitter.com")),
    ("facebook", ("facebook.com", "fb.com", "m.facebook.com")),
    ("instagram", ("instagram.com",)),
    ("youtube", ("youtube.com", "youtu.be", "m.youtube.com")),
    ("tiktok", ("tiktok.com",)),
    ("github", ("github.com",)),
    ("discord", ("discord.gg", "discord.com")),
    ("pinterest", ("pinterest.com",)),
    ("crunchbase", ("crunchbase.com",)),
)
_SKIP_SOCIAL_PREFIXES = {
    "twitter.com": ("/intent", "/share", "/home"),
    "x.com": ("/intent", "/share", "/home"),
    "mobile.twitter.com": ("/intent", "/share"),
    "facebook.com": ("/sharer", "/share", "/plugins", "/dialog"),
    "m.facebook.com": ("/sharer", "/share", "/plugins", "/dialog"),
    "fb.com": ("/sharer", "/share"),
    "linkedin.com": ("/share", "/sharing", "/sharearticle"),
    "youtube.com": ("/embed", "/redirect"),
    "m.youtube.com": ("/embed", "/redirect"),
}

_BOOKING_HOSTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Calendly", ("calendly.com",)),
    ("Cal.com", ("cal.com",)),
    ("HubSpot Meetings", ("meetings.hubspot.com",)),
    ("Acuity", ("acuityscheduling.com",)),
    ("SavvyCal", ("savvycal.com",)),
    ("Chili Piper", ("chilipiper.com",)),
    ("YouCanBook.me", ("youcanbook.me",)),
    ("TidyCal", ("tidycal.com",)),
    ("Zcal", ("zcal.co",)),
    ("Koalendar", ("koalendar.com",)),
)

_CHAT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Intercom", re.compile(r"widget\.intercom\.io|intercomSettings|Intercom\(", re.I)),
    ("Drift", re.compile(r"js\.driftt\.com|drift\.load", re.I)),
    ("Crisp", re.compile(r"client\.crisp\.chat", re.I)),
    ("Tawk", re.compile(r"embed\.tawk\.to", re.I)),
    ("Zendesk", re.compile(r"static\.zdassets\.com|zopim\.com|zendesk\.com/embeddable", re.I)),
    ("HubSpot Chat", re.compile(r"js\.usemessages\.com", re.I)),
    ("LiveChat", re.compile(r"cdn\.livechatinc\.com|livechatinc\.com/tracking\.js", re.I)),
    ("Olark", re.compile(r"static\.olark\.com", re.I)),
    ("Tidio", re.compile(r"code\.tidio\.co", re.I)),
    ("Freshchat", re.compile(r"wchat\.freshchat\.com|freshchat\.com/js", re.I)),
    ("Help Scout", re.compile(r"beacon-v2\.helpscout\.net", re.I)),
    ("Gorgias", re.compile(r"gorgias\.chat", re.I)),
    ("Chaport", re.compile(r"app\.chaport\.com", re.I)),
)

_TECH_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("WordPress", re.compile(r"wordpress[^<\"']{0,40}|/wp-content/|/wp-includes/", re.I)),
    ("Shopify", re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I)),
    ("Webflow", re.compile(r"data-wf-site|assets\.website-files\.com", re.I)),
    ("Squarespace", re.compile(r"squarespace\.com|static1\.squarespace", re.I)),
    ("Wix", re.compile(r"wixstatic\.com|static\.parastorage\.com", re.I)),
    ("Next.js", re.compile(r"/_next/static/|__NEXT_DATA__", re.I)),
    ("Nuxt", re.compile(r"/_nuxt/|__NUXT__", re.I)),
    ("Gatsby", re.compile(r"___gatsby", re.I)),
    ("Google Analytics", re.compile(r"google-analytics\.com|googletagmanager\.com/gtag|\bgtag\(", re.I)),
    ("Google Tag Manager", re.compile(r"googletagmanager\.com/gtm\.js", re.I)),
    ("HubSpot", re.compile(r"js\.hs-scripts\.com|js\.hs-analytics\.net|hs-script-loader", re.I)),
    ("Stripe", re.compile(r"js\.stripe\.com", re.I)),
    ("Bootstrap", re.compile(r"bootstrap(?:\.min)?\.(?:css|js)", re.I)),
    ("jQuery", re.compile(r"jquery(?:-\d|\.min|\.js)", re.I)),
    ("React", re.compile(r"data-reactroot|react-dom(?:\.production)?(?:\.min)?\.js", re.I)),
    ("Vue", re.compile(r"\bvue(?:\.min)?\.js|__vue__", re.I)),
)

_HEADER_TECH = (
    ("cloudflare", "Cloudflare"),
    ("nginx", "nginx"),
    ("apache", "Apache"),
    ("litespeed", "LiteSpeed"),
    ("openresty", "OpenResty"),
    ("caddy", "Caddy"),
    ("microsoft-iis", "Microsoft IIS"),
    ("gunicorn", "Gunicorn"),
)


def clean_ws(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def observed(value, source: str, confidence: str, inferred: bool, page: str) -> dict:
    return {
        "value": value,
        "source": source,
        "confidence": confidence,
        "inferred": inferred,
        "page": page,
    }


def prefer_field(current: dict | None, new: dict | None) -> dict | None:
    """Keep the more direct observation. Ties stay with the earlier page."""

    if current is None:
        return new
    if new is None:
        return current
    if _CONFIDENCE[new["confidence"]] > _CONFIDENCE[current["confidence"]]:
        return new
    if _CONFIDENCE[new["confidence"]] < _CONFIDENCE[current["confidence"]]:
        return current
    if current["inferred"] and not new["inferred"]:
        return new
    return current


def empty_social() -> dict[str, list]:
    return {name: [] for name in SOCIAL_PLATFORMS}


def _host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        return host[4:]
    return host


def _same_host(host: str, needle: str) -> bool:
    return host == needle or host.endswith("." + needle)


def social_platform(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    bare = host[4:] if host.startswith("www.") else host
    path = parts.path or "/"
    lower_path = path.lower()
    for platform, hosts in _SOCIAL_HOSTS:
        if not any(_same_host(bare, item) or _same_host(host, item) for item in hosts):
            continue
        prefixes = _SKIP_SOCIAL_PREFIXES.get(bare) or _SKIP_SOCIAL_PREFIXES.get(host) or ()
        if any(lower_path.startswith(prefix) for prefix in prefixes):
            return None
        if path.strip("/") == "":
            return None
        return platform
    return None


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
        ]
    )
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, query, ""))


def _placeholder_email(email: str) -> bool:
    local, _, domain = email.partition("@")
    if not local or not domain:
        return True
    labels = domain.split(".")
    if not labels or labels[-1] in _ASSET_LABELS:
        return True
    if domain in _PLACEHOLDER_DOMAINS or any(domain.endswith("." + item) for item in _PLACEHOLDER_DOMAINS):
        return True
    if local.lower() in _PLACEHOLDER_LOCALS:
        return True
    return False


def clean_email(value: str) -> str | None:
    email = value.strip().strip(".").lower()
    if not _EMAIL_RE.fullmatch(email):
        return None
    if _placeholder_email(email):
        return None
    return email


def phone_key(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def clean_phone(value: str) -> str | None:
    raw = clean_ws(value)
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") or raw.startswith("00"):
        if not 8 <= len(digits) <= 15:
            return None
    elif not 10 <= len(digits) <= 15:
        return None
    if len(set(digits)) == 1:
        return None
    return raw


def company_from_title(title: str) -> str | None:
    parts = [part.strip() for part in re.split(r"\s+[|–—]\s+|\s+-\s+", title) if part.strip()]
    if len(parts) >= 2 and parts[-1].lower() in _GENERIC_TITLE:
        candidate = parts[0]
    elif len(parts) == 2 and parts[0].lower() in _GENERIC_TITLE:
        candidate = parts[1]
    else:
        candidate = title.strip()
    if not candidate or candidate.lower() in _GENERIC_TITLE:
        return None
    return candidate


def _meta(soup: BeautifulSoup, name: str | None = None, prop: str | None = None) -> str | None:
    wanted = (name or prop or "").lower()
    attr = "name" if name else "property"
    for tag in soup.find_all("meta"):
        if (tag.get(attr) or "").lower() != wanted:
            continue
        content = clean_ws(tag.get("content"))
        if content:
            return content
    return None


def _type_names(node: dict) -> list[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def _is_org(types: list[str]) -> bool:
    for name in types:
        if name in {"Organization", "Corporation", "LocalBusiness", "OnlineBusiness"}:
            return True
        if name.endswith("Organization") or name.endswith("Business"):
            return True
    return False


def _flatten_jsonld(data):
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
        return
    if not isinstance(data, dict):
        return
    graph = data.get("@graph")
    if isinstance(graph, (list, dict)):
        yield from _flatten_jsonld(graph)
        if "@type" in data:
            yield {key: value for key, value in data.items() if key != "@graph"}
        return
    yield data


def _as_text(value) -> str | None:
    if isinstance(value, str):
        return clean_ws(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "text", "@value"):
            if isinstance(value.get(key), str):
                return clean_ws(value[key])
    return None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _address_value(address) -> dict | None:
    if isinstance(address, list):
        for item in address:
            parsed = _address_value(item)
            if parsed:
                return parsed
        return None
    if isinstance(address, str):
        text = clean_ws(address)
        if not text:
            return None
        return {
            "street": None,
            "locality": None,
            "region": None,
            "postal_code": None,
            "country": None,
            "raw": text,
        }
    if not isinstance(address, dict):
        return None
    value = {
        "street": _as_text(address.get("streetAddress")),
        "locality": _as_text(address.get("addressLocality")),
        "region": _as_text(address.get("addressRegion")),
        "postal_code": _as_text(address.get("postalCode")),
        "country": _as_text(address.get("addressCountry")),
        "raw": None,
    }
    if not any(value.values()):
        return None
    return value


def _upsert(items: list, obs: dict, key, limit: int, warnings: list[str], label: str) -> None:
    marker = key(obs["value"])
    for index, existing in enumerate(items):
        if key(existing["value"]) == marker:
            items[index] = prefer_field(existing, obs)
            return
    if len(items) >= limit:
        message = f"stopped after {limit} {label}"
        if message not in warnings:
            warnings.append(message)
        return
    items.append(obs)


def _add_email(page: dict, email: str | None, source: str, confidence: str, page_url: str) -> None:
    if not email:
        return
    _upsert(
        page["emails"],
        observed(email, source, confidence, False, page_url),
        lambda value: value.lower(),
        _MAX_EMAILS,
        page["warnings"],
        "emails",
    )


def _add_phone(page: dict, phone: str | None, source: str, confidence: str, page_url: str) -> None:
    if not phone:
        return
    _upsert(
        page["phones"],
        observed(phone, source, confidence, False, page_url),
        phone_key,
        _MAX_PHONES,
        page["warnings"],
        "phones",
    )


def _add_social(page: dict, url: str, source: str, page_url: str) -> None:
    absolute = urljoin(page_url, url)
    platform = social_platform(absolute)
    if platform is None:
        return
    confidence = "high" if source == "json-ld" else "medium"
    _upsert(
        page["social_links"][platform],
        observed(absolute, source, confidence, False, page_url),
        canonical_url,
        _MAX_SOCIAL,
        page["warnings"],
        f"{platform} links",
    )


def _consider_name(page: dict, value: str | None, source: str, confidence: str, inferred: bool, page_url: str) -> None:
    text = clean_ws(value)
    if not text:
        return
    page["company_name"] = prefer_field(
        page["company_name"],
        observed(text, source, confidence, inferred, page_url),
    )


def _jsonld_nodes(soup: BeautifulSoup, page: dict) -> list[dict]:
    nodes: list[dict] = []
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = script.string or script.get_text() or ""
        if len(raw) > _JSONLD_CHARS:
            page["warnings"].append("skipped an oversized JSON-LD block")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            page["warnings"].append("skipped invalid JSON-LD")
            continue
        for node in _flatten_jsonld(data):
            if isinstance(node, dict) and ("@type" in node or "name" in node):
                nodes.append(node)
                if len(nodes) >= _MAX_JSONLD:
                    page["warnings"].append(f"stopped after {_MAX_JSONLD} JSON-LD nodes")
                    return nodes
    return nodes


def _apply_jsonld(page: dict, nodes: list[dict], page_url: str) -> None:
    page["json_ld"] = nodes
    for node in nodes:
        types = _type_names(node)
        org = _is_org(types)
        website = "WebSite" in types
        if org:
            _consider_name(page, _as_text(node.get("name") or node.get("legalName")), "json-ld", "high", False, page_url)
        elif website:
            _consider_name(page, _as_text(node.get("name")), "json-ld", "medium", True, page_url)
        if org or website:
            description = _as_text(node.get("description"))
            if description:
                page["meta_description"] = prefer_field(
                    page["meta_description"],
                    observed(description, "json-ld", "medium", False, page_url),
                )
        if org:
            for email in _as_list(node.get("email")):
                _add_email(page, clean_email(_as_text(email) or ""), "json-ld", "high", page_url)
            for phone in _as_list(node.get("telephone")):
                _add_phone(page, clean_phone(_as_text(phone) or ""), "json-ld", "high", page_url)
            address = _address_value(node.get("address"))
            if address:
                page["address"] = prefer_field(
                    page["address"],
                    observed(address, "json-ld", "high", False, page_url),
                )
            for link in _as_list(node.get("sameAs")):
                if isinstance(link, str):
                    _add_social(page, link, "json-ld", page_url)


def _itemprop_values(element) -> set[str]:
    raw = element.get("itemprop") or ""
    return {part.strip().lower() for part in raw.split() if part.strip()}


def _direct_prop(node, prop: str):
    wanted = prop.lower()
    for element in node.find_all(attrs={"itemprop": True}):
        if wanted not in _itemprop_values(element):
            continue
        parent = element.parent
        nested = False
        while parent is not None and parent is not node:
            if parent.has_attr("itemscope"):
                nested = True
                break
            parent = parent.parent
        if not nested:
            return element
    return None


def _apply_microdata(page: dict, soup: BeautifulSoup, page_url: str) -> None:
    for node in soup.find_all(attrs={"itemtype": True}):
        itemtype = " ".join(node.get("itemtype") if isinstance(node.get("itemtype"), list) else [node.get("itemtype") or ""])
        if "Organization" not in itemtype and "LocalBusiness" not in itemtype and not itemtype.endswith("Business"):
            if "schema.org/Organization" not in itemtype and "schema.org/" not in itemtype:
                continue
        if not any(token in itemtype for token in ("Organization", "LocalBusiness", "Business")):
            continue
        name_el = _direct_prop(node, "name")
        if name_el is not None:
            _consider_name(page, name_el.get_text(" ", strip=True), "microdata", "high", False, page_url)
        email_el = _direct_prop(node, "email")
        if email_el is not None:
            _add_email(page, clean_email(email_el.get_text(" ", strip=True) or email_el.get("href", "").removeprefix("mailto:")), "microdata", "high", page_url)
        phone_el = _direct_prop(node, "telephone")
        if phone_el is not None:
            _add_phone(page, clean_phone(phone_el.get_text(" ", strip=True) or phone_el.get("href", "").removeprefix("tel:")), "microdata", "high", page_url)
        address_el = _direct_prop(node, "address")
        if address_el is not None:
            address = {
                "street": _prop_text(address_el, "streetAddress"),
                "locality": _prop_text(address_el, "addressLocality"),
                "region": _prop_text(address_el, "addressRegion"),
                "postal_code": _prop_text(address_el, "postalCode"),
                "country": _prop_text(address_el, "addressCountry"),
                "raw": None,
            }
            if not any(address.values()):
                raw = clean_ws(address_el.get_text(" ", strip=True))
                if raw:
                    address["raw"] = raw
            if any(address.values()):
                page["address"] = prefer_field(
                    page["address"],
                    observed(address, "microdata", "high", False, page_url),
                )


def _prop_text(node, prop: str) -> str | None:
    element = node.find(attrs={"itemprop": lambda value: value and prop.lower() in value.lower().split()})
    if element is None:
        return None
    return clean_ws(element.get_text(" ", strip=True))


def _visible_soup(soup: BeautifulSoup) -> BeautifulSoup:
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    return clone


def _collect_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    links: list[str] = []
    for tag, attr in (
        ("a", "href"),
        ("area", "href"),
        ("iframe", "src"),
        ("script", "src"),
        ("link", "href"),
        ("form", "action"),
    ):
        for element in soup.find_all(tag):
            raw = element.get(attr)
            if not raw or not isinstance(raw, str):
                continue
            absolute = urljoin(page_url, raw.strip())
            if urlsplit(absolute).scheme in {"http", "https"}:
                links.append(absolute)
    return links


def _emails_from_mailto(soup: BeautifulSoup, page: dict, page_url: str) -> None:
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href.lower().startswith("mailto:"):
            continue
        payload = unquote(href[7:].split("?", 1)[0])
        for piece in payload.split(","):
            _add_email(page, clean_email(piece), "mailto", "high", page_url)


def _phones_from_tel(soup: BeautifulSoup, page: dict, page_url: str) -> None:
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href.lower().startswith("tel:"):
            continue
        _add_phone(page, clean_phone(unquote(href[4:].split("?", 1)[0])), "tel", "high", page_url)


def _contact_forms(soup: BeautifulSoup, page: dict, page_url: str) -> None:
    for form in soup.find_all("form"):
        if (form.get("role") or "").lower() == "search":
            continue
        interesting = False
        search_only = True
        controls = form.find_all(["input", "textarea", "select"])
        if not controls:
            continue
        for control in controls:
            control_type = (control.get("type") or "").lower()
            name = (control.get("name") or "").lower()
            placeholder = (control.get("placeholder") or "").lower()
            if control_type in {"hidden", "submit", "button", "reset", "image"}:
                continue
            search_only = False
            blob = " ".join((control_type, name, placeholder, control.name or ""))
            if control_type in {"email", "tel"} or control.name == "textarea" or any(
                token in blob for token in ("email", "phone", "message", "contact")
            ):
                interesting = True
            if control_type == "search" or name in {"q", "s", "query", "search"}:
                continue
        if search_only or not interesting:
            continue
        action = form.get("action") or ""
        if action.lower().startswith(("javascript:", "data:")):
            continue
        absolute = urljoin(page_url, action) if action else page_url
        method = (form.get("method") or "get").lower()
        record = {"action": absolute, "method": method, "page": page_url, "source": "html-form"}
        marker = (record["page"], record["action"], record["method"])
        if any((item["page"], item["action"], item["method"]) == marker for item in page["contact_forms"]):
            continue
        if len(page["contact_forms"]) >= _MAX_FORMS:
            break
        page["contact_forms"].append(record)


def _booking_links(links: list[str], page: dict, page_url: str) -> None:
    for link in links:
        host = _host_of(link)
        if not host:
            continue
        provider = None
        for name, hosts in _BOOKING_HOSTS:
            if any(_same_host(host, item) for item in hosts):
                provider = name
                break
        if provider is None:
            continue
        record = {"provider": provider, "url": link, "source": "html-link", "page": page_url}
        marker = (provider, canonical_url(link))
        if any((item["provider"], canonical_url(item["url"])) == marker for item in page["booking_links"]):
            continue
        page["booking_links"].append(record)


def _evidence(match: re.Match[str]) -> str:
    text = " ".join(match.group(0).split())
    return text[:120]


def _technologies(html: str, headers: dict[str, str], page: dict, page_url: str) -> None:
    found: set[str] = set()

    def add(name: str, source: str, evidence: str) -> None:
        if name in found or len(page["technologies"]) >= _MAX_TECH:
            return
        found.add(name)
        page["technologies"].append(
            {"name": name, "source": source, "evidence": evidence[:120], "page": page_url}
        )

    for name, pattern in _TECH_RULES:
        match = pattern.search(html)
        if match:
            add(name, "html", _evidence(match))

    server = (headers.get("server") or "").lower()
    for needle, name in _HEADER_TECH:
        if needle in server:
            add(name, "header", headers.get("server") or needle)
    if "cf-ray" in headers:
        add("Cloudflare", "header", "cf-ray")
    powered = headers.get("x-powered-by") or ""
    powered_lower = powered.lower()
    for needle, name in (
        ("php", "PHP"),
        ("express", "Express"),
        ("asp.net", "ASP.NET"),
        ("next.js", "Next.js"),
    ):
        if needle in powered_lower:
            add(name, "header", powered)


def _live_chat(html: str, page: dict, page_url: str) -> None:
    found: set[str] = set()
    for provider, pattern in _CHAT_RULES:
        match = pattern.search(html)
        if not match or provider in found:
            continue
        found.add(provider)
        page["live_chat"].append(
            {"provider": provider, "source": "html", "evidence": _evidence(match), "page": page_url}
        )


def _open_graph(soup: BeautifulSoup) -> dict | None:
    found = {}
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or "").lower()
        if prop.startswith("og:"):
            content = clean_ws(tag.get("content"))
            if content and prop not in found:
                found[prop] = content
    return found or None


def _twitter(soup: BeautifulSoup) -> dict | None:
    found = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").lower()
        if name.startswith("twitter:"):
            content = clean_ws(tag.get("content"))
            if content and name not in found:
                found[name] = content
    return found or None


def extract_page(body: bytes | str, page_url: str, headers: dict[str, str] | None = None) -> dict:
    """Extract observations from a single fetched page."""

    header_map = {key.lower(): value for key, value in (headers or {}).items()}
    if isinstance(body, bytes):
        html = body.decode("utf-8", errors="replace")
    else:
        html = body
    soup = BeautifulSoup(html, "html.parser")
    page = {
        "company_name": None,
        "website_title": None,
        "meta_description": None,
        "emails": [],
        "phones": [],
        "social_links": empty_social(),
        "address": None,
        "open_graph": _open_graph(soup),
        "twitter": _twitter(soup),
        "json_ld": [],
        "technologies": [],
        "contact_forms": [],
        "booking_links": [],
        "live_chat": [],
        "warnings": [],
        "links": _collect_links(soup, page_url),
    }

    title = None
    if soup.title is not None:
        title = clean_ws(soup.title.get_text(" ", strip=True))
    if title:
        page["website_title"] = observed(title, "title", "high", False, page_url)
        _consider_name(page, company_from_title(title), "title", "low", True, page_url)
    og_title = (page["open_graph"] or {}).get("og:title")
    if og_title:
        page["website_title"] = prefer_field(
            page["website_title"],
            observed(og_title, "opengraph", "medium", False, page_url),
        )

    description = _meta(soup, name="description")
    if description:
        page["meta_description"] = prefer_field(
            page["meta_description"],
            observed(description, "meta", "high", False, page_url),
        )
    og_description = (page["open_graph"] or {}).get("og:description")
    if og_description:
        page["meta_description"] = prefer_field(
            page["meta_description"],
            observed(og_description, "opengraph", "medium", False, page_url),
        )
    site_name = (page["open_graph"] or {}).get("og:site_name")
    _consider_name(page, site_name, "opengraph", "medium", True, page_url)
    _consider_name(page, _meta(soup, name="application-name"), "meta", "medium", True, page_url)

    nodes = _jsonld_nodes(soup, page)
    _apply_jsonld(page, nodes, page_url)
    _apply_microdata(page, soup, page_url)
    _emails_from_mailto(soup, page, page_url)
    _phones_from_tel(soup, page, page_url)

    visible = _visible_soup(soup).get_text(" ", strip=True)
    for match in _EMAIL_RE.finditer(visible):
        _add_email(page, clean_email(match.group(0)), "text", "medium", page_url)
    for match in _PHONE_RE.finditer(visible):
        _add_phone(page, clean_phone(match.group(0)), "text", "medium", page_url)
    for link in page["links"]:
        _add_social(page, link, "html-link", page_url)

    _contact_forms(soup, page, page_url)
    _booking_links(page["links"], page, page_url)
    _technologies(html, header_map, page, page_url)
    _live_chat(html, page, page_url)
    return page

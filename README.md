# DomainKit

Open-source company intelligence for AI agents. One domain in. Structured business data out.

DomainKit takes a public company domain and returns JSON: the name, title, description, emails, phone numbers, social profiles, address, and the booking links, contact forms, chat widgets, and website technologies that are visible in the HTML. It is a small library, a command-line tool, a local HTTP API, and a single web page. There is no database, no account, and no paid API.

## Install

Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For tests:

```bash
pip install -e ".[dev]"
pytest
```

`requirements.txt` lists the runtime packages. `requirements-dev.txt` adds pytest and httpx.

## CLI

```bash
domainkit enrich example.com
domainkit enrich https://www.example.com/ -o example.json
domainkit batch domains.csv -o enriched.csv
domainkit batch domains.csv -o enriched.jsonl
domainkit serve --host 127.0.0.1 --port 8747
```

`enrich` accepts `example.com`, `https://example.com/`, `www.example.com`, and an explicit `http://` URL. A leading `www` is removed from the reported domain and kept on the first request. Paths and queries are ignored; enrichment starts at the site root.

`batch` reads a CSV. If the header has a `domain`, `url`, `website`, or `host` column, that column is used. Otherwise every row's first cell is a domain, including the first row. `.csv` and `.jsonl` outputs are selected from the file extension. Cells that start with `=`, `+`, `-`, or `@` are prefixed so a spreadsheet does not treat them as formulas.

## Python

```python
from domainkit import enrich

record = enrich("example.com")
print(record["company_name"])
```

`enrich` raises `domainkit.errors.InvalidDomain` for input that is not a public domain, and `domainkit.errors.UnsafeDestination` when the host or a homepage redirect resolves to a private, loopback, link-local, or other reserved address. A timeout or a DNS failure on a public host is returned in the `error` field.

## Website

```bash
domainkit serve
```

Then open http://127.0.0.1:8747.

Paste a company URL or domain. The page calls `GET /enrich` and shows what came back: company name (with an inferred badge when the name was derived), title, description, emails, phones, social links, address, structured metadata, technologies, contact forms, booking links, chat widgets, and pages fetched. Empty fields say "Not found". Use Copy JSON for the response body, or open Raw JSON. The page is one HTML file with plain CSS and JavaScript. There is no build step.

### Run the full crawler online without Vercel

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/yipyipperton/domainkit)

The Render Blueprint uses a free Python web service with the same hosted crawler as the Vercel version, with no preset domain list. During setup pick a new preview password of at least 16 ASCII characters. After deploying, open your service URL and sign in as `domainkit` with that password. The access gate covers the website and lookup API. The private preview checks up to six HTML pages, prioritizing contact, team, directory, and location links, including related pages linked from those pages. A pasted path is prioritized after the homepage when it stays on the same website; queries and fragments are ignored. Emails include their source pages, inbox category where recognizable, and whether the address uses the website domain. The free instance can take a little time to wake after inactivity. Sites that block automated access, require login, or expose their content only through JavaScript may still be unavailable or incomplete.

### Deploy the public demo on Vercel

1. In [Vercel's project import](https://vercel.com/new), choose this GitHub repository.
2. Select **FastAPI** if it is not auto-detected, keep the root directory at the repository root, and deploy. No API keys or build output directory are needed. Vercel finds the hosted app in `app.py`.
3. Open the deployment URL and paste a public company domain. Check that `/health` returns `{"status":"ok"}`.

The hosted app reuses the localhost UI and API, but visits at most three pages and caps upstream requests, redirects, response size, lookups per minute, and concurrent lookups. It restricts all outbound hops to the default HTTP and HTTPS ports and retains DNS/IP checks, connection pinning and robots.txt handling. In-app limits are per running instance, not deployment-wide. Before broadly promoting the demo, configure a per-client rate limit on `/enrich` at the edge if available for your plan. No user-submitted URLs are stored by the app.

## HTTP

`domainkit serve` binds to `127.0.0.1:8747` by default. Leave it there. The process has no authentication and will fetch any public domain you ask for, using this machine's IP address.

```bash
curl "http://127.0.0.1:8747/health"
curl "http://127.0.0.1:8747/enrich?domain=example.com"
```

`GET /` is the website. `GET /enrich` returns the same JSON as `enrich()`. Invalid domains and blocked destinations are HTTP 400. A missing `domain` query is HTTP 422. FastAPI also publishes `/docs`.

## What comes back

Scalar business fields are either `null` or an object:

```json
{"value": "...", "source": "json-ld", "confidence": "high", "inferred": false, "page": "https://example.com/"}
```

`source` is where the value was read (`json-ld`, `microdata`, `opengraph`, `meta`, `title`, `mailto`, `tel`, `text`, `html-link`). `confidence` is `high`, `medium`, or `low`. `inferred` is `true` when a company name was derived from a proxy such as `og:site_name` or the page title. A JSON-LD or microdata organization name is not inferred. Empty lists mean that kind of value was looked for and not found. DomainKit does not fill gaps with guesses.

Company name preference, highest first: organization JSON-LD or microdata, then `og:site_name` or `application-name` (inferred), then a title with a generic suffix such as "Home" removed (inferred). Description preference: meta description, then Open Graph, then JSON-LD. The title is never copied into the description.

The tool fetches the homepage and up to three more same-host pages. Links whose paths look like contact, about, or team are preferred. If those links are missing it tries `/contact`, `/contact-us`, `/about`, and `/about-us` until the page cap is reached. `www` and the apex are treated as the same site. Other subdomains are not followed.

## Sample output

The JSON below is the exact result of `enrich("https://www.Northwind.example/about")` against the local HTML fixtures in `tests/fixtures/` (`homepage.html`, `contact.html`, `about.html`, `robots.txt`). The www host redirects to the apex inside the test double. It is not a live crawl. The same document is checked in at `tests/fixtures/sample_output.json`, and `tests/test_sample.py` fails if a fresh run drifts from it.

Addresses, phone numbers, and mailboxes in the fixture are fictional. `name@example.com` is present in the homepage text and is dropped because `example.com` is a reserved placeholder domain.

```json
{
  "input": "https://www.Northwind.example/about",
  "domain": "northwind.example",
  "final_url": "https://northwind.example/",
  "error": null,
  "company_name": {
    "value": "Northwind Labs",
    "source": "json-ld",
    "confidence": "high",
    "inferred": false,
    "page": "https://northwind.example/"
  },
  "website_title": {
    "value": "Northwind Labs | Home",
    "source": "title",
    "confidence": "high",
    "inferred": false,
    "page": "https://northwind.example/"
  },
  "meta_description": {
    "value": "Northwind Labs builds product analytics for B2B teams.",
    "source": "meta",
    "confidence": "high",
    "inferred": false,
    "page": "https://northwind.example/"
  },
  "emails": [
    {
      "value": "hello@northwind.example",
      "source": "json-ld",
      "confidence": "high",
      "inferred": false,
      "page": "https://northwind.example/"
    },
    {
      "value": "press@northwind.example",
      "source": "mailto",
      "confidence": "high",
      "inferred": false,
      "page": "https://northwind.example/"
    },
    {
      "value": "sales@northwind.example",
      "source": "mailto",
      "confidence": "high",
      "inferred": false,
      "page": "https://northwind.example/contact"
    },
    {
      "value": "careers@northwind.example",
      "source": "text",
      "confidence": "medium",
      "inferred": false,
      "page": "https://northwind.example/about"
    }
  ],
  "phones": [
    {
      "value": "+1-415-555-0148",
      "source": "json-ld",
      "confidence": "high",
      "inferred": false,
      "page": "https://northwind.example/"
    },
    {
      "value": "+1-415-555-0142",
      "source": "tel",
      "confidence": "high",
      "inferred": false,
      "page": "https://northwind.example/"
    },
    {
      "value": "(415) 555-0199",
      "source": "text",
      "confidence": "medium",
      "inferred": false,
      "page": "https://northwind.example/contact"
    }
  ],
  "social_links": {
    "linkedin": [
      {
        "value": "https://www.linkedin.com/company/northwind-labs",
        "source": "json-ld",
        "confidence": "high",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "twitter": [
      {
        "value": "https://twitter.com/northwindlabs",
        "source": "json-ld",
        "confidence": "high",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "facebook": [
      {
        "value": "https://www.facebook.com/northwindlabs",
        "source": "html-link",
        "confidence": "medium",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "instagram": [
      {
        "value": "https://www.instagram.com/northwindlabs/",
        "source": "html-link",
        "confidence": "medium",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "youtube": [
      {
        "value": "https://www.youtube.com/northwindlabs",
        "source": "html-link",
        "confidence": "medium",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "tiktok": [
      {
        "value": "https://www.tiktok.com/@northwindlabs",
        "source": "html-link",
        "confidence": "medium",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "github": [
      {
        "value": "https://github.com/northwind-labs",
        "source": "json-ld",
        "confidence": "high",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "discord": [
      {
        "value": "https://discord.gg/northwind",
        "source": "html-link",
        "confidence": "medium",
        "inferred": false,
        "page": "https://northwind.example/"
      }
    ],
    "pinterest": [],
    "crunchbase": []
  },
  "address": {
    "value": {
      "street": "100 Harbor Way",
      "locality": "Sample City",
      "region": "CA",
      "postal_code": "94016",
      "country": "US",
      "raw": null
    },
    "source": "json-ld",
    "confidence": "high",
    "inferred": false,
    "page": "https://northwind.example/"
  },
  "open_graph": {
    "og:site_name": "Northwind Labs",
    "og:title": "Northwind Labs",
    "og:description": "Product analytics for B2B teams.",
    "og:url": "https://northwind.example/",
    "og:type": "website"
  },
  "twitter": {
    "twitter:card": "summary",
    "twitter:site": "@northwindlabs"
  },
  "json_ld": [
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "Northwind Labs",
      "url": "https://northwind.example/",
      "email": "hello@northwind.example",
      "telephone": "+1-415-555-0148",
      "description": "Structured description that should not replace the meta description.",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "100 Harbor Way",
        "addressLocality": "Sample City",
        "addressRegion": "CA",
        "postalCode": "94016",
        "addressCountry": "US"
      },
      "sameAs": [
        "https://www.linkedin.com/company/northwind-labs",
        "https://twitter.com/northwindlabs",
        "https://github.com/northwind-labs"
      ]
    },
    {
      "@context": "https://schema.org",
      "@type": "WebSite",
      "name": "NW",
      "url": "https://northwind.example/"
    }
  ],
  "technologies": [
    {
      "name": "WordPress",
      "source": "html",
      "evidence": "WordPress 6.5.5",
      "page": "https://northwind.example/"
    },
    {
      "name": "Google Analytics",
      "source": "html",
      "evidence": "googletagmanager.com/gtag",
      "page": "https://northwind.example/"
    },
    {
      "name": "HubSpot",
      "source": "html",
      "evidence": "js.hs-scripts.com",
      "page": "https://northwind.example/"
    },
    {
      "name": "jQuery",
      "source": "html",
      "evidence": "jquery-3",
      "page": "https://northwind.example/"
    },
    {
      "name": "Cloudflare",
      "source": "header",
      "evidence": "cloudflare",
      "page": "https://northwind.example/"
    }
  ],
  "contact_forms": [
    {
      "action": "https://northwind.example/thanks",
      "method": "post",
      "page": "https://northwind.example/contact",
      "source": "html-form"
    }
  ],
  "booking_links": [
    {
      "provider": "Calendly",
      "url": "https://calendly.com/northwind-labs/intro",
      "source": "html-link",
      "page": "https://northwind.example/"
    },
    {
      "provider": "HubSpot Meetings",
      "url": "https://meetings.hubspot.com/northwind/demo",
      "source": "html-link",
      "page": "https://northwind.example/contact"
    }
  ],
  "live_chat": [
    {
      "provider": "Intercom",
      "source": "html",
      "evidence": "widget.intercom.io",
      "page": "https://northwind.example/"
    },
    {
      "provider": "Tawk",
      "source": "html",
      "evidence": "embed.tawk.to",
      "page": "https://northwind.example/contact"
    }
  ],
  "pages": [
    {
      "url": "https://northwind.example/",
      "status": 200,
      "ok": true,
      "error": null
    },
    {
      "url": "https://northwind.example/contact",
      "status": 200,
      "ok": true,
      "error": null
    },
    {
      "url": "https://northwind.example/about",
      "status": 200,
      "ok": true,
      "error": null
    }
  ],
  "robots": {
    "url": "https://northwind.example/robots.txt",
    "status": "ok",
    "http_status": 200,
    "crawl_delay_seconds": null
  },
  "warnings": []
}
```

When the page has no organization record, the company name is marked inferred. This is the real result of enriching a one-page local response whose only name signals were `og:site_name` and `<title>Northwind Labs | Home</title>`:

```json
{
  "company_name": {
    "value": "Northwind Labs",
    "source": "opengraph",
    "confidence": "medium",
    "inferred": true,
    "page": "https://inferred.example/"
  },
  "website_title": {
    "value": "Northwind Labs | Home",
    "source": "title",
    "confidence": "high",
    "inferred": false,
    "page": "https://inferred.example/"
  },
  "meta_description": {
    "value": "Product analytics for B2B teams.",
    "source": "meta",
    "confidence": "high",
    "inferred": false,
    "page": "https://inferred.example/"
  },
  "emails": [],
  "address": null
}
```

## Safety

Every request, including robots.txt and every redirect hop, is checked before a connection is opened.

- The User-Agent is `DomainKit/0.1 (website enrichment; respects robots.txt)`.
- Only `http` and `https`. Credentials, backslashes, control characters, and percent-encoded hostnames are rejected.
- DNS is resolved up front. If any address is private, loopback, link-local, shared (CGNAT), multicast, reserved, documentation, benchmarking, or otherwise not globally routable, the host is rejected. IPv4-mapped IPv6, NAT64, 6to4, and obfuscated forms such as `127.1` or a decimal IP are rejected too.
- The TCP connection is pinned to an address that passed that check. `HTTP_PROXY` and `HTTPS_PROXY` are ignored, so a proxy cannot move the connection.
- Redirects are followed by DomainKit, at most 5, and each hop is validated again.
- Response bodies are capped at 1.5 MB. Connect timeout is 5 seconds and read timeout is 10 seconds by default.
- Requests to one host are spaced by 0.75 seconds by default. A whole-number `Crawl-delay` raises that wait, and is capped at 5 seconds.
- `robots.txt` is fetched first. A normal ruleset is obeyed for this User-Agent. 404 or another ordinary 4xx means the site has no rules. 401, 403, a 5xx, or a failed fetch is treated as disallow for that host.

## Limitations

- HTML is not executed. Copy and widgets that appear only after JavaScript runs are missed.
- Technology, chat, and booking detection look for known strings in the HTML and a few response headers. They miss new vendors and can false-positive on a coincidental string.
- Emails come from `mailto:`, JSON-LD, microdata, and visible text, including clearly written `name [at] domain [dot] com` addresses. Placeholder domains such as `example.com` are dropped. DomainKit does not generate addresses or verify mailbox deliverability.
- Phone numbers come from `tel:`, JSON-LD, microdata, and a conservative text pattern. Many international formats are missed, and a similar digit pattern can still match.
- Addresses come only from JSON-LD or microdata postal addresses, not from free text.
- The crawl is at most 6 pages (4 by default) on the final host. There is no public-suffix list and no subdomain crawl.
- Python's robots parser only honors a whole-number crawl-delay.
- Forms are not submitted. The tool does not log in, solve challenges, or use a browser.
- This is not a person-search product. It reads public company pages and returns what those pages contain.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests use local fixtures and fakes. They do not contact the network. GitHub Actions runs the same suite on Python 3.10, 3.11, and 3.12.

## License

MIT. Copyright DomainKit contributors. See `LICENSE`.

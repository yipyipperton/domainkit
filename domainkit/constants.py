"""Shared defaults. Kept small so the tool stays predictable on a laptop."""

from __future__ import annotations

USER_AGENT = "DomainKit/0.1 (website enrichment; respects robots.txt)"

DEFAULT_TIMEOUT = 10.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_BYTES = 1_500_000
DEFAULT_PER_HOST_DELAY = 0.75
DEFAULT_MAX_CRAWL_DELAY = 5.0
DEFAULT_MAX_PAGES = 4
MAX_PAGES_LIMIT = 6

SOCIAL_PLATFORMS = (
    "linkedin",
    "twitter",
    "facebook",
    "instagram",
    "youtube",
    "tiktok",
    "github",
    "discord",
    "pinterest",
    "crunchbase",
)

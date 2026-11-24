"""Errors raised by DomainKit.

Invalid input and blocked destinations propagate to the caller. Operational
fetch problems are usually reported on the result object instead.
"""

from __future__ import annotations


class DomainKitError(Exception):
    """Base class for errors callers can handle."""


class InvalidDomain(DomainKitError, ValueError):
    """The input is not a usable public http(s) company domain."""


class UnsafeDestination(DomainKitError):
    """A URL was rejected because it is not a public http(s) destination."""


class FetchError(DomainKitError):
    """A page could not be fetched (timeout, DNS, size, redirects)."""


class ResponseTooLarge(FetchError):
    """The response exceeded the configured byte cap."""


class RobotsDenied(DomainKitError):
    """robots.txt does not allow this URL for our User-Agent."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(reason)

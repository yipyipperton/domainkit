"""Password-protected, non-Vercel deployment of the full DomainKit crawler."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

from fastapi import Request
from fastapi.responses import PlainTextResponse

from domainkit.api import create_app


def create_preview(password: str):
    if len(password) < 16 or not password.isascii():
        raise RuntimeError("Set DOMAINKIT_PREVIEW_PASSWORD to at least 16 ASCII characters.")

    expected = hashlib.sha256(("domainkit:" + password).encode()).digest()
    preview = create_app(hosted=True, queue_preview=True)

    @preview.middleware("http")
    async def require_preview_password(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if request.url.path == "/enrich" and request.headers.get("sec-fetch-site") == "cross-site":
            return PlainTextResponse("Open the preview directly to run a lookup.", status_code=403)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        supplied = b""
        if scheme.lower() == "basic" and len(value) <= 4096:
            try:
                supplied = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                pass
        if not hmac.compare_digest(hashlib.sha256(supplied).digest(), expected):
            return PlainTextResponse(
                "DomainKit private preview. Sign in to continue.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="DomainKit preview"', "Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return preview


def start():
    return create_preview(os.environ.get("DOMAINKIT_PREVIEW_PASSWORD", ""))

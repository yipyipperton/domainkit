"""FastAPI and the single-page website, with tighter limits for hosted demos."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from domainkit import __version__
from domainkit.constants import USER_AGENT
from domainkit.enricher import enrich
from domainkit.errors import DomainKitError
from domainkit.fetch import SafeClient

_PAGE_PATH = Path(__file__).with_name("page.html")


def _hosted_enrich(domain: str, *, contact_focus: bool = False) -> dict:
    return enrich(
        domain,
        max_pages=6 if contact_focus else 3,
        contact_focus=contact_focus,
        client=SafeClient(
            standard_ports_only=True,
            timeout=4,
            connect_timeout=3,
            max_redirects=4,
            max_requests=14 if contact_focus else 10,
            max_bytes=1_000_000,
        ),
    )


def create_app(*, hosted: bool = False, queue_preview: bool = False) -> FastAPI:
    page_html = _PAGE_PATH.read_text(encoding="utf-8")
    app = FastAPI(
        title="DomainKit",
        version=__version__,
        description=(
            "Open-source company intelligence for AI agents. "
            "One domain in. Structured business data out."
        ),
    )
    app.state.enrich = (lambda domain: _hosted_enrich(domain, contact_focus=queue_preview)) if hosted else enrich
    slots = BoundedSemaphore(3)
    preview_slots = asyncio.Semaphore(3)
    preview_pending = 0
    admitted: deque[float] = deque()
    admissions_lock = Lock()

    def admit() -> bool:
        with admissions_lock:
            now = monotonic()
            while admitted and now - admitted[0] >= 60:
                admitted.popleft()
            if len(admitted) >= 30 or not slots.acquire(blocking=False):
                return False
            admitted.append(now)
            return True

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> HTMLResponse:
        return HTMLResponse(page_html)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    def do_lookup(domain: str) -> dict:
        try:
            return app.state.enrich(domain)
        except DomainKitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/enrich")
    async def enrich_domain(
        request: Request,
        domain: str = Query(..., min_length=1, max_length=2048, description="Public company domain"),
    ) -> dict:
        nonlocal preview_pending
        if not hosted:
            return await run_in_threadpool(do_lookup, domain)
        if request.headers.get("user-agent", "").startswith(USER_AGENT.split("/")[0] + "/"):
            raise HTTPException(status_code=403, detail="Recursive DomainKit lookups are blocked.")

        if queue_preview:
            # This mode is used behind the Render preview's password gate.
            # Let a small burst wait, but keep crawler concurrency bounded.
            def queue_full() -> HTTPException:
                return HTTPException(
                    status_code=503,
                    detail="Other lookups are still running. Please retry in a few seconds.",
                    headers={"Retry-After": "5"},
                )

            if preview_pending >= 9:
                raise queue_full()
            preview_pending += 1
            try:
                try:
                    await asyncio.wait_for(preview_slots.acquire(), timeout=20)
                except asyncio.TimeoutError as exc:
                    raise queue_full() from exc
                try:
                    return await run_in_threadpool(do_lookup, domain)
                finally:
                    preview_slots.release()
            finally:
                preview_pending -= 1

        if not admit():
            raise HTTPException(
                status_code=429,
                detail="Too many lookup requests. Please retry in a minute.",
                headers={"Retry-After": "60"},
            )
        try:
            return await run_in_threadpool(do_lookup, domain)
        finally:
            slots.release()

    if hosted:
        @app.middleware("http")
        async def hosted_headers(request: Request, call_next):
            response = await call_next(request)
            if request.url.path == "/enrich":
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
            elif request.url.path == "/":
                response.headers["Referrer-Policy"] = "no-referrer"
            return response

    return app


app = create_app()

"""FastAPI and the single-page website, with tighter limits for hosted demos."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from domainkit import __version__
from domainkit.constants import USER_AGENT
from domainkit.enricher import enrich
from domainkit.errors import DomainKitError
from domainkit.fetch import SafeClient

_PAGE_PATH = Path(__file__).with_name("page.html")


def _hosted_enrich(domain: str) -> dict:
    return enrich(
        domain,
        max_pages=3,
        client=SafeClient(
            standard_ports_only=True,
            timeout=4,
            connect_timeout=3,
            max_redirects=4,
            max_requests=10,
            max_bytes=1_000_000,
        ),
    )


def create_app(*, hosted: bool = False) -> FastAPI:
    page_html = _PAGE_PATH.read_text(encoding="utf-8")
    app = FastAPI(
        title="DomainKit",
        version=__version__,
        description=(
            "Open-source company intelligence for AI agents. "
            "One domain in. Structured business data out."
        ),
    )
    app.state.enrich = _hosted_enrich if hosted else enrich
    slots = BoundedSemaphore(3)
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

    @app.get("/enrich")
    def enrich_domain(
        request: Request,
        domain: str = Query(..., min_length=1, max_length=2048, description="Public company domain"),
    ) -> dict:
        if hosted:
            if request.headers.get("user-agent", "").startswith(USER_AGENT.split("/")[0] + "/"):
                raise HTTPException(status_code=403, detail="Recursive DomainKit lookups are blocked.")
            if not admit():
                raise HTTPException(
                    status_code=429,
                    detail="The demo is busy. Please retry in a minute.",
                    headers={"Retry-After": "60"},
                )
        try:
            return app.state.enrich(domain)
        except DomainKitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if hosted:
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

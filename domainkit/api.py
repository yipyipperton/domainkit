"""Local HTTP API and the single-page website. No authentication. Bind it to localhost."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from domainkit import __version__
from domainkit.enricher import enrich
from domainkit.errors import DomainKitError, InvalidDomain, UnsafeDestination

_PAGE_PATH = Path(__file__).with_name("page.html")


def create_app() -> FastAPI:
    page_html = _PAGE_PATH.read_text(encoding="utf-8")
    app = FastAPI(
        title="DomainKit",
        version=__version__,
        description=(
            "Open-source company intelligence for AI agents. "
            "One domain in. Structured business data out."
        ),
    )
    app.state.enrich = enrich

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> HTMLResponse:
        return HTMLResponse(page_html)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/enrich")
    def enrich_domain(
        domain: str = Query(..., min_length=1, max_length=2048, description="Public company domain"),
    ) -> dict:
        try:
            return app.state.enrich(domain)
        except (InvalidDomain, UnsafeDestination) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DomainKitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()

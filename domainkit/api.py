"""Local HTTP API. No authentication. Bind it to localhost."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from domainkit import __version__
from domainkit.enricher import enrich
from domainkit.errors import DomainKitError, InvalidDomain, UnsafeDestination


def create_app() -> FastAPI:
    app = FastAPI(
        title="DomainKit",
        version=__version__,
        description=(
            "Open-source company intelligence for AI agents. "
            "One domain in. Structured business data out."
        ),
    )
    app.state.enrich = enrich

    @app.get("/")
    def root() -> dict:
        return {
            "name": "DomainKit",
            "description": (
                "Open-source company intelligence for AI agents. "
                "One domain in. Structured business data out."
            ),
            "enrich": "/enrich?domain=example.com",
            "health": "/health",
        }

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

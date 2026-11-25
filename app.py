"""Vercel entry point. The CLI continues to use its localhost app."""

from domainkit.api import create_app

app = create_app(hosted=True)

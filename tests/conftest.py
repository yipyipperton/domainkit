"""Block real network access for every test."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

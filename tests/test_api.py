"""Tests du client API Immich (session HTTP factice)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.immich_guest_slideshow.api import (
    ImmichApiClient,
    ImmichAuthError,
)


class FakeResponse:
    """Réponse aiohttp minimale."""

    def __init__(self, status: int = 200, json_data: Any = None, body: bytes = b"") -> None:
        self.status = status
        self._json = json_data
        self._body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def json(self) -> Any:
        return self._json

    async def read(self) -> bytes:
        return self._body

    async def text(self) -> str:
        return ""


class FakeSession:
    """Session aiohttp factice rejouant une file de réponses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)


def _page(ids: list[str], next_page: int | None) -> FakeResponse:
    return FakeResponse(
        json_data={
            "assets": {
                "items": [{"id": i} for i in ids],
                "nextPage": next_page,
            }
        }
    )


def test_search_assets_follows_pagination() -> None:
    """La recherche suit nextPage jusqu'à la dernière page."""
    session = FakeSession([_page(["a", "b"], 2), _page(["c"], None)])
    client = ImmichApiClient("http://immich", "key", session)  # type: ignore[arg-type]

    items = asyncio.run(client.async_search_assets(["p1"], limit=10, page_size=2))

    assert [i["id"] for i in items] == ["a", "b", "c"]
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["personIds"] == ["p1"]


def test_search_assets_respects_limit() -> None:
    """La pagination s'arrête dès que la limite est atteinte."""
    session = FakeSession([_page(["a", "b"], 2), _page(["c", "d"], 3)])
    client = ImmichApiClient("http://immich", "key", session)  # type: ignore[arg-type]

    items = asyncio.run(client.async_search_assets(["p1"], limit=3, page_size=2))

    assert len(items) == 3


def test_auth_error_raises() -> None:
    """Un statut 401 lève ImmichAuthError."""
    session = FakeSession([FakeResponse(status=401)])
    client = ImmichApiClient("http://immich", "bad", session)  # type: ignore[arg-type]

    with pytest.raises(ImmichAuthError):
        asyncio.run(client.async_validate_connection())

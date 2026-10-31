"""Tests de la désignation d'une personne par son ID Immich (UUID)."""
from __future__ import annotations

import asyncio
from typing import Any

from custom_components.immich_guest_slideshow.api import (
    ImmichApiClient,
    ImmichApiError,
    extract_person_uuid,
)
from custom_components.immich_guest_slideshow.slideshow import SlideshowEngine

ALEXIA = "1238c4da-3b98-41b9-b795-81d51e95a6d5"
OWNER_ID = "aaaaaaaa-0000-4000-8000-000000000001"
URL = f"http://192.168.1.90:2283/people/{ALEXIA}?previousRoute=%2Fphotos"


def test_extract_uuid_from_bare_id_and_url() -> None:
    assert extract_person_uuid(ALEXIA) == ALEXIA
    assert extract_person_uuid(URL) == ALEXIA
    assert extract_person_uuid(ALEXIA.upper()) == ALEXIA


def test_extract_uuid_returns_none_for_a_name() -> None:
    assert extract_person_uuid("Alexia") is None
    assert extract_person_uuid("") is None


class FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class FakeHass:
    def __init__(self, values: dict[str, str]) -> None:
        self.states = self
        self._values = values

    def get(self, entity_id: str) -> FakeState | None:
        value = self._values.get(entity_id)
        return FakeState(value) if value is not None else None


class FakeApi:
    """Immich factice : connaît des personnes par id, et par nom."""

    def __init__(self) -> None:
        self.people = {
            ALEXIA: {"id": ALEXIA, "name": "Alexia"},
            OWNER_ID: {"id": OWNER_ID, "name": "Propriétaire 1"},
        }
        self.get_calls: list[str] = []
        self.find_calls: list[str] = []
        self.search_calls: list[list[str]] = []

    async def async_get_person(self, person_id: str) -> dict[str, Any] | None:
        self.get_calls.append(person_id)
        return self.people.get(person_id)

    async def async_find_person(self, name: str) -> dict[str, Any] | None:
        self.find_calls.append(name)
        return next((p for p in self.people.values() if p["name"] == name), None)

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.search_calls.append(sorted(person_ids))
        return [{"id": "-".join(sorted(person_ids)) + "#0"}]


def _engine(api: FakeApi, guest: str, permanents: list[str]) -> SlideshowEngine:
    return SlideshowEngine(
        FakeHass({"input_text.h1": guest}),  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_test",
        room_name="Chambre test",
        helpers=["input_text.h1"],
        permanents=permanents,
        # Le faux Immich renvoie 1 photo par recherche : avec une cible de
        # 1 photo, la 1re recherche (invité + propriétaire) suffit et les
        # niveaux suivants ne sont pas interrogés (couverts par test_tiers.py).
        cache_size=1,
        min_photos=1,
    )


def test_guest_and_owner_by_uuid_skip_name_search() -> None:
    api = FakeApi()
    engine = _engine(api, ALEXIA, [OWNER_ID])

    asyncio.run(engine.async_rebuild())

    assert api.find_calls == []
    assert set(api.get_calls) == {ALEXIA, OWNER_ID}
    assert [ALEXIA, OWNER_ID] in [sorted(c) for c in api.search_calls] or sorted(
        [ALEXIA, OWNER_ID]
    ) in api.search_calls
    assert engine.photo_count == 1


def test_labels_show_immich_names_not_uuids() -> None:
    api = FakeApi()
    engine = _engine(api, ALEXIA, [OWNER_ID])

    asyncio.run(engine.async_rebuild())

    assert [c.label for c in engine.combos] == ["Alexia + Propriétaire 1"]
    photo = engine.next_photo()
    assert photo is not None and photo.search_label == "Alexia + Propriétaire 1"


def test_guest_url_pasted_in_helper_is_accepted() -> None:
    api = FakeApi()
    engine = _engine(api, URL, [OWNER_ID])

    asyncio.run(engine.async_rebuild())

    assert ALEXIA in api.get_calls
    assert engine.photo_count == 1


def test_names_still_work() -> None:
    api = FakeApi()
    engine = _engine(api, "Alexia", ["Propriétaire 1"])

    asyncio.run(engine.async_rebuild())

    assert api.get_calls == []
    assert engine.photo_count == 1
    assert [c.label for c in engine.combos] == ["Alexia + Propriétaire 1"]


def test_unknown_uuid_yields_no_photo() -> None:
    api = FakeApi()
    engine = _engine(api, "bbbbbbbb-0000-4000-8000-000000000002", [OWNER_ID])

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 0


class _Session:
    """Session aiohttp factice renvoyant une seule réponse."""

    def __init__(self, status: int, payload: Any) -> None:
        self._status = status
        self._payload = payload
        self.urls: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> "_Response":
        self.urls.append(url)
        return _Response(self._status, self._payload)


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return "not found"


def test_api_get_person_ok_and_404() -> None:
    ok = ImmichApiClient("http://x", "k", _Session(200, {"id": ALEXIA, "name": "Alexia"}))  # type: ignore[arg-type]
    person = asyncio.run(ok.async_get_person(ALEXIA))
    assert person == {"id": ALEXIA, "name": "Alexia"}

    missing_session = _Session(404, None)
    missing = ImmichApiClient("http://x", "k", missing_session)  # type: ignore[arg-type]
    assert asyncio.run(missing.async_get_person(ALEXIA)) is None
    assert missing_session.urls == [f"http://x/api/people/{ALEXIA}"]
    assert ImmichApiError  # import utilisé : 404 est converti en None

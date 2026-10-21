"""Outils communs aux tests d'intégration : faux client Immich, entrée v2."""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_PERMANENT_PERSONS,
    CONF_URL,
    DOMAIN,
    ROOM_CALENDAR,
    ROOM_DOOR_FRAME,
    ROOM_FRAMES,
    ROOM_HELPERS,
    ROOM_ID,
    ROOM_NAME,
    SUBENTRY_ROOM,
)

HELPER_1 = "input_text.guest_chambre_test"
HELPER_2 = "input_text.guest_chambre_test2"
OWNERS = ["Propriétaire 1", "Propriétaire 2"]
PEOPLE = {
    "Propriétaire 1": "aaaaaaaa-0000-4000-8000-000000000001",
    "Propriétaire 2": "aaaaaaaa-0000-4000-8000-000000000002",
    "Alexia": "1238c4da-3b98-41b9-b795-81d51e95a6d5",
    "Bruno": "bbbbbbbb-0000-4000-8000-000000000003",
    "Carla Dupont": "cccccccc-0000-4000-8000-000000000004",
}


def jpeg(color: tuple[int, int, int], size: tuple[int, int] = (640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


class FakeImmich:
    """Client Immich factice : une photo unie par asset."""

    photos: dict[frozenset[str], int] = {}
    fail: set[str] = set()
    person_thumbs: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @classmethod
    def reset(cls, photos: dict[frozenset[str], int] | None = None) -> None:
        cls.photos = dict(photos or {})
        cls.fail = set()
        cls.person_thumbs = []

    async def async_validate_connection(self) -> dict[str, Any]:
        return {"version": "3.0.1"}

    async def async_get_person(self, person_id: str) -> dict[str, Any] | None:
        for name, pid in PEOPLE.items():
            if pid == person_id:
                return {"id": pid, "name": name}
        return None

    async def async_find_person(self, name: str) -> dict[str, Any] | None:
        pid = PEOPLE.get(name)
        return {"id": pid, "name": name} if pid else None

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        count = self.photos.get(frozenset(person_ids), 0)
        key = "-".join(sorted(person_ids))
        return [{"id": f"{key}#{i}"} for i in range(count)]

    async def async_get_faces(self, asset_id: str) -> list[dict[str, Any]]:
        return []

    async def async_get_thumbnail(self, asset_id: str, **kwargs: Any) -> bytes:
        from custom_components.immich_guest_slideshow.api import ImmichApiError

        if asset_id in self.fail:
            raise ImmichApiError("boom")
        n = int(asset_id.rsplit("#", 1)[1])
        return jpeg(((n * 40) % 256, (n * 90) % 256, (n * 15) % 256))

    async def async_get_person_thumbnail(self, person_id: str) -> bytes:
        self.person_thumbs.append(person_id)
        return jpeg((200, 150, 120), (250, 250))


def room_subentry(
    room_id: str = "chambre_test",
    name: str = "Chambre test",
    helpers: list[str] | None = None,
    calendar: str | None = None,
    door: bool = False,
    frames: int = 2,
) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            ROOM_ID: room_id,
            ROOM_NAME: name,
            ROOM_HELPERS: helpers if helpers is not None else [HELPER_1, HELPER_2],
            ROOM_CALENDAR: calendar,
            ROOM_DOOR_FRAME: door,
            ROOM_FRAMES: frames,
        },
        subentry_type=SUBENTRY_ROOM,
        title=name,
        unique_id=room_id,
    )


def make_entry(
    rooms: list[ConfigSubentryData] | None = None, **options: Any
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://immich.test:2283", CONF_API_KEY: "k"},
        options={CONF_PERMANENT_PERSONS: OWNERS, **options},
        subentries_data=rooms if rooms is not None else [room_subentry()],
        unique_id="http://immich.test:2283",
        version=2,
    )


def patch_immich():
    return patch("custom_components.immich_guest_slideshow.ImmichApiClient", FakeImmich)


async def setup_entry(
    hass: HomeAssistant,
    guests: dict[str, str] | None = None,
    entry: MockConfigEntry | None = None,
) -> MockConfigEntry:
    """Crée l'état des helpers puis installe l'intégration (client factice)."""
    hass.config.internal_url = "http://192.168.1.29:8123"
    for entity_id, value in (guests or {}).items():
        hass.states.async_set(entity_id, value)
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    with patch_immich():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry

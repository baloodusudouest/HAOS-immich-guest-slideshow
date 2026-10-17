"""Test d'intégration : démarrage complet, puis signalement via Réparations.

Reproduit ce qui se passe dans Home Assistant : l'entrée est configurée, un
helper invité reçoit un nom, et l'on vérifie qu'un élément de Réparations et
une notification apparaissent (ou non) selon ce qu'Immich répond.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_PERMANENT_PERSONS,
    CONF_ROOMS,
    CONF_URL,
    DOMAIN,
    KEY_HELPERS,
    KEY_NAME,
)

HELPER = "input_text.guest_chambre_test"
PEOPLE = {
    "Propriétaire 1": "aaaaaaaa-0000-4000-8000-000000000001",
    "Propriétaire 2": "aaaaaaaa-0000-4000-8000-000000000002",
    "Alexia": "1238c4da-3b98-41b9-b795-81d51e95a6d5",
}


class FakeImmich:
    """Client Immich factice (remplace ImmichApiClient)."""

    photos: dict[frozenset[str], int] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

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

    async def async_get_thumbnail(self, *args: Any, **kwargs: Any) -> bytes:
        return b""


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://immich.test:2283", CONF_API_KEY: "k"},
        options={
            CONF_PERMANENT_PERSONS: ["Propriétaire 1", "Propriétaire 2"],
            CONF_ROOMS: {
                "chambre_test": {KEY_NAME: "Chambre test", KEY_HELPERS: [HELPER]},
            },
        },
        unique_id="http://immich.test:2283",
    )


def _issues(hass: HomeAssistant) -> list[tuple[str, str]]:
    registry = ir.async_get(hass)
    return sorted(
        (i.translation_key or "", i.translation_placeholders["name"])
        for i in registry.issues.values()
        if i.domain == DOMAIN
    )


async def _setup(hass: HomeAssistant, helper_value: str) -> MockConfigEntry:
    hass.states.async_set(HELPER, helper_value)
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.immich_guest_slideshow.ImmichApiClient", FakeImmich
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_unknown_name_raises_repair_and_notification(
    hass: HomeAssistant,
) -> None:
    FakeImmich.photos = {}
    entry = await _setup(hass, "Zorglub")

    assert entry.state is ConfigEntryState.LOADED
    assert _issues(hass) == [("person_not_found", "Zorglub")]
    assert len(persistent_notification._async_get_or_create_notifications(hass)) == 1


async def test_few_photos_raises_repair(hass: HomeAssistant) -> None:
    FakeImmich.photos = {frozenset([PEOPLE["Alexia"]]): 1}
    entry = await _setup(hass, "Alexia")

    assert entry.state is ConfigEntryState.LOADED
    assert _issues(hass) == [("person_few_photos", "Alexia")]


async def test_enough_photos_raises_nothing(hass: HomeAssistant) -> None:
    FakeImmich.photos = {
        frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 6,
    }
    entry = await _setup(hass, "Alexia")

    assert entry.state is ConfigEntryState.LOADED
    assert _issues(hass) == []


async def test_changing_the_helper_updates_repairs(hass: HomeAssistant) -> None:
    """Un helper modifié après le démarrage déclenche bien le signalement."""
    FakeImmich.photos = {
        frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 6,
    }
    entry = await _setup(hass, "Alexia")
    assert _issues(hass) == []

    with patch(
        "custom_components.immich_guest_slideshow.ImmichApiClient", FakeImmich
    ):
        hass.states.async_set(HELPER, "Zorglub")
        await hass.async_block_till_done()
    assert _issues(hass) == [("person_not_found", "Zorglub")]

    hass.states.async_set(HELPER, "Alexia")
    await hass.async_block_till_done()
    assert _issues(hass) == []
    assert entry.state is ConfigEntryState.LOADED


async def test_link_service_fixes_the_issue(hass: HomeAssistant) -> None:
    FakeImmich.photos = {
        frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 6,
    }
    await _setup(hass, "Alex")
    assert _issues(hass) == [("person_not_found", "Alex")]

    await hass.services.async_call(
        DOMAIN,
        "link_person",
        {
            "name": "Alex",
            "person_id": "http://192.168.1.90:2283/people/"
            "1238c4da-3b98-41b9-b795-81d51e95a6d5?previousRoute=%2Fphotos",
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _issues(hass) == []

    response = await hass.services.async_call(
        DOMAIN, "unlink_person", {"name": "Alex"}, blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    assert response == {"removed": 1}
    assert _issues(hass) == [("person_not_found", "Alex")]

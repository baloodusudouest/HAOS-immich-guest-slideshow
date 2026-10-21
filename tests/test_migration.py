"""Migration v1 -> v2 : chambres des options -> une sous-entrée par chambre."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_ARRIVAL_HOUR,
    CONF_CACHE_SIZE,
    CONF_INTERVAL,
    CONF_PERMANENT_PERSONS,
    CONF_URL,
    DOMAIN,
    ROOM_CALENDAR,
    ROOM_DOOR_FRAME,
    ROOM_FRAMES,
    ROOM_HELPERS,
    ROOM_ID,
    SUBENTRY_ROOM,
)

from .common import OWNERS, FakeImmich, patch_immich

TOKEN = "jeton-des-cadres-v1"


def _v1_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_URL: "http://immich.test:2283", CONF_API_KEY: "k"},
        options={
            CONF_PERMANENT_PERSONS: OWNERS,
            CONF_INTERVAL: 30,
            CONF_CACHE_SIZE: 50,
            "picpak_mode": "visages",
            "picpak_arrival_hour": 10,
            "rooms": {
                "chambre_d_ami": {
                    "name": "Chambre d'ami",
                    "helpers": ["input_text.invite_chambre_ami_1", "input_text.invite_chambre_ami_2"],
                },
                "chambre_ado": {
                    "name": "Chambre Ado",
                    "helpers": ["input_text.invite_chambre_ado_1", "input_text.invite_chambre_ado_2"],
                },
            },
        },
        unique_id="http://immich.test:2283",
    )


async def test_v1_entry_is_migrated(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    FakeImmich.reset()
    entry = _v1_entry()
    entry.add_to_hass(hass)
    seen = {"last_seen": "2026-10-20T10:00:00+02:00", "assets": [], "index": -1}
    hass_storage[f"{DOMAIN}.picpak.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.picpak.{entry.entry_id}",
        "data": {
            "token": TOKEN,
            "frames": {
                "chambre_d_ami/1": seen,
                "chambre_d_ami/2": seen,
                "chambre_ado/1": {"last_seen": None},  # jamais flashé
            },
        },
    }
    # Entités et appareils v1 (entity_id renommé par l'utilisateur).
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    room_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_chambre_d_ami")},
    )
    frame_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_chambre_d_ami_picpak_1")},
    )
    ado_frame = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_chambre_ado_picpak_1")},
    )
    ent_reg.async_get_or_create(
        "image",
        DOMAIN,
        f"{entry.entry_id}_chambre_d_ami_image",
        config_entry=entry,
        device_id=room_dev.id,
        suggested_object_id="immich_chambre_ami",
    )
    hass.states.async_set("calendar.chambre_ami", "off")
    for helper in ("input_text.invite_chambre_ami_1", "input_text.invite_chambre_ami_2"):
        hass.states.async_set(helper, "")

    with patch_immich():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 2
    assert "rooms" not in entry.options and "picpak_mode" not in entry.options
    assert entry.options[CONF_ARRIVAL_HOUR] == 10
    assert entry.options[CONF_INTERVAL] == 15 and entry.options[CONF_CACHE_SIZE] == 100

    subs = {s.data[ROOM_ID]: s for s in entry.subentries.values()}
    assert set(subs) == {"chambre_d_ami", "chambre_ado"}
    ami = subs["chambre_d_ami"]
    assert ami.subentry_type == SUBENTRY_ROOM and ami.title == "Chambre d'ami"
    assert ami.data[ROOM_FRAMES] == 2  # deux cadres déjà connectés
    assert ami.data[ROOM_DOOR_FRAME] is False
    assert ami.data[ROOM_CALENDAR] == "calendar.chambre_ami"
    assert ami.data[ROOM_HELPERS] == [
        "input_text.invite_chambre_ami_1",
        "input_text.invite_chambre_ami_2",
    ]
    assert subs["chambre_ado"].data[ROOM_FRAMES] == 0

    # Même entity_id, rattaché à la ligne de la chambre.
    ent = ent_reg.async_get("image.immich_chambre_ami")
    assert ent is not None and ent.config_subentry_id == ami.subentry_id
    device = dev_reg.async_get(frame_dev.id)
    assert ami.subentry_id in device.config_entries_subentries[entry.entry_id]
    assert None not in device.config_entries_subentries[entry.entry_id]
    # Le cadre jamais connecté de la chambre Ado disparaît.
    assert dev_reg.async_get(ado_frame.id) is None

    # Jeton conservé : les URL des cadres flashés ne changent pas.
    assert entry.runtime_data.picpak.token == TOKEN
    assert set(entry.runtime_data.picpak.frames) == {"chambre_d_ami/1", "chambre_d_ami/2"}


async def test_v1_11_select_entities_are_removed(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    FakeImmich.reset()
    entry = _v1_entry()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "select",
        DOMAIN,
        f"{entry.entry_id}_chambre_d_ami_picpak_1_mode",
        config_entry=entry,
        suggested_object_id="picpak_chambre_d_ami_1_affichage",
    )
    with patch_immich():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.version == 2
    assert ent_reg.async_get("select.picpak_chambre_d_ami_1_affichage") is None

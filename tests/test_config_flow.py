"""Configuration : serveur Immich, options, et une ligne par chambre."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.immich_guest_slideshow.api import (
    ImmichAuthError,
    ImmichConnectionError,
)
from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_ARRIVAL_HOUR,
    CONF_CACHE_SIZE,
    CONF_INTERVAL,
    CONF_MIN_PHOTOS,
    CONF_PERMANENT_PERSONS,
    CONF_REBUILD_HOURS,
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

from .common import make_entry, room_subentry

USER_INPUT = {CONF_URL: "http://192.168.1.100:2283", CONF_API_KEY: "secret"}


async def test_user_flow_success(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.immich_guest_slideshow.config_flow.ImmichApiClient"
    ) as client_cls:
        client_cls.return_value.async_validate_connection = AsyncMock(
            return_value={"version": "3.0.1"}
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        with patch(
            "custom_components.immich_guest_slideshow.async_setup_entry",
            return_value=True,
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT
    entry = result["result"]
    assert entry.version == 2
    assert entry.options[CONF_INTERVAL] == 15 and entry.options[CONF_CACHE_SIZE] == 100


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (ImmichAuthError("bad key"), "invalid_auth"),
        (ImmichConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, side_effect: Exception, error: str
) -> None:
    with patch(
        "custom_components.immich_guest_slideshow.config_flow.ImmichApiClient"
    ) as client_cls:
        client_cls.return_value.async_validate_connection = AsyncMock(
            side_effect=side_effect
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_options_flow_general(hass: HomeAssistant) -> None:
    entry = make_entry([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["general", "picpak", "carnet"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PERMANENT_PERSONS: "Julien Bertrand, Yves Bouget",
            CONF_INTERVAL: 15,
            CONF_CACHE_SIZE: 100,
            CONF_ARRIVAL_HOUR: 10,
            CONF_REBUILD_HOURS: 12,
            CONF_MIN_PHOTOS: 2,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PERMANENT_PERSONS] == ["Julien Bertrand", "Yves Bouget"]
    assert result["data"][CONF_ARRIVAL_HOUR] == 10


async def test_add_room_subentry(hass: HomeAssistant) -> None:
    entry = make_entry([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    bad = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            ROOM_NAME: "Chambre d'ami",
            ROOM_HELPERS: ["input_text.a", "input_text.b", "input_text.c"],
            ROOM_DOOR_FRAME: False,
            ROOM_FRAMES: 2,
        },
    )
    assert bad["errors"] == {ROOM_HELPERS: "too_many_helpers"}

    with patch("custom_components.immich_guest_slideshow.async_setup_entry", return_value=True):
        result = await hass.config_entries.subentries.async_configure(
            bad["flow_id"],
            {
                ROOM_NAME: "Chambre d'ami",
                ROOM_CALENDAR: "calendar.chambre_ami",
                ROOM_HELPERS: ["input_text.a", "input_text.b"],
                ROOM_DOOR_FRAME: True,
                ROOM_FRAMES: 2,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.title == "Chambre d'ami" and sub.unique_id == "chambre_d_ami"
    assert sub.data[ROOM_ID] == "chambre_d_ami"
    assert sub.data[ROOM_DOOR_FRAME] is True and sub.data[ROOM_FRAMES] == 2


async def test_reconfigure_room_keeps_its_id(hass: HomeAssistant) -> None:
    entry = make_entry([room_subentry(room_id="chambre_d_ami", name="Chambre d'ami")])
    entry.add_to_hass(hass)
    sub = next(iter(entry.subentries.values()))
    result = await entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.immich_guest_slideshow.async_setup_entry", return_value=True):
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                ROOM_NAME: "Chambre des amis",
                ROOM_HELPERS: ["input_text.a"],
                ROOM_DOOR_FRAME: True,
                ROOM_FRAMES: 1,
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    sub = entry.subentries[sub.subentry_id]
    assert sub.data[ROOM_ID] == "chambre_d_ami"  # entités et URL conservées
    assert sub.title == "Chambre des amis" and sub.data[ROOM_FRAMES] == 1


async def test_options_flow_carnet(hass: HomeAssistant) -> None:
    """Carnet : adresse + jeton vérifiés ; « Bearer » retiré ; vide = sans Carnet."""
    from custom_components.immich_guest_slideshow.carnet import CarnetAuthError
    from custom_components.immich_guest_slideshow.const import (
        CONF_CARNET_TOKEN,
        CONF_CARNET_URL,
    )

    entry = make_entry([])
    entry.add_to_hass(hass)

    async def _open():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        return await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "carnet"}
        )

    target = "custom_components.immich_guest_slideshow.config_flow.CarnetClient.async_validate"
    result = await _open()
    assert result["step_id"] == "carnet"
    with patch(target, side_effect=CarnetAuthError("401")):
        bad = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CARNET_URL: "http://192.168.1.90:8096", CONF_CARNET_TOKEN: "faux"},
        )
    assert bad["errors"] == {"base": "carnet_invalid_auth"}
    with patch(target, return_value=None):
        ok = await hass.config_entries.options.async_configure(
            bad["flow_id"],
            {CONF_CARNET_URL: "http://192.168.1.90:8096/", CONF_CARNET_TOKEN: "Bearer abc"},
        )
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    assert ok["data"][CONF_CARNET_URL] == "http://192.168.1.90:8096"
    assert ok["data"][CONF_CARNET_TOKEN] == "abc"

    result = await _open()
    off = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CARNET_URL: "", CONF_CARNET_TOKEN: ""}
    )
    assert off["type"] is FlowResultType.CREATE_ENTRY
    assert off["data"][CONF_CARNET_URL] == "" and off["data"][CONF_CARNET_TOKEN] == ""

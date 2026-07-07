"""Tests du Config Flow (connexion Immich mockée)."""
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
    CONF_URL,
    DOMAIN,
)

USER_INPUT = {CONF_URL: "http://192.168.1.100:2283", CONF_API_KEY: "secret"}


@pytest.mark.asyncio
async def test_user_flow_success(hass: HomeAssistant) -> None:
    """Le flux crée une entrée quand la connexion est valide."""
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

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


@pytest.mark.asyncio
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
    """Les erreurs de connexion sont affichées dans le formulaire."""
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


@pytest.mark.asyncio
async def test_options_flow_general(hass: HomeAssistant) -> None:
    """Le menu d'options permet de régler les paramètres généraux."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.immich_guest_slideshow.const import (
        CONF_CACHE_SIZE,
        CONF_INTERVAL,
        CONF_PERMANENT_PERSONS,
        CONF_REBUILD_HOURS,
        CONF_ROOMS,
    )

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_INTERVAL: 15,
            CONF_CACHE_SIZE: 100,
            CONF_REBUILD_HOURS: 12,
            CONF_PERMANENT_PERSONS: "Propriétaire 1, Propriétaire 2",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_INTERVAL] == 15
    assert result["data"][CONF_PERMANENT_PERSONS] == [
        "Propriétaire 1",
        "Propriétaire 2",
    ]
    assert CONF_ROOMS in result["data"]  # chambres par défaut préservées

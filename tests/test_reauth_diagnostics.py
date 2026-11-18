"""2.5.2 (audit du 17/11/2026, M50) : réauthentification quand Immich refuse la
clé API, diagnostics sans clé ni nom d'invité ou de propriétaire."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.immich_guest_slideshow.api import (
    ImmichAuthError,
    ImmichConnectionError,
)
from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_CARNET_TOKEN,
    CONF_CARNET_URL,
    DOMAIN,
)
from custom_components.immich_guest_slideshow.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .common import HELPER_1, HELPER_2, OWNERS, FakeImmich, make_entry, setup_entry

FLOW_CLIENT = "custom_components.immich_guest_slideshow.config_flow.ImmichApiClient"


class RefusedImmich(FakeImmich):
    async def async_validate_connection(self):
        raise ImmichAuthError("Authentification refusée (401)")


# --------------------------------------------------------------------------- #
# Réauthentification
# --------------------------------------------------------------------------- #
async def test_refused_key_starts_reauth(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.immich_guest_slideshow.ImmichApiClient", RefusedImmich):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert flows[0]["step_id"] == "reauth_confirm"


async def _start_reauth(hass: HomeAssistant):
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    return entry, result


async def test_reauth_wrong_key_shows_error(hass: HomeAssistant) -> None:
    entry, result = await _start_reauth(hass)
    for exc, error in ((ImmichAuthError("401"), "invalid_auth"),
                       (ImmichConnectionError("loin"), "cannot_connect")):
        with patch(FLOW_CLIENT) as client_cls:
            client_cls.return_value.async_validate_connection = AsyncMock(side_effect=exc)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_API_KEY: "encore-fausse"}
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error}
    assert entry.data[CONF_API_KEY] == "k"


async def test_reauth_new_key_is_saved_and_reloaded(hass: HomeAssistant) -> None:
    entry, result = await _start_reauth(hass)
    with patch(FLOW_CLIENT) as client_cls, patch(
        "custom_components.immich_guest_slideshow.ImmichApiClient", FakeImmich
    ):
        client_cls.return_value.async_validate_connection = AsyncMock(
            return_value={"version": "3.0.1"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "nouvelle-cle"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "nouvelle-cle"
    assert client_cls.call_args.args[0] == entry.data["url"]      # même serveur
    assert entry.state is ConfigEntryState.LOADED


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
async def test_diagnostics_hide_key_token_and_names(hass: HomeAssistant) -> None:
    FakeImmich.reset({frozenset(["eeeeeeee-0000-4000-8000-000000000001"]): 5,
                      frozenset(["bbbbbbbb-0000-4000-8000-000000000003"]): 5})
    entry = make_entry(**{CONF_CARNET_URL: "http://carnet.test", CONF_CARNET_TOKEN: "jeton-carnet"})
    entry = await setup_entry(hass, {HELPER_1: "Alexia", HELPER_2: "Bruno"}, entry)
    assert entry.state is ConfigEntryState.LOADED

    diag = await async_get_config_entry_diagnostics(hass, entry)
    texte = json.dumps(diag, ensure_ascii=False, default=str)

    for secret in ("jeton-carnet", '"k"', "Alexia", "Bruno", *OWNERS):
        assert secret not in texte, secret
    room = diag["rooms"]["chambre_test"]
    assert room["guest_count"] == 2
    assert diag["entry"]["data"][CONF_API_KEY] == "**REDACTED**"

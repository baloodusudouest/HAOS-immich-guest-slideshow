"""Config Flow de l'intégration Immich Guest Slideshow.

L'URL et la clé API sont saisies par l'utilisateur et validées par un
appel réel à l'API Immich. La clé n'apparaît jamais dans le code.

Le flux d'options permet de régler le diaporama (intervalle, cache,
personnes permanentes, rafraîchissement périodique) et de gérer les
chambres (nom + helpers input_text) directement depuis l'interface.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util import slugify

from .api import ImmichApiClient, ImmichAuthError, ImmichConnectionError
from .const import (
    CONF_API_KEY,
    CONF_CACHE_SIZE,
    CONF_INTERVAL,
    CONF_PERMANENT_PERSONS,
    CONF_REBUILD_HOURS,
    CONF_ROOMS,
    CONF_URL,
    DEFAULT_CACHE_SIZE,
    DEFAULT_INTERVAL,
    DEFAULT_PERMANENT_PERSONS,
    DEFAULT_REBUILD_HOURS,
    DEFAULT_ROOMS,
    DOMAIN,
    KEY_HELPERS,
    KEY_NAME,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://192.168.1.100:2283"): str,
        vol.Required(CONF_API_KEY): str,
    }
)


class ImmichGuestSlideshowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flux de configuration UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape initiale : URL + clé API, avec validation de connexion."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            client = ImmichApiClient(url, user_input[CONF_API_KEY], session)
            try:
                about = await client.async_validate_connection()
            except ImmichAuthError:
                errors["base"] = "invalid_auth"
            except ImmichConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue lors de la validation")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                version = about.get("version", "?")
                return self.async_create_entry(
                    title=f"Immich ({url}) v{version}",
                    data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Retourne le flux d'options."""
        return ImmichGuestSlideshowOptionsFlow()


class ImmichGuestSlideshowOptionsFlow(OptionsFlow):
    """Options : réglages généraux et gestion des chambres."""

    def __init__(self) -> None:
        """Initialise une copie de travail des options."""
        self._options: dict[str, Any] | None = None

    @property
    def options(self) -> dict[str, Any]:
        """Copie de travail (fusionnée à chaque sauvegarde partielle)."""
        if self._options is None:
            self._options = dict(self.config_entry.options)
            self._options.setdefault(CONF_ROOMS, dict(DEFAULT_ROOMS))
        return self._options

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu principal des options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "add_room", "remove_room"],
        )

    # ------------------------------------------------------------------ #
    # Réglages généraux
    # ------------------------------------------------------------------ #
    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Intervalle, cache, rebuild périodique, personnes permanentes."""
        if user_input is not None:
            self.options[CONF_INTERVAL] = user_input[CONF_INTERVAL]
            self.options[CONF_CACHE_SIZE] = user_input[CONF_CACHE_SIZE]
            self.options[CONF_REBUILD_HOURS] = user_input[CONF_REBUILD_HOURS]
            self.options[CONF_PERMANENT_PERSONS] = [
                name.strip()
                for name in user_input[CONF_PERMANENT_PERSONS].split(",")
                if name.strip()
            ]
            return self.async_create_entry(title="", data=self.options)

        opts = self.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERVAL,
                    default=opts.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
                vol.Required(
                    CONF_CACHE_SIZE,
                    default=opts.get(CONF_CACHE_SIZE, DEFAULT_CACHE_SIZE),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1000)),
                vol.Required(
                    CONF_REBUILD_HOURS,
                    default=opts.get(CONF_REBUILD_HOURS, DEFAULT_REBUILD_HOURS),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=168)),
                vol.Required(
                    CONF_PERMANENT_PERSONS,
                    default=", ".join(
                        opts.get(CONF_PERMANENT_PERSONS, DEFAULT_PERMANENT_PERSONS)
                    ),
                ): str,
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    # ------------------------------------------------------------------ #
    # Ajout d'une chambre
    # ------------------------------------------------------------------ #
    async def async_step_add_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ajoute une chambre (nom + helpers input_text)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            room_id = slugify(user_input[KEY_NAME])
            rooms: dict[str, Any] = self.options[CONF_ROOMS]
            if not room_id:
                errors["base"] = "invalid_name"
            elif room_id in rooms:
                errors["base"] = "room_exists"
            elif not user_input[KEY_HELPERS]:
                errors["base"] = "no_helpers"
            else:
                rooms = dict(rooms)
                rooms[room_id] = {
                    KEY_NAME: user_input[KEY_NAME].strip(),
                    KEY_HELPERS: list(user_input[KEY_HELPERS]),
                }
                self.options[CONF_ROOMS] = rooms
                return self.async_create_entry(title="", data=self.options)

        schema = vol.Schema(
            {
                vol.Required(KEY_NAME): str,
                vol.Required(KEY_HELPERS): EntitySelector(
                    EntitySelectorConfig(domain="input_text", multiple=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="add_room", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # Suppression de chambres
    # ------------------------------------------------------------------ #
    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supprime une ou plusieurs chambres."""
        rooms: dict[str, Any] = self.options[CONF_ROOMS]
        if user_input is not None:
            remaining = {
                room_id: room
                for room_id, room in rooms.items()
                if room_id not in user_input[CONF_ROOMS]
            }
            self.options[CONF_ROOMS] = remaining
            return self.async_create_entry(title="", data=self.options)

        schema = vol.Schema(
            {
                vol.Required(CONF_ROOMS, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": room_id, "label": room[KEY_NAME]}
                            for room_id, room in rooms.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="remove_room", data_schema=schema)

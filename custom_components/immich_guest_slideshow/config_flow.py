"""Configuration de l'intégration Immich Guest Slideshow (v2).

- **Entrée** : serveur Immich (URL + clé API, validées par un vrai appel).
- **Options** : réglages généraux (propriétaires, diaporama, heure
  d'arrivée) et réglages des cadres PicPak.
- **Sous-entrées « Chambre »** : une ligne par chambre, avec son propre bouton
  « Configurer » : nom, calendrier, invités, cadre de porte, cadres dans la
  chambre.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)
from homeassistant.util import slugify

from .api import ImmichApiClient, ImmichAuthError, ImmichConnectionError
from .const import (
    CONF_API_KEY,
    CONF_ARRIVAL_HOUR,
    CONF_CACHE_SIZE,
    CONF_INTERVAL,
    CONF_MIN_PHOTOS,
    CONF_PERMANENT_PERSONS,
    CONF_PICPAK_BASE_URL,
    CONF_PICPAK_INTERVAL,
    CONF_PICPAK_LOGO,
    CONF_PICPAK_LOGO_2,
    CONF_PICPAK_PHOTOS,
    CONF_PICPAK_QUIET,
    CONF_PICPAK_RESET_BUTTON,
    CONF_PICPAK_SUBTITLE,
    CONF_PICPAK_TITLE,
    CONF_REBUILD_HOURS,
    CONF_URL,
    DEFAULT_ARRIVAL_HOUR,
    DEFAULT_CACHE_SIZE,
    DEFAULT_INTERVAL,
    DEFAULT_MIN_PHOTOS,
    DEFAULT_PERMANENT_PERSONS,
    DEFAULT_PICPAK_BASE_URL,
    DEFAULT_PICPAK_INTERVAL,
    DEFAULT_PICPAK_LOGO,
    DEFAULT_PICPAK_LOGO_2,
    DEFAULT_PICPAK_PHOTOS,
    DEFAULT_PICPAK_QUIET,
    DEFAULT_PICPAK_RESET_BUTTON,
    DEFAULT_PICPAK_SUBTITLE,
    DEFAULT_PICPAK_TITLE,
    DEFAULT_REBUILD_HOURS,
    DOMAIN,
    MAX_FRAME_PHOTOS,
    MAX_GUESTS,
    MAX_ROOM_FRAMES,
    ROOM_CALENDAR,
    ROOM_DOOR_FRAME,
    ROOM_FRAMES,
    ROOM_HELPERS,
    ROOM_ID,
    ROOM_NAME,
    SUBENTRY_ROOM,
)
from .stay import parse_quiet

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="http://192.168.1.100:2283"): str,
        vol.Required(CONF_API_KEY): str,
    }
)


class ImmichGuestSlideshowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un serveur Immich."""

    VERSION = 2
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """URL + clé API, validées par un appel à l'API."""
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
            except Exception:
                _LOGGER.exception("Erreur inattendue lors de la validation")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Immich ({url}) v{about.get('version', '?')}",
                    data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]},
                    options={
                        CONF_INTERVAL: DEFAULT_INTERVAL,
                        CONF_CACHE_SIZE: DEFAULT_CACHE_SIZE,
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Une sous-entrée par chambre."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ImmichGuestSlideshowOptionsFlow()


# --------------------------------------------------------------------------- #
# Options : réglages généraux et cadres PicPak
# --------------------------------------------------------------------------- #
class ImmichGuestSlideshowOptionsFlow(OptionsFlow):
    """Réglages communs à toutes les chambres."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None

    @property
    def options(self) -> dict[str, Any]:
        if self._options is None:
            self._options = dict(self.config_entry.options)
        return self._options

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["general", "picpak"])

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Propriétaires, diaporama des dashboards, heure d'arrivée."""
        opts = self.options
        if user_input is not None:
            opts[CONF_PERMANENT_PERSONS] = [
                n.strip() for n in user_input[CONF_PERMANENT_PERSONS].split(",") if n.strip()
            ]
            for key in (
                CONF_INTERVAL,
                CONF_CACHE_SIZE,
                CONF_ARRIVAL_HOUR,
                CONF_REBUILD_HOURS,
                CONF_MIN_PHOTOS,
            ):
                opts[key] = int(user_input[key])
            return self.async_create_entry(title="", data=opts)

        def _num(key: str, default: int, lo: int, hi: int) -> tuple:
            return (
                vol.Required(key, default=opts.get(key, default)),
                vol.All(vol.Coerce(int), vol.Range(min=lo, max=hi)),
            )

        fields = dict(
            [
                (
                    vol.Required(
                        CONF_PERMANENT_PERSONS,
                        default=", ".join(
                            opts.get(CONF_PERMANENT_PERSONS, DEFAULT_PERMANENT_PERSONS)
                        ),
                    ),
                    str,
                ),
                _num(CONF_INTERVAL, DEFAULT_INTERVAL, 5, 3600),
                _num(CONF_CACHE_SIZE, DEFAULT_CACHE_SIZE, 5, 1000),
                _num(CONF_ARRIVAL_HOUR, DEFAULT_ARRIVAL_HOUR, 0, 23),
                _num(CONF_REBUILD_HOURS, DEFAULT_REBUILD_HOURS, 0, 168),
                _num(CONF_MIN_PHOTOS, DEFAULT_MIN_PHOTOS, 1, 100),
            ]
        )
        return self.async_show_form(step_id="general", data_schema=vol.Schema(fields))

    async def async_step_picpak(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rythme, pause nocturne, logo, URL vue par les cadres, bouton."""
        errors: dict[str, str] = {}
        opts = self.options
        if user_input is not None:
            quiet = (user_input.get(CONF_PICPAK_QUIET) or "").strip()
            base_url = (user_input.get(CONF_PICPAK_BASE_URL) or "").strip().rstrip("/")
            if quiet and parse_quiet(quiet) is None:
                errors[CONF_PICPAK_QUIET] = "invalid_quiet"
            elif base_url and not base_url.startswith(("http://", "https://")):
                errors[CONF_PICPAK_BASE_URL] = "invalid_base_url"
            else:
                opts[CONF_PICPAK_INTERVAL] = int(user_input[CONF_PICPAK_INTERVAL])
                opts[CONF_PICPAK_PHOTOS] = int(user_input[CONF_PICPAK_PHOTOS])
                opts[CONF_PICPAK_QUIET] = quiet
                opts[CONF_PICPAK_BASE_URL] = base_url
                for key in (
                    CONF_PICPAK_LOGO,
                    CONF_PICPAK_LOGO_2,
                    CONF_PICPAK_TITLE,
                    CONF_PICPAK_SUBTITLE,
                    CONF_PICPAK_RESET_BUTTON,
                ):
                    opts[key] = (user_input.get(key) or "").strip()
                return self.async_create_entry(title="", data=opts)
            opts = {**opts, **user_input}

        def _text(key: str, default: str) -> vol.Optional:
            return vol.Optional(key, description={"suggested_value": opts.get(key, default)})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PICPAK_INTERVAL,
                    default=opts.get(CONF_PICPAK_INTERVAL, DEFAULT_PICPAK_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=15, max=720)),
                _text(CONF_PICPAK_QUIET, DEFAULT_PICPAK_QUIET): str,
                vol.Required(
                    CONF_PICPAK_PHOTOS,
                    default=opts.get(CONF_PICPAK_PHOTOS, DEFAULT_PICPAK_PHOTOS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_FRAME_PHOTOS)),
                _text(CONF_PICPAK_LOGO, DEFAULT_PICPAK_LOGO): str,
                _text(CONF_PICPAK_LOGO_2, DEFAULT_PICPAK_LOGO_2): str,
                _text(CONF_PICPAK_TITLE, DEFAULT_PICPAK_TITLE): str,
                _text(CONF_PICPAK_SUBTITLE, DEFAULT_PICPAK_SUBTITLE): str,
                _text(CONF_PICPAK_BASE_URL, DEFAULT_PICPAK_BASE_URL): str,
                _text(CONF_PICPAK_RESET_BUTTON, DEFAULT_PICPAK_RESET_BUTTON): str,
            }
        )
        return self.async_show_form(step_id="picpak", data_schema=schema, errors=errors)


# --------------------------------------------------------------------------- #
# Sous-entrée : une chambre
# --------------------------------------------------------------------------- #
def _room_schema(values: dict[str, Any]) -> vol.Schema:
    """Formulaire d'une chambre, prérempli avec ``values``."""

    def suggested(key: str) -> dict[str, Any]:
        value = values.get(key)
        return {"suggested_value": value} if value not in (None, "", []) else {}

    return vol.Schema(
        {
            vol.Required(ROOM_NAME, description=suggested(ROOM_NAME)): TextSelector(),
            vol.Optional(ROOM_CALENDAR, description=suggested(ROOM_CALENDAR)): EntitySelector(
                EntitySelectorConfig(domain="calendar")
            ),
            vol.Required(ROOM_HELPERS, description=suggested(ROOM_HELPERS)): EntitySelector(
                EntitySelectorConfig(domain="input_text", multiple=True)
            ),
            vol.Required(
                ROOM_DOOR_FRAME, default=bool(values.get(ROOM_DOOR_FRAME, False))
            ): BooleanSelector(),
            vol.Required(
                ROOM_FRAMES, default=int(values.get(ROOM_FRAMES, 0) or 0)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=MAX_ROOM_FRAMES, step=1, mode=NumberSelectorMode.SLIDER
                )
            ),
        }
    )


def _validate_room(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Nettoie la saisie d'une chambre ; renvoie (données, erreurs)."""
    errors: dict[str, str] = {}
    name = str(user_input.get(ROOM_NAME) or "").strip()
    helpers = list(dict.fromkeys(user_input.get(ROOM_HELPERS) or []))
    if not slugify(name):
        errors[ROOM_NAME] = "invalid_name"
    if not helpers:
        errors[ROOM_HELPERS] = "no_helpers"
    elif len(helpers) > MAX_GUESTS:
        errors[ROOM_HELPERS] = "too_many_helpers"
    data = {
        ROOM_NAME: name,
        ROOM_CALENDAR: user_input.get(ROOM_CALENDAR) or None,
        ROOM_HELPERS: helpers,
        ROOM_DOOR_FRAME: bool(user_input.get(ROOM_DOOR_FRAME, False)),
        ROOM_FRAMES: int(user_input.get(ROOM_FRAMES, 0) or 0),
    }
    return data, errors


class RoomSubentryFlow(ConfigSubentryFlow):
    """Ajout et réglage d'une chambre."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Nouvelle chambre."""
        errors: dict[str, str] = {}
        values: dict[str, Any] = {}
        if user_input is not None:
            data, errors = _validate_room(user_input)
            values = {**user_input, **data}
            room_id = slugify(data[ROOM_NAME])
            existing = {
                sub.unique_id for sub in self._get_entry().subentries.values()
            }
            if not errors and room_id in existing:
                errors[ROOM_NAME] = "room_exists"
            if not errors:
                return self.async_create_entry(
                    title=data[ROOM_NAME],
                    data={ROOM_ID: room_id, **data},
                    unique_id=room_id,
                )
        return self.async_show_form(
            step_id="user", data_schema=_room_schema(values), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Réglages d'une chambre existante (son identifiant ne change pas :
        entités et URL des cadres sont conservées)."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        values: dict[str, Any] = dict(subentry.data)
        if user_input is not None:
            data, errors = _validate_room(user_input)
            values = {**user_input, **data}
            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=data[ROOM_NAME],
                    data={ROOM_ID: subentry.data[ROOM_ID], **data},
                )
        return self.async_show_form(
            step_id="reconfigure", data_schema=_room_schema(values), errors=errors
        )

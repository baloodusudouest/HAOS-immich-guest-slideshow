"""Intégration Immich Guest Slideshow.

Crée un moteur de diaporama indépendant par chambre, écoute les helpers
input_text (invités) et expose des entités image + sensor par chambre.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)

from .api import ImmichApiClient, ImmichApiError
from .const import (
    ATTR_ROOM,
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
    SERVICE_NEXT,
    SERVICE_REFRESH,
)
from .coordinator import RoomCoordinator
from .slideshow import SlideshowEngine

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR]

type ImmichConfigEntry = ConfigEntry[ImmichRuntimeData]


@dataclass
class ImmichRuntimeData:
    """Données d'exécution attachées à la config entry."""

    api: ImmichApiClient
    coordinators: dict[str, RoomCoordinator] = field(default_factory=dict)


async def async_setup_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Configure l'intégration depuis une config entry."""
    session = async_get_clientsession(hass)
    api = ImmichApiClient(entry.data[CONF_URL], entry.data[CONF_API_KEY], session)

    try:
        await api.async_validate_connection()
    except ImmichApiError as err:
        raise ConfigEntryNotReady(err) from err

    interval = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
    cache_size = entry.options.get(CONF_CACHE_SIZE, DEFAULT_CACHE_SIZE)
    rebuild_hours = entry.options.get(CONF_REBUILD_HOURS, DEFAULT_REBUILD_HOURS)
    permanents = entry.options.get(
        CONF_PERMANENT_PERSONS, DEFAULT_PERMANENT_PERSONS
    )
    rooms: dict[str, dict] = entry.options.get(CONF_ROOMS) or DEFAULT_ROOMS

    runtime = ImmichRuntimeData(api=api)
    helper_to_room: dict[str, str] = {}

    for room_id, room in rooms.items():
        engine = SlideshowEngine(
            hass,
            api,
            room_id=room_id,
            room_name=room[KEY_NAME],
            helpers=room[KEY_HELPERS],
            permanents=permanents,
            cache_size=cache_size,
        )
        coordinator = RoomCoordinator(hass, engine, interval, rebuild_hours)
        await engine.async_rebuild()
        await coordinator.async_config_entry_first_refresh()
        runtime.coordinators[room_id] = coordinator
        for helper in room[KEY_HELPERS]:
            helper_to_room[helper] = room_id

    entry.runtime_data = runtime
    _async_cleanup_stale_devices(hass, entry, set(rooms))

    # --- Détection des changements d'invités ---------------------------- #
    async def _on_helper_change(event: Event[EventStateChangedData]) -> None:
        """Un helper a changé : rebuild du cache de la chambre concernée."""
        entity_id = event.data["entity_id"]
        old = event.data["old_state"]
        new = event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        room_id = helper_to_room.get(entity_id)
        if room_id is None:
            return
        _LOGGER.debug("Helper %s modifié -> rebuild %s", entity_id, room_id)
        await runtime.coordinators[room_id].async_rebuild_and_refresh()

    entry.async_on_unload(
        async_track_state_change_event(
            hass, list(helper_to_room), _on_helper_change
        )
    )

    # --- Service manuel de rafraîchissement ----------------------------- #
    async def _handle_refresh(call: ServiceCall) -> None:
        """Service immich_guest_slideshow.refresh."""
        room = call.data.get(ATTR_ROOM)
        targets = (
            [runtime.coordinators[room]]
            if room and room in runtime.coordinators
            else list(runtime.coordinators.values())
        )
        for coordinator in targets:
            await coordinator.async_rebuild_and_refresh()

    async def _handle_next(call: ServiceCall) -> None:
        """Service immich_guest_slideshow.next : image suivante immédiate."""
        room = call.data.get(ATTR_ROOM)
        targets = (
            [runtime.coordinators[room]]
            if room and room in runtime.coordinators
            else list(runtime.coordinators.values())
        )
        for coordinator in targets:
            await coordinator.async_refresh()

    service_schema = vol.Schema({vol.Optional(ATTR_ROOM): cv.string})
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=service_schema
    )
    hass.services.async_register(
        DOMAIN, SERVICE_NEXT, _handle_next, schema=service_schema
    )

    # Recharger si les options changent
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_cleanup_stale_devices(
    hass: HomeAssistant, entry: ImmichConfigEntry, room_ids: set[str]
) -> None:
    """Supprime les appareils des chambres retirées de la configuration."""
    registry = dr.async_get(hass)
    valid = {(DOMAIN, f"{entry.entry_id}_{room_id}") for room_id in room_ids}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not (device.identifiers & valid):
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recharge l'entrée quand les options changent."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Décharge la config entry."""
    for service in (SERVICE_REFRESH, SERVICE_NEXT):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

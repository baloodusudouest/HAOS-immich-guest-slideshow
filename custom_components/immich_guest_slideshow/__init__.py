"""Intégration Immich Guest Slideshow (v2).

Une entrée par serveur Immich (URL, clé API, propriétaires), et **une
sous-entrée par chambre** — une ligne par chambre dans Paramètres → Appareils
et services — qui regroupe :

- le diaporama des dashboards invité et gestionnaire (``image.immich_<chambre>``) ;
- les cadres PicPak de la chambre (0 à 2) et celui de sa porte (facultatif).

Voir :mod:`.slideshow` (choix des photos), :mod:`.stay` (séjours d'après le
calendrier) et :mod:`.picpak` (cadres e-ink).

v2.3.0 (31/10/2026) : suppression du service ``sync_albums`` et des albums
Immich par lit. Ils servaient au circuit manuel (téléchargement de l'album
puis envoi par l'app PicPak) ; tous les cadres sont désormais flashés
open-PicPak et reçoivent leurs images directement. La clé API n'a plus
besoin des permissions sur les albums.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .api import ImmichApiClient, ImmichApiError, extract_person_uuid, normalize_name
from .const import (
    ATTR_BASE_URL,
    ATTR_BED,
    ATTR_NAME,
    ATTR_PERSON_ID,
    ATTR_ROOM,
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
    LEGACY_CONF_ROOMS,
    LEGACY_KEY_HELPERS,
    LEGACY_KEY_NAME,
    LEGACY_PICPAK_ARRIVAL_HOUR,
    LEGACY_PICPAK_MODE,
    MAX_ROOM_FRAMES,
    ROOM_CALENDAR,
    ROOM_DOOR_FRAME,
    ROOM_FRAMES,
    ROOM_HELPERS,
    ROOM_ID,
    ROOM_NAME,
    SERVICE_LINK_PERSON,
    SERVICE_NEXT,
    SERVICE_PICPAK_NEXT,
    SERVICE_PICPAK_URLS,
    SERVICE_PREVIOUS,
    SERVICE_REFRESH,
    SERVICE_UNLINK_PERSON,
    SUBENTRY_ROOM,
)
from .coordinator import RoomCoordinator
from .person_links import PersonLinks
from .picpak import PicPakFrameView, PicPakManager, PicPakSettings
from .problems import ProblemReporter
from .room import RoomConfig, RoomRuntime
from .slideshow import SlideshowEngine
from .stay import StayTracker, parse_quiet

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR]

# La vue HTTP des cadres est globale (une par instance de HA) : elle retrouve
# la bonne entrée grâce au jeton de l'URL.
_PICPAK_VIEW_KEY = f"{DOMAIN}_picpak_view"

type ImmichConfigEntry = ConfigEntry[ImmichRuntimeData]


@dataclass
class ImmichRuntimeData:
    """Données d'exécution attachées à la config entry."""

    api: ImmichApiClient
    links: PersonLinks
    reporter: ProblemReporter
    picpak: PicPakManager
    rooms: dict[str, RoomRuntime] = field(default_factory=dict)
    entry_id: str = ""

    @property
    def coordinators(self) -> dict[str, RoomCoordinator]:
        """Coordinateur du diaporama de chaque chambre."""
        return {r: room.coordinator for r, room in self.rooms.items() if room.coordinator}

    async def async_rebuild_all(self) -> None:
        """Reconstruit toutes les chambres (après un changement de liaison)."""
        for room_id, room in self.rooms.items():
            if room.coordinator is not None:
                await room.coordinator.async_rebuild_and_refresh()
            self.picpak.async_room_changed(room_id, guests_changed=False)


# --------------------------------------------------------------------------- #
# Migration v1 -> v2 : chambres en sous-entrées
# --------------------------------------------------------------------------- #
def _guess_calendar(hass: HomeAssistant, room_id: str, name: str) -> str | None:
    """Calendrier probable d'une chambre v1 (``calendar.<chambre>``)."""
    registry = er.async_get(hass)
    candidates = [
        f"calendar.{room_id}",
        f"calendar.{room_id.replace('_d_', '_')}",
        f"calendar.{slugify(name)}",
        f"calendar.{slugify(name).replace('_d_', '_')}",
    ]
    for entity_id in dict.fromkeys(candidates):
        if registry.async_get(entity_id) is not None or hass.states.get(entity_id):
            return entity_id
    return None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (chambres dans les options) -> v2 (une sous-entrée par chambre).

    Rien n'est perdu : identifiants des chambres, entités (mêmes entity_id),
    jeton et URL des cadres déjà flashés. Une chambre reçoit autant de cadres
    qu'elle en avait de **déjà connectés** ; le cadre de porte est à activer
    dans les réglages de la chambre.
    """
    if entry.version > 2:
        return False
    if entry.version == 2:
        return True

    options = dict(entry.options)
    legacy_rooms: dict[str, dict] = dict(options.pop(LEGACY_CONF_ROOMS, None) or {})
    options.pop(LEGACY_PICPAK_MODE, None)
    if LEGACY_PICPAK_ARRIVAL_HOUR in options:
        options[CONF_ARRIVAL_HOUR] = options.pop(LEGACY_PICPAK_ARRIVAL_HOUR)
    # Nouveau rythme des dashboards : 100 photos, une toutes les 15 s.
    options[CONF_INTERVAL] = DEFAULT_INTERVAL
    options[CONF_CACHE_SIZE] = DEFAULT_CACHE_SIZE

    stored = await Store(hass, 1, f"{DOMAIN}.picpak.{entry.entry_id}").async_load() or {}
    seen_frames = {
        key
        for key, frame in (stored.get("frames") or {}).items()
        if isinstance(frame, dict) and frame.get("last_seen")
    }

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    hass.config_entries.async_update_entry(entry, options=options)

    # v1.11 : entités « Affichage pendant le séjour » (photos / visages) par
    # cadre, remplacées par le cadre de porte.
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.domain == "select":
            ent_reg.async_remove(ent.entity_id)

    for room_id, room in legacy_rooms.items():
        name = str(room.get(LEGACY_KEY_NAME) or room_id)
        helpers = list(room.get(LEGACY_KEY_HELPERS) or [])[:2]
        frames = sum(
            1 for slot in range(1, MAX_ROOM_FRAMES + 1) if f"{room_id}/{slot}" in seen_frames
        )
        subentry = ConfigSubentry(
            data=MappingProxyType(
                {
                    ROOM_ID: room_id,
                    ROOM_NAME: name,
                    ROOM_HELPERS: helpers,
                    ROOM_CALENDAR: _guess_calendar(hass, room_id, name),
                    ROOM_DOOR_FRAME: False,
                    ROOM_FRAMES: frames,
                }
            ),
            subentry_type=SUBENTRY_ROOM,
            title=name,
            unique_id=room_id,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        sid = subentry.subentry_id
        prefix = f"{entry.entry_id}_{room_id}"
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            ident = next((i[1] for i in device.identifiers if i[0] == DOMAIN), "")
            if ident != prefix and not ident.startswith(f"{prefix}_picpak_"):
                continue
            dev_reg.async_update_device(
                device.id, add_config_entry_id=entry.entry_id, add_config_subentry_id=sid
            )
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id, remove_config_subentry_id=None
            )
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if ent.unique_id.startswith(f"{prefix}_"):
                ent_reg.async_update_entity(ent.entity_id, config_subentry_id=sid)

    hass.config_entries.async_update_entry(entry, version=2, minor_version=1)
    _LOGGER.info(
        "Immich Guest Slideshow migré en v2 : %d chambre(s) en sous-entrées",
        len(legacy_rooms),
    )
    return True


# --------------------------------------------------------------------------- #
# Mise en place
# --------------------------------------------------------------------------- #
def _picpak_settings(entry: ConfigEntry) -> PicPakSettings:
    opts = entry.options
    return PicPakSettings(
        interval_min=int(opts.get(CONF_PICPAK_INTERVAL, DEFAULT_PICPAK_INTERVAL)),
        photos=int(opts.get(CONF_PICPAK_PHOTOS, DEFAULT_PICPAK_PHOTOS)),
        quiet=parse_quiet(opts.get(CONF_PICPAK_QUIET, DEFAULT_PICPAK_QUIET)),
        logo_path=str(opts.get(CONF_PICPAK_LOGO, DEFAULT_PICPAK_LOGO) or ""),
        logo_path_2=str(opts.get(CONF_PICPAK_LOGO_2, DEFAULT_PICPAK_LOGO_2) or ""),
        title=str(opts.get(CONF_PICPAK_TITLE, DEFAULT_PICPAK_TITLE) or ""),
        subtitle=str(opts.get(CONF_PICPAK_SUBTITLE, DEFAULT_PICPAK_SUBTITLE) or ""),
        base_url=str(opts.get(CONF_PICPAK_BASE_URL, DEFAULT_PICPAK_BASE_URL) or ""),
    )


def room_configs(entry: ConfigEntry) -> list[RoomConfig]:
    """Chambres de l'entrée (sous-entrées « room »)."""
    return [
        RoomConfig.from_data(sub.data, sub.subentry_id)
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_ROOM and sub.data.get(ROOM_ID)
    ]


async def async_setup_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Configure l'intégration depuis une config entry."""
    session = async_get_clientsession(hass)
    api = ImmichApiClient(entry.data[CONF_URL], entry.data[CONF_API_KEY], session)
    try:
        await api.async_validate_connection()
    except ImmichApiError as err:
        raise ConfigEntryNotReady(err) from err

    opts = entry.options
    interval = int(opts.get(CONF_INTERVAL, DEFAULT_INTERVAL))
    cache_size = int(opts.get(CONF_CACHE_SIZE, DEFAULT_CACHE_SIZE))
    rebuild_hours = int(opts.get(CONF_REBUILD_HOURS, DEFAULT_REBUILD_HOURS))
    min_photos = int(opts.get(CONF_MIN_PHOTOS, DEFAULT_MIN_PHOTOS))
    arrival_hour = int(opts.get(CONF_ARRIVAL_HOUR, DEFAULT_ARRIVAL_HOUR))
    permanents = list(opts.get(CONF_PERMANENT_PERSONS, DEFAULT_PERMANENT_PERSONS))

    links = PersonLinks.create(hass, entry.entry_id)
    await links.async_load()
    reporter = ProblemReporter(hass, entry.entry_id)
    picpak = PicPakManager(hass, entry.entry_id, api, _picpak_settings(entry))
    await picpak.async_load()
    runtime = ImmichRuntimeData(
        api=api, links=links, reporter=reporter, picpak=picpak, entry_id=entry.entry_id
    )

    helper_to_room: dict[str, str] = {}
    calendar_to_rooms: dict[str, list[str]] = {}
    for config in room_configs(entry):
        room_id = config.room_id
        engine = SlideshowEngine(
            hass,
            api,
            room_id=room_id,
            room_name=config.name,
            helpers=list(config.helpers),
            permanents=permanents,
            cache_size=cache_size,
            min_photos=min_photos,
            links=links,
            reporter=reporter,
        )
        stay = StayTracker(
            hass,
            config.calendar,
            arrival_hour,
            closed_at=lambda: picpak.closed_at,
            closed_without_calendar=partial(picpak.is_closed_without_calendar, room_id),
        )
        room = RoomRuntime(config=config, engine=engine, stay=stay)
        room.coordinator = RoomCoordinator(hass, engine, stay, interval, rebuild_hours)
        runtime.rooms[room_id] = room
        for helper in config.helpers:
            helper_to_room[helper] = room_id
        if config.calendar:
            calendar_to_rooms.setdefault(config.calendar, []).append(room_id)

    picpak.async_attach_rooms(runtime.rooms)
    for room in runtime.rooms.values():
        await room.engine.async_rebuild()
        await room.coordinator.async_config_entry_first_refresh()

    if not hass.data.get(_PICPAK_VIEW_KEY):
        hass.http.register_view(PicPakFrameView())
        hass.data[_PICPAK_VIEW_KEY] = True

    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    _async_cleanup_stale_devices(hass, entry, runtime)

    # --- Invités modifiés ------------------------------------------------- #
    async def _on_helper_change(event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        room_id = helper_to_room.get(event.data["entity_id"])
        if room_id is None:
            return
        picpak.async_room_changed(room_id)
        await runtime.rooms[room_id].coordinator.async_rebuild_and_refresh()

    if helper_to_room:
        entry.async_on_unload(
            async_track_state_change_event(hass, list(helper_to_room), _on_helper_change)
        )

    # --- Début / fin d'un événement du calendrier -------------------------- #
    @callback
    def _on_calendar_change(event: Event[EventStateChangedData]) -> None:
        for room_id in calendar_to_rooms.get(event.data["entity_id"], []):
            picpak.async_room_changed(room_id, guests_changed=False)

    if calendar_to_rooms:
        entry.async_on_unload(
            async_track_state_change_event(hass, list(calendar_to_rooms), _on_calendar_change)
        )

    # --- Bouton de réinitialisation de la maison (fin des séjours) --------- #
    reset_button = str(
        opts.get(CONF_PICPAK_RESET_BUTTON, DEFAULT_PICPAK_RESET_BUTTON) or ""
    ).strip()

    @callback
    def _on_reset_pressed(event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if old is None or new is None or old.state == new.state:
            return
        if new.state in ("unknown", "unavailable"):
            return
        picpak.async_reset_house()

    if reset_button:
        entry.async_on_unload(
            async_track_state_change_event(hass, [reset_button], _on_reset_pressed)
        )

    # --- Une fois HA démarré : helpers manquants, calendriers chargés ------ #
    async def _on_started(_hass: HomeAssistant) -> None:
        for room_id, room in runtime.rooms.items():
            room.engine.refresh_problems()
            picpak.async_room_changed(room_id, guests_changed=False)

    entry.async_on_unload(async_at_started(hass, _on_started))

    _async_register_services(hass, runtime)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    picpak.async_start()
    return True


def _async_register_services(hass: HomeAssistant, runtime: ImmichRuntimeData) -> None:
    """Services de l'intégration."""
    api = runtime.api
    links = runtime.links
    picpak = runtime.picpak

    def _rooms(call: ServiceCall) -> list[RoomRuntime]:
        """Chambres visées : ``room`` (identifiant) ou ``entity_id`` (l'image
        du diaporama de la chambre, même renommée) ; sinon toutes."""
        room = call.data.get(ATTR_ROOM)
        entity_ids = call.data.get("entity_id") or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if entity_ids:
            registry = er.async_get(hass)
            by_unique = {
                f"{runtime.entry_id}_{room_id}_image": room_id for room_id in runtime.rooms
            }
            found: list[RoomRuntime] = []
            for entity_id in entity_ids:
                ent = registry.async_get(entity_id)
                room_id = by_unique.get(ent.unique_id) if ent else None
                if room_id is None:
                    raise ServiceValidationError(
                        f"{entity_id} n'est pas le diaporama d'une chambre"
                    )
                found.append(runtime.rooms[room_id])
            return found
        if room and room not in runtime.rooms:
            raise ServiceValidationError(
                f"Chambre inconnue : {room} (chambres : {', '.join(runtime.rooms)})"
            )
        return [runtime.rooms[room]] if room else list(runtime.rooms.values())

    async def _handle_refresh(call: ServiceCall) -> None:
        for room in _rooms(call):
            await room.coordinator.async_rebuild_and_refresh()
            picpak.async_room_changed(room.config.room_id, guests_changed=False)

    async def _handle_next(call: ServiceCall) -> None:
        for room in _rooms(call):
            await room.coordinator.async_step(1)

    async def _handle_previous(call: ServiceCall) -> None:
        for room in _rooms(call):
            await room.coordinator.async_step(-1)

    async def _handle_link_person(call: ServiceCall) -> None:
        name = normalize_name(call.data[ATTR_NAME])
        person_id = extract_person_uuid(call.data[ATTR_PERSON_ID])
        if not name:
            raise ServiceValidationError("Le nom ne peut pas être vide")
        if person_id is None:
            raise ServiceValidationError(
                "ID de personne invalide : collez l'UUID ou l'URL de la fiche "
                "de la personne dans Immich"
            )
        try:
            person = await api.async_get_person(person_id)
        except ImmichApiError as err:
            raise HomeAssistantError(f"Immich injoignable: {err}") from err
        if person is None:
            raise ServiceValidationError(f"Aucune personne Immich avec l'ID {person_id}")
        await links.async_set(name, person_id)
        await runtime.async_rebuild_all()

    async def _handle_unlink_person(call: ServiceCall) -> ServiceResponse:
        removed = await links.async_remove(call.data.get(ATTR_NAME))
        if removed:
            await runtime.async_rebuild_all()
        return {"removed": removed}

    async def _handle_picpak_urls(call: ServiceCall) -> ServiceResponse:
        return {"frames": picpak.urls(call.data.get(ATTR_BASE_URL))}

    async def _handle_picpak_next(call: ServiceCall) -> ServiceResponse:
        rooms = _rooms(call)
        room_id = rooms[0].config.room_id if call.data.get(ATTR_ROOM) else None
        slot = call.data.get(ATTR_BED)
        return {"frames": picpak.request_next(room_id, str(slot) if slot else None)}

    room_schema = vol.Schema(
        {vol.Optional(ATTR_ROOM): cv.string, vol.Optional("entity_id"): cv.entity_ids}
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=room_schema)
    hass.services.async_register(DOMAIN, SERVICE_NEXT, _handle_next, schema=room_schema)
    hass.services.async_register(
        DOMAIN, SERVICE_PREVIOUS, _handle_previous, schema=room_schema
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LINK_PERSON,
        _handle_link_person,
        schema=vol.Schema(
            {vol.Required(ATTR_NAME): cv.string, vol.Required(ATTR_PERSON_ID): cv.string}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNLINK_PERSON,
        _handle_unlink_person,
        schema=vol.Schema({vol.Optional(ATTR_NAME): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PICPAK_URLS,
        _handle_picpak_urls,
        schema=vol.Schema({vol.Optional(ATTR_BASE_URL): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PICPAK_NEXT,
        _handle_picpak_next,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_ROOM): cv.string,
                vol.Optional(ATTR_BED): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _async_cleanup_stale_devices(
    hass: HomeAssistant, entry: ImmichConfigEntry, runtime: ImmichRuntimeData
) -> None:
    """Retire les appareils des chambres et cadres qui n'existent plus."""
    registry = dr.async_get(hass)
    valid = {(DOMAIN, f"{entry.entry_id}_{room_id}") for room_id in runtime.rooms}
    for key in runtime.picpak.frames:
        room_id, slot = key.rsplit("/", 1)
        valid.add((DOMAIN, f"{entry.entry_id}_{room_id}_picpak_{slot}"))
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not (device.identifiers & valid):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recharge l'entrée quand les options ou les chambres changent."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ImmichConfigEntry) -> bool:
    """Décharge la config entry."""
    for service in (
        SERVICE_REFRESH,
        SERVICE_NEXT,
        SERVICE_PREVIOUS,
        SERVICE_LINK_PERSON,
        SERVICE_UNLINK_PERSON,
        SERVICE_PICPAK_URLS,
        SERVICE_PICPAK_NEXT,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.picpak.async_unload()
        entry.runtime_data.reporter.async_clear_all()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


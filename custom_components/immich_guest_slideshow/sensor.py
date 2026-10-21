"""Capteurs : recherche Immich courante par chambre, état des cadres PicPak."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ImmichConfigEntry
from .const import SIGNAL_PICPAK_UPDATE
from .coordinator import RoomCoordinator, RoomData
from .image import room_device_info
from .picpak import DOOR, LOGO, FrameState, PicPakManager, frame_device_info, frame_key


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Par chambre : recherche courante ; par cadre : batterie et connexion."""
    runtime = entry.runtime_data
    for room_id, room in runtime.rooms.items():
        entities: list[SensorEntity] = [ImmichCurrentSearchSensor(entry, room.coordinator)]
        for slot in room.config.slots:
            key = frame_key(room_id, slot)
            entities.append(PicPakBatterySensor(entry, runtime.picpak, key))
            entities.append(PicPakLastSeenSensor(entry, runtime.picpak, key))
        async_add_entities(entities, config_subentry_id=room.config.subentry_id)


class ImmichCurrentSearchSensor(CoordinatorEntity[RoomCoordinator], SensorEntity):
    """Combinaison de recherche de la photo affichée (ex. « Alice + Julien »)."""

    _attr_has_entity_name = True
    _attr_translation_key = "current_search"
    _attr_icon = "mdi:image-search"

    def __init__(self, entry: ImmichConfigEntry, coordinator: RoomCoordinator) -> None:
        super().__init__(coordinator)
        engine = coordinator.engine
        room_id = engine.room_id
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_current_search"
        self.entity_id = f"sensor.immich_{room_id}_current_search"
        self._attr_device_info = room_device_info(entry.entry_id, room_id, engine.room_name)

    @property
    def available(self) -> bool:
        data: RoomData | None = self.coordinator.data
        return super().available and data is not None and data.active

    @property
    def native_value(self) -> str | None:
        data: RoomData | None = self.coordinator.data
        return data.search_label if data else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        engine = self.coordinator.engine
        data: RoomData | None = self.coordinator.data
        return {
            "guests": engine.get_guests(),
            "photo_count": data.photo_count if data else 0,
            "searches": [combo.label for combo in engine.combos],
        }


class _PicPakSensor(SensorEntity):
    """Base des capteurs d'un cadre PicPak, mis à jour à chaque réveil."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _suffix = ""

    def __init__(self, entry: ImmichConfigEntry, manager: PicPakManager, key: str) -> None:
        self._manager = manager
        self._key = key
        self._entry_id = entry.entry_id
        room_id, slot = key.rsplit("/", 1)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_picpak_{slot}_{self._suffix}"
        self.entity_id = f"sensor.picpak_{room_id}_{slot}_{self._suffix}"
        self._attr_device_info = frame_device_info(entry.entry_id, manager, key)

    @property
    def _frame(self) -> FrameState:
        return self._manager.frames[self._key]

    @property
    def available(self) -> bool:
        """Disponible dès que le cadre s'est connecté au moins une fois."""
        return self._frame.last_seen is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PICPAK_UPDATE.format(self._entry_id, self._key),
                self._on_update,
            )
        )

    @callback
    def _on_update(self) -> None:
        self.async_write_ha_state()


class PicPakBatterySensor(_PicPakSensor):
    """Batterie du cadre, mesurée à son réveil."""

    _attr_translation_key = "picpak_battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _suffix = "batterie"

    @property
    def native_value(self) -> int | None:
        return self._frame.battery_pct

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        frame = self._frame
        count = len(frame.assets)
        photo = frame.shown not in (None, LOGO, DOOR)
        return {
            "tension_mv": frame.battery_mv,
            "invite": frame.guest_name,
            "etat": frame.state,
            "affichage": (
                "visages" if frame.shown == DOOR else "photo" if photo else "logo"
            ),
            "photo": (frame.index + 1) if photo and frame.index >= 0 else None,
            "photos": count,
            "asset_id": frame.shown if photo else None,
            "cause_reveil": frame.wake,
            "prochain_reveil_s": frame.next_wake_s,
            "reveils": frame.requests,
        }


class PicPakLastSeenSensor(_PicPakSensor):
    """Dernière connexion du cadre : sert à repérer un cadre muet."""

    _attr_translation_key = "picpak_last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _suffix = "derniere_connexion"

    @property
    def native_value(self) -> datetime | None:
        seen = self._frame.last_seen
        return dt_util.parse_datetime(seen) if seen else None

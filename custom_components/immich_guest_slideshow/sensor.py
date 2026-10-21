"""Sensors : recherche Immich courante par chambre, état des cadres PicPak."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ImmichConfigEntry
from .const import DOMAIN, MANUFACTURER, SIGNAL_PICPAK_UPDATE
from .coordinator import RoomCoordinator, RoomData
from .picpak import PicPakManager, frame_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ajoute un sensor 'current search' par chambre, et deux par cadre."""
    entities: list[SensorEntity] = [
        ImmichCurrentSearchSensor(entry, coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    ]
    manager = entry.runtime_data.picpak
    if manager is not None:
        for key in manager.frames:
            entities.append(PicPakBatterySensor(entry, manager, key))
            entities.append(PicPakLastSeenSensor(entry, manager, key))
    async_add_entities(entities)


class ImmichCurrentSearchSensor(CoordinatorEntity[RoomCoordinator], SensorEntity):
    """Expose la combinaison de recherche de la photo affichée."""

    _attr_has_entity_name = True
    _attr_translation_key = "current_search"
    _attr_icon = "mdi:image-search"

    def __init__(
        self, entry: ImmichConfigEntry, coordinator: RoomCoordinator
    ) -> None:
        """Initialise le sensor de la chambre."""
        super().__init__(coordinator)
        room_id = coordinator.engine.room_id
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_current_search"
        # Force l'entity_id attendu : sensor.immich_chambre_xxx_current_search
        self.entity_id = f"sensor.immich_{room_id}_current_search"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{room_id}")},
            name=f"Immich {coordinator.engine.room_name}",
            manufacturer=MANUFACTURER,
            model="Guest Slideshow",
        )

    @property
    def available(self) -> bool:
        """Indisponible si aucun invité renseigné."""
        data: RoomData | None = self.coordinator.data
        return super().available and data is not None and data.active

    @property
    def native_value(self) -> str | None:
        """Libellé de la recherche courante (ex. 'Alice + Propriétaire 1')."""
        data: RoomData | None = self.coordinator.data
        return data.search_label if data else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Détails : invités détectés, nb de photos, recherches actives."""
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

    def __init__(
        self, entry: ImmichConfigEntry, manager: PicPakManager, key: str
    ) -> None:
        self._manager = manager
        self._key = key
        self._entry_id = entry.entry_id
        room_id, bed = key.rsplit("/", 1)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_picpak_{bed}_{self._suffix}"
        # entity_id attendu : sensor.picpak_chambre_xxx_1_<suffixe>
        self.entity_id = f"sensor.picpak_{room_id}_{bed}_{self._suffix}"
        self._attr_device_info = frame_device_info(entry.entry_id, manager, key)

    @property
    def _frame(self):
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
    """Batterie du cadre, mesurée à son réveil (avant la charge Wi-Fi/écran)."""

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
        return {
            "tension_mv": frame.battery_mv,
            "invite": frame.guest_name,
            "photo": (frame.index + 1) if count and frame.index >= 0 else None,
            "photos": count,
            "asset_id": (
                frame.shown if frame.shown not in ("__logo__", "__visages__") else None
            ),
            "affichage": (
                "visages" if frame.shown == "__visages__"
                else "logo" if not count
                else "logo (hors séjour)" if frame.in_stay is False
                else "photos"
            ),
            "sejour_en_cours": frame.in_stay,
            "cause_reveil": frame.wake,
            "prochain_reveil_s": frame.next_wake_s,
            "reveils": frame.requests,
        }


class PicPakLastSeenSensor(_PicPakSensor):
    """Dernière connexion du cadre : sert à détecter un cadre muet."""

    _attr_translation_key = "picpak_last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _suffix = "derniere_connexion"

    @property
    def native_value(self) -> datetime | None:
        seen = self._frame.last_seen
        return dt_util.parse_datetime(seen) if seen else None

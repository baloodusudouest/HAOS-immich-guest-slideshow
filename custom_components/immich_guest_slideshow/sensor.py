"""Sensors : recherche Immich courante par chambre."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ImmichConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import RoomCoordinator, RoomData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ajoute un sensor 'current search' par chambre."""
    async_add_entities(
        ImmichCurrentSearchSensor(entry, coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


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

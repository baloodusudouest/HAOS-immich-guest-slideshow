"""Entités image : une par chambre (image.immich_<chambre>)."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ImmichConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import RoomCoordinator, RoomData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ajoute une entité image par chambre."""
    async_add_entities(
        ImmichRoomImage(hass, entry, coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


class ImmichRoomImage(CoordinatorEntity[RoomCoordinator], ImageEntity):
    """Image du diaporama d'une chambre."""

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ImmichConfigEntry,
        coordinator: RoomCoordinator,
    ) -> None:
        """Initialise l'entité image de la chambre."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        room_id = coordinator.engine.room_id
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_image"
        # Force l'entity_id attendu : image.immich_chambre_xxx
        self.entity_id = f"image.immich_{room_id}"
        self._attr_name = None
        self._last_asset_id: str | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{room_id}")},
            name=f"Immich {coordinator.engine.room_name}",
            manufacturer=MANUFACTURER,
            model="Guest Slideshow",
        )

    @property
    def available(self) -> bool:
        """Indisponible si aucun invité (aucune photo à afficher)."""
        data: RoomData | None = self.coordinator.data
        return super().available and data is not None and data.active

    def _handle_coordinator_update(self) -> None:
        """Met à jour l'horodatage d'image quand l'asset change."""
        data: RoomData | None = self.coordinator.data
        asset_id = data.asset_id if data else None
        if asset_id != self._last_asset_id:
            self._last_asset_id = asset_id
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Retourne les octets de la photo courante (None si aucun invité)."""
        data: RoomData | None = self.coordinator.data
        if data is None or not data.active:
            return None
        return await self.coordinator.engine.async_get_image_bytes()

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Attributs de diagnostic utiles pour les cartes Lovelace."""
        data: RoomData | None = self.coordinator.data
        return {
            "asset_id": data.asset_id if data else None,
            "search": data.search_label if data else None,
            "photo_count": data.photo_count if data else 0,
        }

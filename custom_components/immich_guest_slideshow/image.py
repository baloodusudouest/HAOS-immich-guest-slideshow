"""Entités image : une par chambre (image.immich_<chambre>), et une par
cadre PicPak (image.picpak_<chambre>_<lit>) montrant ce qu'affiche la dalle."""
from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ImmichConfigEntry
from .const import DOMAIN, MANUFACTURER, SIGNAL_PICPAK_UPDATE
from .coordinator import RoomCoordinator, RoomData
from .picpak import LOGO, PicPakManager, frame_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ajoute une entité image par chambre et une par cadre PicPak."""
    entities: list[ImageEntity] = [
        ImmichRoomImage(hass, entry, coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    ]
    manager = entry.runtime_data.picpak
    if manager is not None:
        entities.extend(
            PicPakFrameImage(hass, entry, manager, key) for key in manager.frames
        )
    async_add_entities(entities)


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
            # À utiliser en CSS (object-fit: cover) pour ne jamais couper
            # le visage des invités : voir framing.py.
            "object_position": data.object_position if data else None,
        }


class PicPakFrameImage(ImageEntity):
    """Aperçu de la dalle d'un cadre, avec ses vraies couleurs."""

    _attr_has_entity_name = True
    _attr_translation_key = "picpak_screen"
    _attr_content_type = "image/png"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ImmichConfigEntry,
        manager: PicPakManager,
        key: str,
    ) -> None:
        ImageEntity.__init__(self, hass)
        self._manager = manager
        self._key = key
        self._entry_id = entry.entry_id
        room_id, bed = key.rsplit("/", 1)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_picpak_{bed}_image"
        self.entity_id = f"image.picpak_{room_id}_{bed}"
        self._attr_device_info = frame_device_info(entry.entry_id, manager, key)
        self._rev = manager.body_revision(key)
        self._attr_image_last_updated = dt_util.utcnow()

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
        rev = self._manager.body_revision(self._key)
        if rev != self._rev:
            self._rev = rev
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """PNG du dernier tampon envoyé au cadre."""
        return await self._manager.async_preview(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        frame = self._manager.frames[self._key]
        return {
            "invite": frame.guest_name,
            "asset_id": frame.shown if frame.shown != LOGO else None,
            "affichage": "logo" if not frame.assets else "photos",
        }

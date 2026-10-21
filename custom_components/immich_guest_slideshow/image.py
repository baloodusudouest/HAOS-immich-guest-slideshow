"""Entités image : diaporama de chaque chambre (``image.immich_<chambre>``) et
aperçu de chaque cadre PicPak (``image.picpak_<chambre>_<emplacement>``)."""
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
from .picpak import DOOR, LOGO, PicPakManager, frame_device_info, frame_key


def room_device_info(entry_id: str, room_id: str, room_name: str) -> DeviceInfo:
    """Appareil d'une chambre (porte le diaporama ; parent de ses cadres)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{room_id}")},
        name=f"Immich {room_name}",
        manufacturer=MANUFACTURER,
        model="Guest Slideshow",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Par chambre : son diaporama et l'aperçu de chacun de ses cadres."""
    runtime = entry.runtime_data
    for room_id, room in runtime.rooms.items():
        entities: list[ImageEntity] = [ImmichRoomImage(hass, entry, room.coordinator)]
        entities.extend(
            PicPakFrameImage(hass, entry, runtime.picpak, frame_key(room_id, slot))
            for slot in room.config.slots
        )
        async_add_entities(entities, config_subentry_id=room.config.subentry_id)


class ImmichRoomImage(CoordinatorEntity[RoomCoordinator], ImageEntity):
    """Photo courante du diaporama de la chambre (dashboards)."""

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(
        self, hass: HomeAssistant, entry: ImmichConfigEntry, coordinator: RoomCoordinator
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        engine = coordinator.engine
        room_id = engine.room_id
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_image"
        self.entity_id = f"image.immich_{room_id}"
        self._attr_name = None
        self._last_asset_id: str | None = None
        self._attr_device_info = room_device_info(entry.entry_id, room_id, engine.room_name)

    @property
    def available(self) -> bool:
        """Indisponible tant qu'aucun invité n'est renseigné."""
        data: RoomData | None = self.coordinator.data
        return super().available and data is not None and data.active

    @callback
    def _handle_coordinator_update(self) -> None:
        data: RoomData | None = self.coordinator.data
        asset_id = data.asset_id if data else None
        if asset_id != self._last_asset_id:
            self._last_asset_id = asset_id
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        data: RoomData | None = self.coordinator.data
        if data is None or not data.active:
            return None
        return await self.coordinator.engine.async_get_image_bytes()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Pour les cartes : cadrage visages et séjour prévu."""
        data: RoomData | None = self.coordinator.data
        stay = data.stay if data else None
        days = stay.days if stay else None
        return {
            "chambre": self.coordinator.engine.room_id,
            "asset_id": data.asset_id if data else None,
            "search": data.search_label if data else None,
            "photo_count": data.photo_count if data else 0,
            "object_position": data.object_position if data else None,
            "invites": self.coordinator.engine.get_guests(),
            "sejour": data.stay_state if data else None,
            "sejour_dates": stay.label if stay else "",
            "sejour_debut": days[0].isoformat() if days else None,
            "sejour_fin": days[1].isoformat() if days else None,
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
        room_id, slot = key.rsplit("/", 1)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_picpak_{slot}_image"
        self.entity_id = f"image.picpak_{room_id}_{slot}"
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
        """PNG de ce qu'affiche (ou affichera) le cadre."""
        return await self._manager.async_preview(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        frame = self._manager.frames[self._key]
        return {
            "invite": frame.guest_name,
            "asset_id": frame.shown if frame.shown not in (LOGO, DOOR) else None,
            "etat": frame.state,
        }

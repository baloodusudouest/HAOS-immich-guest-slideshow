"""Entités select : affichage de chaque cadre PicPak pendant le séjour.

Une entité par cadre, ``select.picpak_<chambre>_<lit>_affichage`` :

- ``photos`` : les photos de l'invité du lit, une par intervalle ;
- ``visages`` : la carte médaillon des invités de la chambre (visages, nom de
  la chambre, noms et dates du séjour).

Le choix est mémorisé avec l'état des cadres (il survit aux redémarrages).
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ImmichConfigEntry
from .const import PICPAK_MODES, SIGNAL_PICPAK_UPDATE
from .picpak import PicPakManager, frame_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImmichConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ajoute une entité « Affichage » par cadre PicPak."""
    manager = entry.runtime_data.picpak
    if manager is None:
        return
    async_add_entities(
        PicPakModeSelect(entry, manager, key) for key in manager.frames
    )


class PicPakModeSelect(SelectEntity):
    """Affichage du cadre pendant le séjour : photos ou visages."""

    _attr_has_entity_name = True
    _attr_translation_key = "picpak_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(PICPAK_MODES)
    _attr_should_poll = False

    def __init__(
        self, entry: ImmichConfigEntry, manager: PicPakManager, key: str
    ) -> None:
        self._manager = manager
        self._key = key
        self._entry_id = entry.entry_id
        room_id, bed = key.rsplit("/", 1)
        self._attr_unique_id = f"{entry.entry_id}_{room_id}_picpak_{bed}_mode"
        self.entity_id = f"select.picpak_{room_id}_{bed}_affichage"
        self._attr_device_info = frame_device_info(entry.entry_id, manager, key)

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

    @property
    def current_option(self) -> str:
        return self._manager.frame_mode(self._key)

    async def async_select_option(self, option: str) -> None:
        self._manager.async_set_frame_mode(self._key, option)
        self.async_write_ha_state()

"""Coordinateur du diaporama d'une chambre (dashboards invité et gestionnaire)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .slideshow import SlideshowEngine
from .stay import FREE, StayStatus, StayTracker

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomData:
    """Données exposées à chaque tick (une photo toutes les 15 s par défaut)."""

    asset_id: str | None
    search_label: str | None
    photo_count: int
    active: bool
    # ``object-position`` CSS gardant visibles les visages des invités.
    object_position: str | None = None
    stay: StayStatus | None = None

    @property
    def stay_state(self) -> str:
        """« en_cours », « programme » ou « libre »."""
        return self.stay.state if self.stay is not None else FREE


class RoomCoordinator(DataUpdateCoordinator[RoomData]):
    """Fait avancer le diaporama d'une chambre depuis son cache local.

    À chaque tick, la photo suivante est prise dans le cache (aucun appel
    Immich, sauf le cadrage des visages, mémorisé, et un rebuild périodique).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        engine: SlideshowEngine,
        stay: StayTracker,
        interval_seconds: int,
        rebuild_hours: int = 0,
    ) -> None:
        self.engine = engine
        self.stay = stay
        self._rebuild_interval = (
            timedelta(hours=rebuild_hours) if rebuild_hours > 0 else None
        )
        self._last_rebuild = dt_util.utcnow()
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{engine.room_id}",
            update_interval=timedelta(seconds=interval_seconds),
        )

    async def _async_update_data(self) -> RoomData:
        if (
            self._rebuild_interval is not None
            and dt_util.utcnow() - self._last_rebuild >= self._rebuild_interval
        ):
            _LOGGER.debug("[%s] Rebuild périodique", self.engine.room_id)
            await self.engine.async_rebuild()
            self._last_rebuild = dt_util.utcnow()
        return await self._async_data(self.engine.next_photo())

    async def _async_data(self, photo) -> RoomData:
        stay = await self.stay.async_status()
        if photo is None:
            return RoomData(None, None, 0, False, stay=stay)
        return RoomData(
            asset_id=photo.asset_id,
            search_label=photo.search_label,
            photo_count=self.engine.photo_count,
            active=True,
            object_position=await self.engine.async_object_position(photo.asset_id),
            stay=stay,
        )

    async def async_step(self, delta: int) -> None:
        """Photo suivante (``+1``) ou précédente (``-1``) tout de suite ; le
        minuteur de 15 s repart de zéro."""
        photo = self.engine.next_photo() if delta > 0 else self.engine.previous_photo()
        self.async_set_updated_data(await self._async_data(photo))

    async def async_rebuild_and_refresh(self) -> None:
        """Reconstruit la sélection puis affiche une nouvelle photo."""
        self.stay.invalidate()
        await self.engine.async_rebuild()
        self._last_rebuild = dt_util.utcnow()
        await self.async_request_refresh()

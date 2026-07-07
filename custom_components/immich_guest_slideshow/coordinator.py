"""DataUpdateCoordinator pilotant la rotation d'images d'une chambre."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .slideshow import SlideshowEngine

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomData:
    """Données exposées par le coordinateur à chaque tick."""

    asset_id: str | None
    search_label: str | None
    photo_count: int
    active: bool


class RoomCoordinator(DataUpdateCoordinator[RoomData]):
    """Un coordinateur indépendant par chambre.

    Le ``update_interval`` correspond à la durée d'affichage d'une image :
    à chaque tick, on pioche la photo suivante dans le cache local
    (aucun appel Immich, sauf lors d'un rebuild).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        engine: SlideshowEngine,
        interval_seconds: int,
        rebuild_hours: int = 0,
    ) -> None:
        """Initialise le coordinateur de la chambre.

        Args:
            rebuild_hours: si > 0, le cache est reconstruit automatiquement
                toutes les ``rebuild_hours`` heures pour intégrer les
                nouvelles photos ajoutées dans Immich.
        """
        self.engine = engine
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
        """Avance le diaporama d'une image (depuis le cache uniquement).

        Un rebuild complet (appels Immich) n'est déclenché que si le délai
        de rafraîchissement périodique est écoulé.
        """
        if (
            self._rebuild_interval is not None
            and dt_util.utcnow() - self._last_rebuild >= self._rebuild_interval
        ):
            _LOGGER.debug("[%s] Rebuild périodique du cache", self.engine.room_id)
            await self.engine.async_rebuild()
            self._last_rebuild = dt_util.utcnow()
        photo = self.engine.next_photo()
        if photo is None:
            return RoomData(
                asset_id=None,
                search_label=None,
                photo_count=0,
                active=False,
            )
        return RoomData(
            asset_id=photo.asset_id,
            search_label=photo.search_label,
            photo_count=self.engine.photo_count,
            active=True,
        )

    async def async_rebuild_and_refresh(self) -> None:
        """Reconstruit les recherches/cache puis force un rafraîchissement."""
        await self.engine.async_rebuild()
        self._last_rebuild = dt_util.utcnow()
        await self.async_request_refresh()

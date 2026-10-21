"""Configuration d'une chambre (une sous-entrée = une ligne par chambre)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import (
    DOOR_SLOT,
    MAX_GUESTS,
    MAX_ROOM_FRAMES,
    ROOM_CALENDAR,
    ROOM_DOOR_FRAME,
    ROOM_FRAMES,
    ROOM_HELPERS,
    ROOM_ID,
    ROOM_NAME,
)

if TYPE_CHECKING:
    from .coordinator import RoomCoordinator
    from .slideshow import SlideshowEngine
    from .stay import StayTracker


@dataclass(frozen=True)
class RoomConfig:
    """Réglages d'une chambre."""

    room_id: str
    name: str
    helpers: tuple[str, ...]
    calendar: str | None = None
    door_frame: bool = False
    room_frames: int = 0
    subentry_id: str | None = None

    @classmethod
    def from_data(
        cls, data: Mapping[str, Any], subentry_id: str | None = None
    ) -> RoomConfig:
        """Depuis les données d'une sous-entrée « room »."""
        frames = int(data.get(ROOM_FRAMES, 0) or 0)
        return cls(
            room_id=str(data[ROOM_ID]),
            name=str(data.get(ROOM_NAME) or data[ROOM_ID]),
            helpers=tuple(data.get(ROOM_HELPERS) or ())[:MAX_GUESTS],
            calendar=str(data.get(ROOM_CALENDAR) or "") or None,
            door_frame=bool(data.get(ROOM_DOOR_FRAME, False)),
            room_frames=max(0, min(MAX_ROOM_FRAMES, frames)),
            subentry_id=subentry_id,
        )

    @property
    def slots(self) -> list[str]:
        """Emplacements des cadres : « 1 », « 2 » (chambre) puis « porte »."""
        slots = [str(i) for i in range(1, self.room_frames + 1)]
        if self.door_frame:
            slots.append(DOOR_SLOT)
        return slots


@dataclass
class RoomRuntime:
    """Une chambre en fonctionnement : réglages, moteur, séjour, diaporama."""

    config: RoomConfig
    engine: SlideshowEngine
    stay: StayTracker
    coordinator: RoomCoordinator | None = None

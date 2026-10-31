"""Diagnostics de l'intégration (clé API et jeton des cadres expurgés)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ImmichConfigEntry
from .const import CONF_API_KEY, CONF_CARNET_TOKEN

TO_REDACT = {CONF_API_KEY, CONF_CARNET_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ImmichConfigEntry
) -> dict[str, Any]:
    """Retourne les diagnostics de la config entry."""
    runtime = entry.runtime_data
    rooms: dict[str, Any] = {}
    for room_id, room in runtime.rooms.items():
        engine = room.engine
        data = room.coordinator.data if room.coordinator else None
        stay = room.stay.cached
        rooms[room_id] = {
            "config": {
                "name": room.config.name,
                "calendar": room.config.calendar,
                "helpers": list(room.config.helpers),
                "door_frame": room.config.door_frame,
                "room_frames": room.config.room_frames,
            },
            "guests": engine.get_guests(),
            "searches": [combo.label for combo in engine.combos],
            "photo_count": data.photo_count if data else 0,
            "stay": {
                "state": stay.state if stay else None,
                "dates": stay.label if stay else None,
                "names": list(stay.names) if stay else [],
            },
        }
    frames: dict[str, Any] = {}
    for key, frame in runtime.picpak.frames.items():
        info = frame.as_dict()
        info["assets"] = len(frame.assets)
        frames[key] = info
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "rooms": rooms,
        "picpak": frames,
    }

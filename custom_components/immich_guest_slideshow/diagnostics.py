"""Diagnostics de l'intégration (clé API, jeton de Carnet et noms expurgés).

2.5.2 (audit du 17/11/2026) : un fichier de diagnostics part souvent dans un
ticket public ; il ne contient plus aucun nom d'invité ni de propriétaire
(seulement des nombres), en plus de la clé API et du jeton de Carnet."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ImmichConfigEntry
from .const import CONF_API_KEY, CONF_CARNET_TOKEN, CONF_PERMANENT_PERSONS

TO_REDACT = {CONF_API_KEY, CONF_CARNET_TOKEN, CONF_PERMANENT_PERSONS}
# Noms gardés dans l'état des cadres
FRAME_REDACT = {"guest", "guest_name", "signature"}


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
            "guest_count": len(engine.get_guests()),
            "search_count": len(engine.combos),
            "photo_count": data.photo_count if data else 0,
            "stay": {
                "state": stay.state if stay else None,
                "dates": stay.label if stay else None,
                "name_count": len(stay.names) if stay else 0,
            },
        }
    frames: dict[str, Any] = {}
    for key, frame in runtime.picpak.frames.items():
        info = async_redact_data(frame.as_dict(), FRAME_REDACT)
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

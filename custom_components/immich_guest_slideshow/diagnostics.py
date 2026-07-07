"""Diagnostics de l'intégration (clé API expurgée)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ImmichConfigEntry
from .const import CONF_API_KEY

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ImmichConfigEntry
) -> dict[str, Any]:
    """Retourne les diagnostics de la config entry."""
    rooms: dict[str, Any] = {}
    for room_id, coordinator in entry.runtime_data.coordinators.items():
        engine = coordinator.engine
        data = coordinator.data
        rooms[room_id] = {
            "name": engine.room_name,
            "helpers": engine.helpers,
            "guests": engine.get_guests(),
            "searches": [combo.label for combo in engine.combos],
            "photo_count": data.photo_count if data else 0,
            "active": data.active if data else False,
        }
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "rooms": rooms,
    }

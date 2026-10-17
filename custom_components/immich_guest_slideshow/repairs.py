"""Formulaire de correction (Réparations) : lier un nom à un ID Immich."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import ImmichApiError, extract_person_uuid
from .const import ATTR_PERSON_ID, DOMAIN


class LinkPersonRepairFlow(RepairsFlow):
    """Demande l'ID (ou l'URL) de la personne Immich et mémorise la liaison."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialise avec les données de l'élément de Réparations."""
        self._data = data

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Point d'entrée : affiche directement le formulaire."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Formulaire de saisie de l'ID de personne."""
        errors: dict[str, str] = {}
        name = str(self._data["name"])

        if user_input is not None:
            person_id = extract_person_uuid(user_input[ATTR_PERSON_ID])
            runtime = self.hass.data.get(DOMAIN, {}).get(self._data["entry_id"])
            if person_id is None:
                errors["base"] = "invalid_person_id"
            elif runtime is None:
                errors["base"] = "cannot_connect"
            else:
                try:
                    person = await runtime.api.async_get_person(person_id)
                except ImmichApiError:
                    errors["base"] = "cannot_connect"
                else:
                    if person is None:
                        errors["base"] = "person_not_found"
                    else:
                        await runtime.links.async_set(name, person_id)
                        await runtime.async_rebuild_all()
                        return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(ATTR_PERSON_ID): str}),
            errors=errors,
            description_placeholders={
                "name": name,
                "rooms": str(self._data.get("rooms", "")),
                "found": str(self._data.get("found", 0)),
                "minimum": str(self._data.get("minimum", 0)),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Crée le flux de correction d'un élément de Réparations."""
    return LinkPersonRepairFlow(dict(data or {}))

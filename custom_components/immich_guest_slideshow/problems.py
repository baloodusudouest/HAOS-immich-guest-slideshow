"""Signalement des personnes introuvables ou avec trop peu de photos.

Chaque problème devient :
- un élément des **Réparations** de Home Assistant (Paramètres → Système →
  Réparations), dont le formulaire permet de lier le nom à un ID Immich ;
- une **notification persistante** (cloche) qui renvoie vers les Réparations.

Un même nom, présent dans plusieurs chambres, ne produit qu'un seul élément.
Dès que le problème disparaît (liaison faite, photos ajoutées, invité parti),
l'élément et la notification sont retirés.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import slugify

from .const import DOMAIN
from .person_links import (
    KIND_MISSING_HELPER,
    KIND_NOT_FOUND,
    PersonProblem,
    link_key,
)

_LOGGER = logging.getLogger(__name__)

# Clé de traduction de l'élément de Réparations selon le type de problème.
_TRANSLATION_KEYS = {
    "not_found": "person_not_found",
    "few_photos": "person_few_photos",
    KIND_MISSING_HELPER: "helper_missing",
}


@dataclass(frozen=True)
class _Active:
    """Un problème actuellement signalé (pour détecter les changements)."""

    problem: PersonProblem
    rooms: tuple[str, ...]


class ProblemReporter:
    """Agrège les problèmes de toutes les chambres et les signale."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise le rapporteur pour une config entry."""
        self._hass = hass
        self._entry_id = entry_id
        # chambre -> (nom de la chambre, problèmes)
        self._by_room: dict[str, tuple[str, list[PersonProblem]]] = {}
        # identifiant d'élément -> état actuellement signalé
        self._active: dict[str, _Active] = {}

    # ------------------------------------------------------------------ #
    @callback
    def async_update(
        self, room_id: str, room_name: str, problems: list[PersonProblem]
    ) -> None:
        """Reçoit la liste complète des problèmes d'une chambre."""
        self._by_room[room_id] = (room_name, list(problems))
        self._sync()

    @callback
    def async_clear_all(self) -> None:
        """Retire tous les éléments signalés (déchargement de l'intégration)."""
        self._by_room.clear()
        self._sync()

    # ------------------------------------------------------------------ #
    def _merged(self) -> dict[str, _Active]:
        """Fusionne les chambres : un élément par nom (insensible à la casse)."""
        merged: dict[str, tuple[PersonProblem, list[str]]] = {}
        for room_name, problems in self._by_room.values():
            for problem in problems:
                key = link_key(problem.name)
                if key not in merged:
                    merged[key] = (problem, [room_name])
                    continue
                known, rooms = merged[key]
                if room_name not in rooms:
                    rooms.append(room_name)
                # « introuvable » est plus grave que « peu de photos ».
                if problem.kind == KIND_NOT_FOUND and known.kind != KIND_NOT_FOUND:
                    merged[key] = (problem, rooms)
        return {
            self._issue_id(key): _Active(problem, tuple(rooms))
            for key, (problem, rooms) in merged.items()
        }

    def _issue_id(self, key: str) -> str:
        return f"{self._entry_id}_person_{slugify(key) or 'inconnu'}"

    @callback
    def _sync(self) -> None:
        """Crée, met à jour ou supprime les éléments selon l'état courant."""
        wanted = self._merged()

        for issue_id in list(self._active):
            if issue_id not in wanted:
                ir.async_delete_issue(self._hass, DOMAIN, issue_id)
                persistent_notification.async_dismiss(self._hass, issue_id)
                del self._active[issue_id]

        for issue_id, state in wanted.items():
            if self._active.get(issue_id) == state:
                continue
            self._create(issue_id, state)
            self._active[issue_id] = state

    def _create(self, issue_id: str, state: _Active) -> None:
        problem = state.problem
        rooms = ", ".join(state.rooms)
        # Un helper manquant se corrige dans les options de la chambre (ou en
        # créant le helper) : il n'y a pas de formulaire de liaison à proposer.
        fixable = problem.kind != KIND_MISSING_HELPER
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=fixable,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_TRANSLATION_KEYS[problem.kind],
            translation_placeholders={
                "name": problem.name,
                "rooms": rooms,
                "found": str(problem.found),
                "minimum": str(problem.minimum),
            },
            data={
                "entry_id": self._entry_id,
                "name": problem.name,
                "kind": problem.kind,
                "rooms": rooms,
                "found": problem.found,
                "minimum": problem.minimum,
            },
        )
        if problem.kind == KIND_MISSING_HELPER:
            message = (
                f"Le helper « {problem.name} » configuré pour la chambre "
                f"{rooms} n'existe pas : cette chambre ne voit aucun invité. "
                "Créez-le, ou corrigez les helpers de la chambre dans "
                "Paramètres → Appareils et services → Immich Guest Slideshow "
                "→ Configurer (supprimer puis recréer la chambre)."
            )
        elif problem.kind == KIND_NOT_FOUND:
            message = (
                f"La personne « {problem.name} » est introuvable dans Immich, ou "
                f"plusieurs personnes portent ce nom (chambre : {rooms}). "
                "Ouvrez Paramètres → Système → "
                "Réparations pour la lier à son identifiant Immich."
            )
        else:
            message = (
                f"« {problem.name} » n'a que {problem.found} photo(s) dans "
                f"Immich (minimum attendu : {problem.minimum}, chambre : "
                f"{rooms}). Toutes les photos disponibles sont affichées. Si "
                "ce n'est pas la bonne personne, ouvrez Paramètres → Système → "
                "Réparations pour la lier à son identifiant Immich."
            )
        persistent_notification.async_create(
            self._hass,
            message,
            title="Immich Guest Slideshow",
            notification_id=issue_id,
        )
        _LOGGER.info("Signalement: %s (%s)", problem.name, problem.kind)

"""Mémoire des liaisons « nom saisi → ID de personne Immich ».

Quand un nom ne correspond à personne dans Immich (ou à la mauvaise
personne), l'utilisateur le lie une fois pour toutes à l'identifiant de la
bonne personne, via les Réparations de Home Assistant ou le service
``link_person``. La liaison est conservée dans le stockage de Home Assistant
(survit aux redémarrages) et prime ensuite sur la recherche par nom.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import normalize_name
from .const import DOMAIN

STORAGE_VERSION = 1

KIND_NOT_FOUND = "not_found"
KIND_FEW_PHOTOS = "few_photos"


def link_key(name: str) -> str:
    """Clé de liaison : nom normalisé NFC, sans espaces, insensible à la casse."""
    return normalize_name(name).casefold()


@dataclass(frozen=True)
class PersonProblem:
    """Un problème de résolution détecté pour une personne.

    ``name`` est la saisie telle qu'utilisée par le moteur (celle sur laquelle
    une liaison doit porter).
    """

    name: str
    kind: str  # KIND_NOT_FOUND | KIND_FEW_PHOTOS
    found: int = 0  # nombre de photos trouvées
    minimum: int = 0  # nombre minimum attendu


class _StoreLike(Protocol):
    """Sous-ensemble de ``homeassistant.helpers.storage.Store`` utilisé ici."""

    async def async_load(self) -> Any: ...

    async def async_save(self, data: Any) -> None: ...


class PersonLinks:
    """Liaisons nom -> ID de personne Immich, persistées."""

    def __init__(self, store: _StoreLike) -> None:
        """Initialise avec un stockage (injectable pour les tests)."""
        self._store = store
        self._links: dict[str, str] = {}

    @classmethod
    def create(cls, hass: HomeAssistant, entry_id: str) -> "PersonLinks":
        """Crée l'instance adossée au stockage de Home Assistant."""
        return cls(Store(hass, STORAGE_VERSION, f"{DOMAIN}.person_links.{entry_id}"))

    async def async_load(self) -> None:
        """Charge les liaisons enregistrées."""
        data = await self._store.async_load()
        links = (data or {}).get("links", {})
        self._links = {str(k): str(v) for k, v in links.items()}

    def get(self, name: str) -> str | None:
        """ID de personne lié à ``name``, ou ``None``."""
        return self._links.get(link_key(name))

    @property
    def items(self) -> dict[str, str]:
        """Copie des liaisons (clé normalisée -> ID)."""
        return dict(self._links)

    async def async_set(self, name: str, person_id: str) -> None:
        """Lie ``name`` à ``person_id`` (remplace une liaison existante)."""
        self._links[link_key(name)] = person_id
        await self._save()

    async def async_remove(self, name: str | None = None) -> int:
        """Supprime la liaison de ``name`` (ou toutes si ``name`` est vide).

        Returns:
            Le nombre de liaisons supprimées.
        """
        if name:
            removed = 1 if self._links.pop(link_key(name), None) else 0
        else:
            removed = len(self._links)
            self._links.clear()
        if removed:
            await self._save()
        return removed

    async def _save(self) -> None:
        await self._store.async_save({"links": self._links})

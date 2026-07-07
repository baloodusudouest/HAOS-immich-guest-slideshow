"""Moteur de diaporama : une instance indépendante par chambre.

Construit les recherches Immich selon la logique invités/permanents,
remplit le cache local et fournit la photo suivante à afficher.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .api import ImmichApiClient, ImmichApiError
from .cache import CachedPhoto, PhotoCache
from .const import MAX_ASSETS_PER_SEARCH

_LOGGER = logging.getLogger(__name__)

_EMPTY_STATES = {"", "unknown", "unavailable", "none"}


@dataclass(frozen=True)
class SearchCombo:
    """Une combinaison de personnes à rechercher dans Immich."""

    names: tuple[str, ...]

    @property
    def label(self) -> str:
        """Libellé lisible pour les sensors (ex. 'Alice + Propriétaire 1')."""
        return " + ".join(self.names)


def build_search_combos(
    guests: list[str], permanents: list[str]
) -> list[SearchCombo]:
    """Construit les combinaisons de recherche selon la spécification.

    - 0 invité  -> aucune recherche (pas de photo).
    - 1 invité  -> invité×{perm1}, invité×{perm2}, invité×{perm1,perm2}.
    - 2 invités -> les 3 combos de chaque invité seul, puis les 3 combos
      avec les deux invités ensemble (9 recherches au total).
    """
    guests = [g for g in guests if g]
    if not guests or not permanents:
        return []

    # Sous-ensembles non vides de permanents, dans l'ordre :
    # perm1 seul, perm2 seul, ..., puis tous ensemble.
    perm_subsets: list[tuple[str, ...]] = [(p,) for p in permanents]
    if len(permanents) > 1:
        perm_subsets.append(tuple(permanents))

    combos: list[SearchCombo] = []
    # Chaque invité individuellement
    for guest in guests:
        for subset in perm_subsets:
            combos.append(SearchCombo((guest, *subset)))
    # Tous les invités ensemble
    if len(guests) > 1:
        for subset in perm_subsets:
            combos.append(SearchCombo((*guests, *subset)))
    return combos


class SlideshowEngine:
    """Gère le diaporama d'une chambre : recherches, cache, rotation."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ImmichApiClient,
        room_id: str,
        room_name: str,
        helpers: list[str],
        permanents: list[str],
        cache_size: int,
    ) -> None:
        """Initialise le moteur pour une chambre donnée."""
        self._hass = hass
        self._api = api
        self.room_id = room_id
        self.room_name = room_name
        self.helpers = helpers
        self._permanents = permanents
        self._cache = PhotoCache(max_size=cache_size)
        self._person_ids: dict[str, str | None] = {}
        self.current: CachedPhoto | None = None
        self.combos: list[SearchCombo] = []

    # ------------------------------------------------------------------ #
    # Lecture des helpers
    # ------------------------------------------------------------------ #
    def get_guests(self) -> list[str]:
        """Lit les invités depuis les helpers input_text de la chambre."""
        guests: list[str] = []
        for entity_id in self.helpers:
            state = self._hass.states.get(entity_id)
            if state is None:
                continue
            value = state.state.strip()
            if value.casefold() not in _EMPTY_STATES:
                guests.append(value)
        return guests

    # ------------------------------------------------------------------ #
    # Résolution des personnes et remplissage du cache
    # ------------------------------------------------------------------ #
    async def _resolve_person_id(self, name: str) -> str | None:
        """Résout (avec mémo) un nom complet vers un id de personne Immich."""
        if name not in self._person_ids:
            person = await self._api.async_find_person(name)
            self._person_ids[name] = person["id"] if person else None
            if person is None:
                _LOGGER.warning(
                    "[%s] Personne Immich introuvable: %s", self.room_id, name
                )
        return self._person_ids[name]

    async def async_rebuild(self) -> None:
        """Vide le cache, reconstruit les recherches et recharge les photos.

        Appelé au démarrage et à chaque changement d'un helper input_text.
        """
        self._cache.clear()
        self.current = None
        self._person_ids.clear()
        guests = self.get_guests()
        self.combos = build_search_combos(guests, self._permanents)

        if not self.combos:
            _LOGGER.debug("[%s] Aucun invité: diaporama inactif", self.room_id)
            return

        photos: list[CachedPhoto] = []
        guest_hits: dict[str, int] = {guest: 0 for guest in guests}
        for combo in self.combos:
            try:
                ids = [await self._resolve_person_id(n) for n in combo.names]
                if any(pid is None for pid in ids):
                    continue
                assets = await self._api.async_search_assets(
                    [pid for pid in ids if pid],
                    limit=MAX_ASSETS_PER_SEARCH,
                )
            except ImmichApiError as err:
                _LOGGER.warning(
                    "[%s] Recherche '%s' en échec: %s",
                    self.room_id,
                    combo.label,
                    err,
                )
                continue
            for guest in guests:
                if guest in combo.names:
                    guest_hits[guest] += len(assets)
            photos.extend(
                CachedPhoto(asset_id=a["id"], search_label=combo.label)
                for a in assets
            )

        # Repli : un invité sans aucune photo avec les propriétaires
        # bascule sur ses photos individuelles.
        for guest in guests:
            if guest_hits[guest] > 0:
                continue
            fallback = SearchCombo((guest,))
            try:
                pid = await self._resolve_person_id(guest)
                if pid is None:
                    continue
                assets = await self._api.async_search_assets(
                    [pid], limit=MAX_ASSETS_PER_SEARCH
                )
            except ImmichApiError as err:
                _LOGGER.warning(
                    "[%s] Repli '%s' en échec: %s", self.room_id, guest, err
                )
                continue
            if assets:
                _LOGGER.info(
                    "[%s] Aucune photo de '%s' avec les propriétaires: "
                    "repli sur ses %d photo(s) individuelles",
                    self.room_id,
                    guest,
                    len(assets),
                )
                self.combos.append(fallback)
                photos.extend(
                    CachedPhoto(asset_id=a["id"], search_label=fallback.label)
                    for a in assets
                )

        self._cache.replace(photos)
        _LOGGER.debug(
            "[%s] Cache reconstruit: %d photo(s) via %d recherche(s)",
            self.room_id,
            len(self._cache),
            len(self.combos),
        )

    # ------------------------------------------------------------------ #
    # Rotation
    # ------------------------------------------------------------------ #
    def next_photo(self) -> CachedPhoto | None:
        """Passe à la photo suivante (aléatoire, sans doublon immédiat)."""
        self.current = self._cache.next_photo()
        return self.current

    @property
    def photo_count(self) -> int:
        """Nombre de photos actuellement en cache."""
        return len(self._cache)

    async def async_get_image_bytes(self) -> bytes | None:
        """Récupère les octets de l'image courante depuis Immich."""
        if self.current is None:
            return None
        try:
            return await self._api.async_get_thumbnail(self.current.asset_id)
        except ImmichApiError as err:
            _LOGGER.warning("[%s] Téléchargement image échoué: %s", self.room_id, err)
            return None

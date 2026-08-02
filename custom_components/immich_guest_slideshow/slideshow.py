"""Moteur de diaporama : une instance indépendante par chambre.

Construit les recherches Immich selon la logique invités/permanents,
remplit le cache local et fournit la photo suivante à afficher.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .api import ImmichApiClient, ImmichApiError, normalize_name
from .cache import CachedPhoto, PhotoCache
from .const import ALBUM_PREFIX, MAX_ASSETS_PER_SEARCH

_LOGGER = logging.getLogger(__name__)

_EMPTY_STATES = {"", "unknown", "unavailable", "none"}

# Suffixe de réservation ajouté par la carte de réservation dans les helpers,
# ex. « Jérémy Jouet (2026-07-17 → 2026-07-18) ». Seul le nom doit être
# envoyé à Immich : on retire tout groupe entre parenthèses en fin de chaîne.
_RESERVATION_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def extract_guest_name(value: str) -> str:
    """Extrait le nom de l'invité depuis la valeur brute du helper.

    « Jérémy Jouet (2026-07-17 → 2026-07-18) » -> « Jérémy Jouet ».
    Une valeur sans parenthèses est retournée telle quelle.
    """
    return _RESERVATION_SUFFIX.sub("", value).strip()


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
        # Normalisation NFC des permanents dès l'init : les noms saisis
        # dans le Config Flow depuis iOS/macOS peuvent arriver en NFD.
        self._permanents = [normalize_name(p) for p in permanents if p]
        self._cache = PhotoCache(max_size=cache_size)
        self._person_ids: dict[str, str | None] = {}
        self.current: CachedPhoto | None = None
        self.combos: list[SearchCombo] = []

    # ------------------------------------------------------------------ #
    # Lecture des helpers
    # ------------------------------------------------------------------ #
    def get_guests(self) -> list[str]:
        """Lit les invités depuis les helpers input_text de la chambre.

        Deux nettoyages sont appliqués :
        - suppression du suffixe de réservation « (date → date) » ajouté
          par la carte de réservation, pour n'envoyer que le nom à Immich ;
        - normalisation NFC : un « é » saisi sur iPhone/iPad arrive en forme
          décomposée (NFD) et ne correspondrait sinon jamais aux noms
          stockés par Immich.
        """
        guests: list[str] = []
        for entity_id in self.helpers:
            state = self._hass.states.get(entity_id)
            if state is None:
                continue
            value = normalize_name(extract_guest_name(state.state))
            if value.casefold() not in _EMPTY_STATES:
                guests.append(value)
        return guests

    def get_guests_by_bed(self) -> list[str | None]:
        """Invités par lit, dans l'ordre des helpers, ``None`` si vide.

        ``get_guests()`` compacte la liste et perd l'indice : impossible d'en
        déduire quel lit est occupé. Cette variante le conserve, ce dont les
        albums ont besoin pour associer un lit à un cadre photo.
        """
        beds: list[str | None] = []
        for entity_id in self.helpers:
            state = self._hass.states.get(entity_id)
            if state is None:
                beds.append(None)
                continue
            value = normalize_name(extract_guest_name(state.state))
            beds.append(None if value.casefold() in _EMPTY_STATES else value)
        return beds

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
    # Albums Immich
    # ------------------------------------------------------------------ #
    async def _async_assets_for_guest(self, guest: str) -> list[str]:
        """Identifiants d'assets pour UN invité, avec la même cascade que le
        diaporama : combinaisons avec les permanents, puis repli sur ses
        photos individuelles.

        Volontairement séparé de ``async_rebuild``, qui fusionne tous les
        invités d'une chambre dans un cache unique. Les albums, eux, doivent
        rester distincts par lit.
        """
        found: set[str] = set()
        for combo in build_search_combos([guest], self._permanents):
            try:
                ids = [await self._resolve_person_id(n) for n in combo.names]
                if any(pid is None for pid in ids):
                    continue
                assets = await self._api.async_search_assets(
                    [pid for pid in ids if pid], limit=MAX_ASSETS_PER_SEARCH
                )
            except ImmichApiError as err:
                _LOGGER.warning(
                    "[%s] Recherche album '%s' en échec: %s",
                    self.room_id,
                    combo.label,
                    err,
                )
                continue
            found.update(a["id"] for a in assets if a.get("id"))

        if found:
            return sorted(found)

        # Repli : aucune photo avec les permanents (première visite).
        try:
            pid = await self._resolve_person_id(guest)
            if pid is None:
                return []
            assets = await self._api.async_search_assets(
                [pid], limit=MAX_ASSETS_PER_SEARCH
            )
        except ImmichApiError as err:
            _LOGGER.warning("[%s] Repli album '%s': %s", self.room_id, guest, err)
            return []
        return sorted({a["id"] for a in assets if a.get("id")})

    def _album_name(self, index: int, guest: str | None) -> str:
        """« PicPak Chambre d'ami 1 — Alice »."""
        base = f"{ALBUM_PREFIX} {self.room_name} {index + 1}"
        return f"{base} — {guest}" if guest else base

    def _album_base_name(self, index: int) -> str:
        """Préfixe stable, utilisé pour retrouver l'album malgré le renommage."""
        return f"{ALBUM_PREFIX} {self.room_name} {index + 1}"

    async def _async_upsert_album(
        self, index: int, guest: str | None, asset_ids: list[str]
    ) -> dict:
        """Crée ou met à jour l'album du lit ``index`` avec exactement
        ``asset_ids``.

        L'album est retrouvé par son préfixe et non par son nom complet :
        celui-ci porte le nom de l'invité précédent et change à chaque
        réservation. Sans ça, on créerait un album de plus à chaque fois.
        """
        base = self._album_base_name(index)
        wanted_name = self._album_name(index, guest)
        album_id: str | None = None

        try:
            for album in await self._api.async_list_albums():
                if str(album.get("albumName", "")).startswith(base):
                    album_id = album.get("id")
                    break
        except ImmichApiError as err:
            _LOGGER.warning("[%s] Liste des albums en échec: %s", self.room_id, err)
            return {"bed": index + 1, "guest": guest, "ok": False, "photos": 0}

        try:
            if album_id is None:
                album_id = await self._api.async_create_album(
                    wanted_name, asset_ids
                )
            else:
                # Retirer AVANT d'ajouter : l'ordre inverse ferait transiter
                # l'album par un état contenant l'ancien et le nouveau
                # contenu, et un téléchargement lancé à cet instant
                # récupérerait les deux.
                current = await self._api.async_album_asset_ids(album_id)
                obsolete = [a for a in current if a not in set(asset_ids)]
                await self._api.async_remove_album_assets(album_id, obsolete)
                await self._api.async_add_album_assets(
                    album_id, [a for a in asset_ids if a not in set(current)]
                )
                await self._api.async_rename_album(album_id, wanted_name)
        except ImmichApiError as err:
            _LOGGER.error(
                "[%s] Synchronisation de l'album '%s' en échec: %s",
                self.room_id,
                wanted_name,
                err,
            )
            return {"bed": index + 1, "guest": guest, "ok": False, "photos": 0}

        _LOGGER.info(
            "[%s] Album '%s' synchronisé: %d photo(s)",
            self.room_id,
            wanted_name,
            len(asset_ids),
        )
        return {
            "bed": index + 1,
            "guest": guest,
            "album": wanted_name,
            "album_id": album_id,
            "photos": len(asset_ids),
            "ok": True,
        }

    async def async_sync_albums(self, size: int) -> list[dict]:
        """Synchronise un album Immich par lit occupé.

        - Deux invités : chaque lit reçoit les photos de son occupant.
        - Un seul invité, deux lits : les deux albums puisent dans le même
          ensemble, mais sur des tranches DISJOINTES après mélange, pour que
          les deux cadres n'affichent jamais la même photo au même moment.
        - Lit vide : album laissé intact.
        """
        beds = self.get_guests_by_bed()
        occupied = [g for g in beds if g]
        if not occupied:
            _LOGGER.debug("[%s] Aucun invité: albums inchangés", self.room_id)
            return []

        results: list[dict] = []
        distinct = {g for g in occupied}

        if len(distinct) == 1 and len(beds) > 1:
            # Occupant unique : on mélange puis on découpe en tranches
            # disjointes. Un simple décalage d'indice ne suffirait pas — avec
            # un pas d'échantillonnage régulier il retomberait sur les mêmes
            # photos.
            guest = next(iter(distinct))
            pool = await self._async_assets_for_guest(guest)
            random.shuffle(pool)
            # On découpe sur le nombre de LITS, pas sur le nombre de lits
            # occupés : un invité seul dans une chambre à deux cadres doit
            # alimenter les deux, avec des photos différentes. Compter les
            # occupants ne produisait qu'un seul album.
            n_slots = len(beds)
            if len(pool) >= size * n_slots:
                tranches = [
                    pool[i * size:(i + 1) * size] for i in range(n_slots)
                ]
            else:
                step = max(1, len(pool) // max(1, n_slots))
                tranches = [
                    pool[i * step:(i + 1) * step] for i in range(n_slots)
                ]
            for index, tranche in enumerate(tranches):
                results.append(
                    await self._async_upsert_album(index, guest, tranche)
                )
            return results

        for index, guest in enumerate(beds):
            if not guest:
                continue
            pool = await self._async_assets_for_guest(guest)
            random.shuffle(pool)
            results.append(
                await self._async_upsert_album(index, guest, pool[:size])
            )
        return results

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

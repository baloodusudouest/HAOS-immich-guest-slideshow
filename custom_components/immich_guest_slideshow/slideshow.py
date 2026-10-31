"""Moteur de photos d'une chambre : recherches Immich, sélection, diaporama.

Une instance par chambre. Elle lit les invités dans les helpers de la
chambre, résout les personnes Immich, choisit les photos par ordre de
priorité et alimente :

- le **diaporama** des dashboards (jusqu'à 100 photos, une toutes les 15 s) ;
- les **cadres PicPak** de la chambre (une sélection par cadre, sans doublon
  entre les cadres).

v2.3.0 : les albums Immich par lit (service ``sync_albums``) sont supprimés —
les cadres flashés open-PicPak reçoivent leurs images directement.

Ordre de priorité des photos d'un invité (propriétaires = Julien et Yves) :

1. l'invité avec **tous** les propriétaires ;
2. l'invité avec chaque propriétaire, l'un après l'autre ;
3. les invités de la chambre ensemble (s'ils sont deux) ;
4. l'invité seul.

À chaque niveau, les photos de plusieurs invités sont entrelacées.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Protocol

from homeassistant.core import HomeAssistant

from . import framing
from .api import (
    ImmichApiClient,
    ImmichApiError,
    ImmichAuthError,
    extract_person_uuid,
    normalize_name,
)
from .cache import CachedPhoto, PhotoCache
from .const import DEFAULT_MIN_PHOTOS, MAX_ASSETS_PER_SEARCH
from .person_links import (
    KIND_FEW_PHOTOS,
    KIND_MISSING_HELPER,
    KIND_NOT_FOUND,
    PersonLinks,
    PersonProblem,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_STATES = {"", "unknown", "unavailable", "none"}

# Suffixe de réservation ajouté par la carte de réservation dans les helpers,
# ex. « Jérémy Jouet (2026-07-17 → 2026-07-18) ». Seul le nom est envoyé à
# Immich : on retire tout groupe entre parenthèses en fin de chaîne.
_RESERVATION_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


class ProblemSink(Protocol):
    """Destinataire des problèmes détectés (Réparations, notifications)."""

    def async_update(
        self, room_id: str, room_name: str, problems: list[PersonProblem]
    ) -> None:
        """Reçoit la liste complète et à jour des problèmes d'une chambre."""


def extract_guest_name(value: str) -> str:
    """« Jérémy Jouet (2026-07-17 → 2026-07-18) » -> « Jérémy Jouet »."""
    return _RESERVATION_SUFFIX.sub("", value).strip()


def first_name(name: str) -> str:
    """Prénom : premier mot du nom (« Corinne Jacquouton » -> « Corinne »)."""
    parts = (name or "").split()
    return parts[0] if parts else ""


@dataclass(frozen=True)
class SearchCombo:
    """Une combinaison de personnes recherchée dans Immich."""

    names: tuple[str, ...]
    # Noms affichables quand ``names`` contient des identifiants Immich (UUID).
    display: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        """Libellé lisible (ex. « Alice + Julien + Yves »)."""
        return " + ".join(self.display or self.names)


def priority_levels(
    guest: str, guests: list[str], owners: list[str]
) -> list[tuple[str, ...]]:
    """Recherches d'un invité, de la plus prioritaire à la moins prioritaire.

    >>> priority_levels("A", ["A", "B"], ["J", "Y"])
    [('A', 'J', 'Y'), ('A', 'J'), ('A', 'Y'), ('A', 'B'), ('A',)]
    """
    levels: list[tuple[str, ...]] = []
    if len(owners) > 1:
        levels.append((guest, *owners))
    levels.extend((guest, owner) for owner in owners)
    together = [g for g in guests if g]
    if len(together) > 1:
        # Ordre fixe (celui des helpers) : une seule recherche pour la chambre.
        levels.append(tuple(together))
    levels.append((guest,))
    return levels


def interleave(lists: list[list[str]]) -> list[str]:
    """Entrelace plusieurs listes (a1, b1, a2, b2, …), sans doublon."""
    out: list[str] = []
    seen: set[str] = set()
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i] not in seen:
                seen.add(x[i])
                out.append(x[i])
    return out


def deal(
    candidates: list[list[str]], picked: list[list[str]], used: set[str], size: int
) -> None:
    """Distribue un niveau de priorité entre plusieurs cadres, à tour de rôle.

    ``candidates[i]`` : photos possibles pour le cadre ``i`` à ce niveau.
    Chaque tour, chaque cadre encore incomplet prend sa prochaine photo non
    encore attribuée : les cadres se partagent équitablement les photos
    communes, sans jamais afficher la même. ``picked`` et ``used`` sont
    complétés sur place.
    """
    iters = [iter(c) for c in candidates]
    progress = True
    while progress:
        progress = False
        for i, it in enumerate(iters):
            if len(picked[i]) >= size:
                continue
            for asset in it:
                if asset not in used:
                    used.add(asset)
                    picked[i].append(asset)
                    progress = True
                    break


def frames_guests(guests: list[str], frames: int) -> list[list[str]]:
    """Invités montrés par chaque cadre de la chambre.

    - 1 cadre : tous les invités (photos mélangées) ;
    - 2 cadres, 2 invités : un invité par cadre ;
    - 2 cadres, 1 invité : le même invité sur les deux (photos différentes).
    """
    if frames <= 0:
        return []
    if not guests:
        return [[] for _ in range(frames)]
    if frames == 1:
        return [list(guests)]
    return [[guests[i % len(guests)]] for i in range(frames)]


class SlideshowEngine:
    """Photos d'une chambre : recherches, diaporama, sélections des cadres."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ImmichApiClient,
        room_id: str,
        room_name: str,
        helpers: list[str],
        permanents: list[str],
        cache_size: int,
        min_photos: int = DEFAULT_MIN_PHOTOS,
        links: PersonLinks | None = None,
        reporter: ProblemSink | None = None,
    ) -> None:
        """
        Args:
            helpers: ``input_text`` des invités (1 ou 2), dans l'ordre.
            permanents: propriétaires (noms ou ID Immich).
            cache_size: photos du diaporama des dashboards.
            min_photos: en dessous, un problème « peu de photos » est signalé.
            links: liaisons nom -> ID de personne Immich (prioritaires).
            reporter: reçoit les personnes introuvables / avec peu de photos.
        """
        self._hass = hass
        self._api = api
        self.room_id = room_id
        self.room_name = room_name
        self.helpers = list(helpers)
        # NFC dès l'init : des noms saisis sur iOS/macOS arrivent en NFD.
        self._permanents = [normalize_name(p) for p in permanents if p]
        self._cache = PhotoCache(max_size=cache_size)
        self._min_photos = max(1, int(min_photos))
        self._links = links
        self._reporter = reporter
        self.problems: list[PersonProblem] = []
        self._person_problems: list[PersonProblem] = []
        self._person_ids: dict[str, str | None] = {}
        self._person_names: dict[str, str] = {}
        self.current: CachedPhoto | None = None
        self.combos: list[SearchCombo] = []
        self._searches: dict[tuple[str, ...], list[str]] = {}
        self._labels: dict[str, str] = {}
        # Diaporama et cadres sélectionnent sur le même moteur, parfois
        # en même temps (changement d'invité) : une sélection à la fois.
        self._select_lock = asyncio.Lock()
        # 2.3.1 : vrai si une recherche Immich a échoué depuis le dernier
        # _reset_searches() — la sélection est alors incomplète et ne doit
        # pas être tenue pour définitive (nouvel essai plus tard).
        self.incomplete = False
        # Cadrage par photo (``object-position`` CSS), vidé à chaque rebuild.
        self._positions: dict[str, str] = {}
        # Faux après un refus de l'API visages (clé sans « face.read »).
        self._faces_ok = True

    # ------------------------------------------------------------------ #
    # Invités
    # ------------------------------------------------------------------ #
    def _read(self, entity_id: str) -> str | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        value = normalize_name(extract_guest_name(state.state))
        return None if value.casefold() in _EMPTY_STATES else value

    def get_guests(self) -> list[str]:
        """Invités renseignés, dans l'ordre des helpers, sans doublon."""
        return list(dict.fromkeys(g for g in map(self._read, self.helpers) if g))

    def get_guests_by_bed(self) -> list[str | None]:
        """Invité de chaque helper (``None`` si vide), indice conservé."""
        return [self._read(h) for h in self.helpers]

    # ------------------------------------------------------------------ #
    # Personnes Immich
    # ------------------------------------------------------------------ #
    async def _resolve_person_id(self, ref: str) -> str | None:
        """Résout (avec mémo) une saisie vers un id de personne Immich.

        1. liaison enregistrée (nom -> ID) ; 2. UUID ou URL de la fiche ;
        3. recherche par nom.
        """
        if ref not in self._person_ids:
            person = None
            linked = self._links.get(ref) if self._links else None
            if linked:
                person = await self._api.async_get_person(linked)
                if person is None:
                    _LOGGER.warning(
                        "[%s] La personne liée à '%s' (%s) n'existe plus dans "
                        "Immich: recherche par nom",
                        self.room_id,
                        ref,
                        linked,
                    )
            if person is None:
                uuid = extract_person_uuid(ref)
                if uuid:
                    person = await self._api.async_get_person(uuid)
                else:
                    person = await self._api.async_find_person(ref)
            self._person_ids[ref] = person["id"] if person else None
            if person is not None:
                self._person_names[ref] = str(person.get("name") or "").strip() or ref
            else:
                _LOGGER.warning("[%s] Personne Immich introuvable: %s", self.room_id, ref)
        return self._person_ids[ref]

    async def async_person_id(self, ref: str) -> str | None:
        """ID de personne Immich d'une saisie (liaison, UUID ou nom), mémoïsé."""
        try:
            return await self._resolve_person_id(ref)
        except ImmichApiError as err:
            _LOGGER.debug("[%s] Résolution de '%s' en échec: %s", self.room_id, ref, err)
            return None

    def display_name(self, ref: str | None) -> str | None:
        """Nom Immich d'une saisie résolue (utile quand c'est un UUID)."""
        if not ref:
            return None
        return self._person_names.get(ref, ref)

    def _labelled(self, combo: SearchCombo) -> SearchCombo:
        display = tuple(self._person_names.get(n, n) for n in combo.names)
        return combo if display == combo.names else SearchCombo(combo.names, display)

    # ------------------------------------------------------------------ #
    # Recherches et sélection
    # ------------------------------------------------------------------ #
    async def _async_search(self, names: tuple[str, ...]) -> list[str]:
        """Photos contenant toutes les personnes ``names`` (mémoïsé, mélangé)."""
        if names in self._searches:
            return self._searches[names]
        assets: list[str] = []
        try:
            ids = [await self._resolve_person_id(n) for n in names]
            if all(ids):
                found = await self._api.async_search_assets(
                    list(dict.fromkeys(pid for pid in ids if pid)),
                    limit=MAX_ASSETS_PER_SEARCH,
                )
                assets = [a["id"] for a in found if a.get("id")]
                combo = self._labelled(SearchCombo(names))
                self.combos.append(combo)
                for asset in assets:
                    self._labels.setdefault(asset, combo.label)
        except ImmichApiError as err:
            _LOGGER.warning(
                "[%s] Recherche '%s' en échec: %s", self.room_id, " + ".join(names), err
            )
            # 2.3.1 : un échec n'est PAS mémorisé comme « aucune photo »
            self.incomplete = True
            return []
        random.shuffle(assets)
        self._searches[names] = assets
        return assets

    def _reset_searches(self) -> None:
        self._searches = {}
        self.incomplete = False
        self._labels = {}
        self.combos = []

    async def _async_select(
        self, groups: list[list[str]], size: int
    ) -> list[list[str]]:
        """Sélection par niveaux de priorité pour plusieurs cadres.

        ``groups[i]`` : invités montrés par le cadre ``i`` (vide : aucun).
        Les photos sont distribuées niveau par niveau, à tour de rôle entre
        les cadres, sans doublon.
        """
        guests = list(dict.fromkeys(g for group in groups for g in group))
        picked: list[list[str]] = [[] for _ in groups]
        used: set[str] = set()
        depth = max(
            (len(priority_levels(g, guests, self._permanents)) for g in guests),
            default=0,
        )
        for level in range(depth):
            short = [i for i, grp in enumerate(groups) if grp and len(picked[i]) < size]
            if not short:
                break
            candidates: list[list[str]] = [[] for _ in groups]
            for i in short:
                lists = []
                for guest in groups[i]:
                    levels = priority_levels(guest, guests, self._permanents)
                    if level < len(levels):
                        lists.append(await self._async_search(levels[level]))
                candidates[i] = interleave(lists)
            deal(candidates, picked, used, size)
        return picked

    async def async_frame_playlists(
        self, groups: list[list[str]], size: int
    ) -> list[list[str]]:
        """Photos de chaque cadre (voir :func:`frames_guests`).

        Un cadre qui ne reçoit aucune photo alors que ses invités en ont
        (moins de photos que de cadres) reçoit quand même les leurs, quitte à
        les partager : il montre l'invité plutôt qu'un écran vide.
        """
        async with self._select_lock:
            self._reset_searches()
            picked = await self._async_select(groups, size)
            for i, group in enumerate(groups):
                if group and not picked[i]:
                    alone = await self._async_select([group], size)
                    picked[i] = alone[0]
            if self.incomplete:
                # 2.3.1 : Immich injoignable pendant la sélection — l'appelant
                # (cadres) ne mémorise rien et réessaie au prochain réveil.
                raise ImmichApiError("sélection incomplète (Immich injoignable)")
            return picked

    # ------------------------------------------------------------------ #
    # Diaporama des dashboards
    # ------------------------------------------------------------------ #
    async def async_rebuild(self) -> None:
        """Vide le cache, refait la sélection et recharge les photos.

        Appelé au démarrage, à chaque changement d'invité, et périodiquement.
        Les photos s'affichent dès que l'invité est renseigné (avant même son
        arrivée).
        """
        async with self._select_lock:
            await self._async_rebuild()

    async def _async_rebuild(self) -> None:
        self._cache.clear()
        self.current = None
        self._positions.clear()
        self._faces_ok = True
        self._person_ids.clear()
        self._person_names.clear()
        self._reset_searches()
        guests = self.get_guests()
        if not guests:
            _LOGGER.debug("[%s] Aucun invité: diaporama inactif", self.room_id)
            self._set_problems([])
            return

        picked = (await self._async_select([guests], self._cache.max_size))[0]
        self._cache.replace(
            [CachedPhoto(asset_id=a, search_label=self._labels.get(a, "")) for a in picked]
        )

        # Signalements : toutes les photos connues de chaque invité.
        guest_assets: dict[str, set[str]] = {}
        for guest in guests:
            known: set[str] = set()
            for names, assets in self._searches.items():
                if guest in names:
                    known.update(assets)
            if len(known) < self._min_photos and (guest,) not in self._searches:
                known.update(await self._async_search((guest,)))
            guest_assets[guest] = known
        if self.incomplete:
            # 2.3.1 : pas de faux « introuvable / peu de photos » pendant une
            # panne d'Immich — les signalements précédents sont conservés.
            _LOGGER.warning(
                "[%s] Diaporama incomplet (Immich injoignable) : nouvel essai "
                "dans quelques minutes",
                self.room_id,
            )
            return
        self._set_problems(self._collect_problems(guests, guest_assets))
        _LOGGER.debug(
            "[%s] Diaporama: %d photo(s) via %d recherche(s)",
            self.room_id,
            len(self._cache),
            len(self.combos),
        )

    def next_photo(self) -> CachedPhoto | None:
        """Passe à la photo suivante."""
        self.current = self._cache.next_photo()
        return self.current

    def previous_photo(self) -> CachedPhoto | None:
        """Revient à la photo précédente."""
        self.current = self._cache.previous_photo()
        return self.current

    @property
    def photo_count(self) -> int:
        """Nombre de photos du diaporama."""
        return len(self._cache)

    async def async_get_image_bytes(self) -> bytes | None:
        """Octets de la photo courante du diaporama."""
        if self.current is None:
            return None
        try:
            return await self._api.async_get_thumbnail(self.current.asset_id)
        except ImmichApiError as err:
            _LOGGER.warning("[%s] Téléchargement image échoué: %s", self.room_id, err)
            return None

    async def async_object_position(self, asset_id: str | None) -> str:
        """``object-position`` CSS gardant visibles les visages des invités.

        Voir :mod:`framing`. Un appel Immich par photo, mémorisé.
        """
        if not asset_id:
            return framing.DEFAULT_OBJECT_POSITION
        if asset_id in self._positions:
            return self._positions[asset_id]
        if not self._faces_ok:
            return framing.DEFAULT_OBJECT_POSITION
        try:
            faces = await self._api.async_get_faces(asset_id)
        except ImmichApiError as err:
            _LOGGER.debug("[%s] Visages de %s indisponibles: %s", self.room_id, asset_id, err)
            if isinstance(err, ImmichAuthError):
                self._faces_ok = False  # clé sans « face.read »
            return framing.DEFAULT_OBJECT_POSITION
        guest_ids = [await self.async_person_id(g) for g in self.get_guests()]
        box = framing.faces_box(faces, guest_ids)
        position = framing.object_position(framing.expand_box(box) if box else None)
        self._positions[asset_id] = position
        return position

    # ------------------------------------------------------------------ #
    # Signalements
    # ------------------------------------------------------------------ #
    def _collect_problems(
        self, guests: list[str], guest_assets: dict[str, set[str]]
    ) -> list[PersonProblem]:
        """Personnes introuvables, et invités avec trop peu de photos.

        Une saisie qui est déjà un ID Immich n'est pas signalée : la lier à
        un nom n'aurait aucun sens, il faut corriger la saisie.
        """
        problems: list[PersonProblem] = []
        unresolved: set[str] = set()
        for ref, pid in self._person_ids.items():
            if pid is None and not extract_person_uuid(ref):
                unresolved.add(ref)
                problems.append(PersonProblem(ref, KIND_NOT_FOUND, 0, self._min_photos))
        for guest in guests:
            if guest in unresolved or self._person_ids.get(guest) is None:
                continue
            found = len(guest_assets[guest])
            if found < self._min_photos:
                problems.append(
                    PersonProblem(guest, KIND_FEW_PHOTOS, found, self._min_photos)
                )
        return problems

    def _missing_helper_problems(self) -> list[PersonProblem]:
        """Helpers configurés pour la chambre mais inexistants dans HA.

        Pendant le démarrage, les états ne sont pas tous chargés : rien n'est
        signalé, ``refresh_problems`` est rappelé une fois HA démarré.
        """
        if not getattr(self._hass, "is_running", True):
            return []
        return [
            PersonProblem(entity_id, KIND_MISSING_HELPER)
            for entity_id in self.helpers
            if self._hass.states.get(entity_id) is None
        ]

    def _set_problems(self, problems: list[PersonProblem]) -> None:
        self._person_problems = problems
        self.refresh_problems()

    def refresh_problems(self) -> None:
        """Republie les problèmes (personnes + helpers manquants)."""
        self.problems = [*self._person_problems, *self._missing_helper_problems()]
        if self._reporter is not None:
            self._reporter.async_update(self.room_id, self.room_name, self.problems)


"""Moteur de diaporama : une instance indépendante par chambre.

Construit les recherches Immich selon la logique invités/permanents,
remplit le cache local et fournit la photo suivante à afficher.
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
from .const import ALBUM_PREFIX, DEFAULT_MIN_PHOTOS, MAX_ASSETS_PER_SEARCH
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
# ex. « Jérémy Jouet (2026-07-17 → 2026-07-18) ». Seul le nom doit être
# envoyé à Immich : on retire tout groupe entre parenthèses en fin de chaîne.
_RESERVATION_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


class ProblemSink(Protocol):
    """Destinataire des problèmes détectés (Réparations, notifications)."""

    def async_update(
        self, room_id: str, room_name: str, problems: list[PersonProblem]
    ) -> None:
        """Reçoit la liste complète et à jour des problèmes d'une chambre."""


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
    # Noms affichables quand ``names`` contient des identifiants Immich (UUID).
    display: tuple[str, ...] | None = None

    @property
    def label(self) -> str:
        """Libellé lisible pour les sensors (ex. 'Alice + Propriétaire 1')."""
        return " + ".join(self.display or self.names)


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


def interleave(lists: list[list[str]]) -> list[str]:
    """Entrelace plusieurs listes (a1, b1, a2, b2, …), sans doublon.

    Sert à mélanger équitablement les photos de deux invités dans un même
    niveau de priorité.
    """
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
        min_photos: int = DEFAULT_MIN_PHOTOS,
        links: PersonLinks | None = None,
        reporter: ProblemSink | None = None,
    ) -> None:
        """Initialise le moteur pour une chambre donnée.

        Args:
            min_photos: minimum de photos attendu par invité ; en dessous, un
                problème « peu de photos » est signalé.
            links: liaisons nom -> ID de personne Immich (prioritaires).
            reporter: reçoit les personnes introuvables / avec trop peu de
                photos pour les signaler à l'utilisateur.
        """
        self._hass = hass
        self._api = api
        self.room_id = room_id
        self.room_name = room_name
        self.helpers = helpers
        # Normalisation NFC des permanents dès l'init : les noms saisis
        # dans le Config Flow depuis iOS/macOS peuvent arriver en NFD.
        self._permanents = [normalize_name(p) for p in permanents if p]
        self._cache = PhotoCache(max_size=cache_size)
        self._min_photos = max(1, int(min_photos))
        self._links = links
        self._reporter = reporter
        self.problems: list[PersonProblem] = []
        # Problèmes de personnes du dernier rebuild (les helpers manquants
        # sont recalculés à chaque publication).
        self._person_problems: list[PersonProblem] = []
        self._person_ids: dict[str, str | None] = {}
        # Nom d'affichage de chaque référence (utile quand elle est un UUID).
        self._person_names: dict[str, str] = {}
        self.current: CachedPhoto | None = None
        self.combos: list[SearchCombo] = []
        self._searches: dict[tuple[str, ...], list[str]] = {}
        self._labels: dict[str, str] = {}
        # Le diaporama et les cadres/albums sélectionnent sur le même moteur,
        # parfois en même temps (changement d'invité) : une sélection à la fois.
        self._select_lock = asyncio.Lock()
        # Cadrage par photo (``object-position`` CSS), calculé une seule fois
        # par photo et par séjour : vidé à chaque rebuild.
        self._positions: dict[str, str] = {}
        # Faux après un refus de l'API visages (clé sans « face.read ») :
        # on n'insiste pas avant le prochain rebuild.
        self._faces_ok = True

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
    async def _resolve_person_id(self, ref: str) -> str | None:
        """Résout (avec mémo) une référence vers un id de personne Immich.

        Ordre de résolution :

        1. liaison enregistrée par l'utilisateur (nom -> ID) ;
        2. identifiant Immich saisi directement (UUID, ou URL de la fiche) ;
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
                _LOGGER.warning(
                    "[%s] Personne Immich introuvable: %s", self.room_id, ref
                )
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
        """Retourne ``combo`` avec des noms lisibles à la place des UUID."""
        display = tuple(self._person_names.get(n, n) for n in combo.names)
        if display == combo.names:
            return combo
        return SearchCombo(combo.names, display)

    # ------------------------------------------------------------------ #
    # Recherches Immich par niveau de priorité
    # ------------------------------------------------------------------ #
    # Pour chaque invité, les photos sont prises dans cet ordre, jusqu'à
    # atteindre le nombre voulu :
    #   1. l'invité avec un propriétaire, l'autre, ou les deux ;
    #   2. les invités de la chambre ensemble (s'ils sont plusieurs) ;
    #   3. toutes les autres photos de l'invité.
    # Les recherches ne sont faites qu'au besoin, et mémorisées le temps
    # d'une sélection (``self._searches``).
    async def _async_search(self, names: tuple[str, ...]) -> list[str]:
        """Assets contenant toutes les personnes ``names`` (mémoïsé, mélangé)."""
        if names in self._searches:
            return self._searches[names]
        assets: list[str] = []
        try:
            ids = [await self._resolve_person_id(n) for n in names]
            if all(ids):
                found = await self._api.async_search_assets(
                    [pid for pid in ids if pid], limit=MAX_ASSETS_PER_SEARCH
                )
                assets = [a["id"] for a in found if a.get("id")]
                combo = self._labelled(SearchCombo(names))
                self.combos.append(combo)
                for asset in assets:
                    self._labels.setdefault(asset, combo.label)
        except ImmichApiError as err:
            _LOGGER.warning(
                "[%s] Recherche '%s' en échec: %s",
                self.room_id,
                " + ".join(names),
                err,
            )
        random.shuffle(assets)
        self._searches[names] = assets
        return assets

    async def _async_tier(self, level: int, guest: str, guests: list[str]) -> list[str]:
        """Photos d'un invité pour un niveau de priorité (1, 2 ou 3)."""
        if level == 1:
            return interleave(
                [await self._async_search((guest, p)) for p in self._permanents]
            )
        if level == 2:
            others = [g for g in guests if g != guest]
            if not others:
                return []
            return await self._async_search(tuple(guests))
        return await self._async_search((guest,))

    def _reset_searches(self) -> None:
        self._searches = {}
        self._labels = {}
        self.combos = []

    async def async_select(
        self, bed_guests: list[str | None], size: int
    ) -> list[list[str]]:
        """Sélection par niveaux pour plusieurs cadres (ou un seul diaporama).

        ``bed_guests[i]`` : l'invité (ou les invités, pour le diaporama de la
        chambre — voir ``async_rebuild``) dont le cadre ``i`` montre les
        photos ; ``None`` pour un cadre sans invité. Les niveaux sont
        distribués à tour de rôle entre les cadres, sans doublon.
        """
        guests = list(dict.fromkeys(g for g in bed_guests if g))
        picked: list[list[str]] = [[] for _ in bed_guests]
        used: set[str] = set()
        for level in (1, 2, 3):
            short = [
                i for i, g in enumerate(bed_guests) if g and len(picked[i]) < size
            ]
            if not short:
                break
            candidates: list[list[str]] = [[] for _ in bed_guests]
            for i in short:
                candidates[i] = await self._async_tier(level, bed_guests[i], guests)
            deal(candidates, picked, used, size)
        return picked

    async def async_rebuild(self) -> None:
        """Voir ``_async_rebuild`` (une sélection à la fois)."""
        async with self._select_lock:
            await self._async_rebuild()

    async def _async_rebuild(self) -> None:
        """Vide le cache, refait la sélection et recharge les photos.

        Appelé au démarrage et à chaque changement d'un helper input_text.

        Le diaporama de la chambre suit les mêmes niveaux de priorité que les
        cadres (voir ``_async_tier``), photos des invités entrelacées,
        jusqu'à la taille du cache. Le minimum de photos sert uniquement à
        prévenir l'utilisateur (personne introuvable ou trop peu de photos).
        """
        self._cache.clear()
        self.current = None
        self._positions.clear()
        self._faces_ok = True
        self._person_ids.clear()
        self._person_names.clear()
        self._reset_searches()
        guests = list(dict.fromkeys(self.get_guests()))

        if not guests:
            _LOGGER.debug("[%s] Aucun invité: diaporama inactif", self.room_id)
            self._set_problems([])
            return

        size = self._cache.max_size
        picked: list[str] = []
        used: set[str] = set()
        for level in (1, 2, 3):
            if len(picked) >= size:
                break
            tiers = [await self._async_tier(level, g, guests) for g in guests]
            deal([interleave(tiers)], [picked], used, size)
            # deal() complète picked[0] : on travaille sur une seule « file ».
        photos = [
            CachedPhoto(asset_id=a, search_label=self._labels.get(a, ""))
            for a in picked
        ]
        self._cache.replace(photos)

        # Comptage pour les signalements : toutes les photos connues de chaque
        # invité (sa recherche individuelle si elle a été faite).
        guest_assets: dict[str, set[str]] = {}
        for guest in guests:
            known: set[str] = set()
            for names, assets in self._searches.items():
                if guest in names:
                    known.update(assets)
            if len(known) < self._min_photos and (guest,) not in self._searches:
                known.update(await self._async_search((guest,)))
            guest_assets[guest] = known
        self._set_problems(self._collect_problems(guests, guest_assets))
        _LOGGER.debug(
            "[%s] Cache reconstruit: %d photo(s) via %d recherche(s)",
            self.room_id,
            len(self._cache),
            len(self.combos),
        )

    def _collect_problems(
        self, guests: list[str], guest_assets: dict[str, set[str]]
    ) -> list[PersonProblem]:
        """Personnes introuvables, et invités avec trop peu de photos.

        Une saisie qui est déjà un identifiant Immich n'est pas signalée : la
        lier à un nom n'aurait aucun sens, il faut corriger la saisie.
        """
        problems: list[PersonProblem] = []
        unresolved: set[str] = set()
        for ref, pid in self._person_ids.items():
            if pid is None and not extract_person_uuid(ref):
                unresolved.add(ref)
                problems.append(
                    PersonProblem(ref, KIND_NOT_FOUND, 0, self._min_photos)
                )
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

        Sans ce signalement, une chambre dont les helpers n'existent pas (nom
        modifié, helper supprimé) ne voit simplement aucun invité : aucun
        message, un capteur « indisponible », et rien pour comprendre pourquoi.

        Tant que Home Assistant démarre, les états des helpers ne sont pas
        tous chargés : on ne signale rien, ``refresh_problems`` est rappelé
        une fois le démarrage terminé.
        """
        if not getattr(self._hass, "is_running", True):
            return []
        return [
            PersonProblem(entity_id, KIND_MISSING_HELPER)
            for entity_id in self.helpers
            if self._hass.states.get(entity_id) is None
        ]

    def _set_problems(self, problems: list[PersonProblem]) -> None:
        """Mémorise les problèmes de personnes puis publie le tout."""
        self._person_problems = problems
        self.refresh_problems()

    def refresh_problems(self) -> None:
        """Republie les problèmes (personnes + helpers manquants)."""
        self.problems = [*self._person_problems, *self._missing_helper_problems()]
        if self._reporter is not None:
            self._reporter.async_update(self.room_id, self.room_name, self.problems)

    # ------------------------------------------------------------------ #
    # Albums Immich
    # ------------------------------------------------------------------ #
    def _album_name(self, index: int, guest: str | None) -> str:
        """« PicPak Chambre d'ami 1 — Alice »."""
        base = f"{ALBUM_PREFIX} {self.room_name} {index + 1}"
        if not guest:
            return base
        # Si l'invité est saisi sous forme d'UUID, on affiche son nom Immich.
        return f"{base} — {self._person_names.get(guest, guest)}"

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

    async def async_bed_playlists(
        self, size: int, *, fill_empty: bool = False
    ) -> list[tuple[str | None, list[str]]]:
        """Sélection de photos par lit : ``[(invité, [asset_id, ...]), ...]``.

        Une entrée par helper (donc par lit, donc par cadre photo), dans
        l'ordre des helpers. Pour chaque cadre, jusqu'à ``size`` photos, par
        niveau de priorité : invité avec les propriétaires, puis les invités
        ensemble, puis l'invité seul (voir ``_async_tier``).

        - Deux invités : chaque cadre montre son occupant ; les photos où ils
          sont ensemble sont réparties entre les deux cadres, sans doublon.
        - Un seul invité, plusieurs lits : tous les cadres le montrent, avec
          des photos différentes (distribution à tour de rôle).
        - Lit vide (et pas d'occupant unique) : ``(None, [])``.

        Args:
            fill_empty: un cadre qui ne reçoit aucune photo alors que son
                invité en a (moins de photos que de cadres) reçoit quand même
                ses photos, quitte à les partager. Utile aux cadres, qui
                doivent montrer l'invité plutôt que l'écran « chambre libre ».
        """
        async with self._select_lock:
            return await self._async_bed_playlists(size, fill_empty)

    async def _async_bed_playlists(
        self, size: int, fill_empty: bool
    ) -> list[tuple[str | None, list[str]]]:
        beds = self.get_guests_by_bed()
        occupied = [g for g in beds if g]
        if not occupied:
            return [(None, []) for _ in beds]
        self._reset_searches()
        distinct = list(dict.fromkeys(occupied))
        if len(distinct) == 1:
            # Occupant unique : il alimente tous les cadres de la chambre.
            beds = [distinct[0]] * len(beds)
        picked = await self.async_select(beds, size)
        if fill_empty:
            for i, guest in enumerate(beds):
                if guest and not picked[i]:
                    picked[i] = list(
                        interleave(
                            [await self._async_tier(lvl, guest, distinct) for lvl in (1, 2, 3)]
                        )[:size]
                    )
        return list(zip(beds, picked, strict=True))

    async def async_sync_albums(self, size: int) -> list[dict]:
        """Synchronise un album Immich par lit occupé.

        La répartition des photos entre les lits est celle de
        :meth:`async_bed_playlists`. Un lit vide garde son album intact.
        """
        plans = await self.async_bed_playlists(size)
        if not any(guest for guest, _ in plans):
            _LOGGER.debug("[%s] Aucun invité: albums inchangés", self.room_id)
            return []
        results: list[dict] = []
        for index, (guest, asset_ids) in enumerate(plans):
            if not guest:
                continue
            results.append(await self._async_upsert_album(index, guest, asset_ids))
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

    async def async_object_position(self, asset_id: str | None) -> str:
        """``object-position`` CSS gardant visibles les visages des invités.

        Visages des invités de la chambre (tous ensemble s'ils sont sur la
        photo), à défaut tous les visages détectés, avec une marge pour les
        cheveux et le menton : voir :mod:`framing`. Sans visage connu (ou clé
        API sans la permission « face.read »), position par défaut un peu
        au-dessus du centre. Un appel Immich par photo, mémorisé.
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
            _LOGGER.debug(
                "[%s] Visages de %s indisponibles: %s", self.room_id, asset_id, err
            )
            # Clé sans permission : inutile de réessayer à chaque photo.
            # Erreur passagère : on réessaiera pour cette photo plus tard.
            if isinstance(err, ImmichAuthError):
                self._faces_ok = False
            return framing.DEFAULT_OBJECT_POSITION
        guest_ids = [await self.async_person_id(g) for g in self.get_guests()]
        box = framing.faces_box(faces, guest_ids)
        position = framing.object_position(
            framing.expand_box(box) if box else None
        )
        self._positions[asset_id] = position
        return position

    async def async_get_image_bytes(self) -> bytes | None:
        """Récupère les octets de l'image courante depuis Immich."""
        if self.current is None:
            return None
        try:
            return await self._api.async_get_thumbnail(self.current.asset_id)
        except ImmichApiError as err:
            _LOGGER.warning("[%s] Téléchargement image échoué: %s", self.room_id, err)
            return None

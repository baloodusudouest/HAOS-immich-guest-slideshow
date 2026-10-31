"""Cadres e-ink PicPak alimentés par Home Assistant.

Chaque cadre tourne avec le firmware open-PicPak patché « tirage Wi-Fi » : à
chaque réveil il télécharge sur
``/api/immich_guest_slideshow/picpak/<chambre>/<emplacement>?k=<jeton>`` une
image déjà rendue de 30 000 octets, l'affiche si elle a changé, puis se
rendort pendant la durée indiquée par l'en-tête ``X-Next-Wake-Seconds``.

Deux emplacements par chambre :

**Cadres dans la chambre** (« 1 », « 2 ») :

- chambre libre : logo + nom de la chambre ;
- séjour programmé (le prochain du calendrier, quelle que soit sa date) :
  logo + nom de la chambre + noms des invités + dates ;
- séjour en cours : les photos des invités, une par heure, autant que d'heures
  de séjour hors pause nocturne (rotation s'il y en a moins). Deux cadres et
  deux invités : un invité par cadre ; un seul invité : ses photos sur les
  deux, sans doublon ; un seul cadre : photos des deux invités mélangées.

**Cadre de la porte** (« porte ») :

- chambre libre : logo + nom de la chambre ;
- séjour programmé : « Bientôt dans la chambre … », visage de chaque invité
  dans le losange du logo Petit Lecussan, prénoms, date prévue ;
- séjour en cours : la même carte avec « Bienvenue dans la chambre … ».

Visages (2.4.0) : portrait HD de Carnet quand il est configuré (meilleure
photo du contact, cadre du visage fourni : visage entier centré dans le
losange), sinon vignette de la personne dans Immich. Les mêmes portraits sont
servis aux dashboards (attribut ``portraits`` de ``image.immich_<chambre>``).

Le séjour va du jour d'arrivée à 9 h (réglable) jusqu'au lendemain du dernier
jour à 00:01 (voir :mod:`.stay`). Un écran inchangé n'est jamais redessiné :
le cadre compare l'image reçue à celle qu'il affiche.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import framing, picpak_render
from .api import ImmichApiClient, ImmichApiError
from .carnet import CarnetClient, CarnetError, FaceBox
from .const import (
    DOMAIN,
    DOOR_SLOT,
    MAX_FRAME_PHOTOS,
    PICPAK_MANUFACTURER,
    PICPAK_MODEL,
    PICPAK_URL_MAX,
    PICPAK_URL_PATH,
    PORTRAIT_URL_PATH,
    SIGNAL_PICPAK_UPDATE,
)
from .slideshow import first_name, frames_guests
from .stay import (
    FREE,
    ONGOING,
    PLANNED,
    StayStatus,
    frame_photo_count,
    quiet_remaining,
)

if TYPE_CHECKING:
    from .room import RoomRuntime

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 15

# Délai au-delà duquel on répond au cadre sans attendre la fin du rendu.
RENDER_DEADLINE = 9.0
# Réveil anticipé quand le rendu n'était pas prêt à temps.
RETRY_SOON_S = 120
MIN_WAKE_S_FLOOR = 60
MAX_WAKE_S = 12 * 3600
# Marge après un changement d'état (début ou fin de séjour).
TRANSITION_MARGIN_S = 60
# Nombre de photos essayées quand Immich échoue sur l'une d'elles.
MAX_ASSET_TRIES = 3

# Contenu affiché (FrameState.shown) quand ce n'est pas une photo.
LOGO = "__logo__"
DOOR = "__porte__"

# Portraits des invités gardés en mémoire (Carnet les change au plus une fois
# par mois ; un nouveau choix manuel apparaît donc dans les 6 h).
PORTRAIT_TTL_S = 6 * 3600


@dataclass(frozen=True)
class Portrait:
    """Visage d'un invité : image, cadre du visage (0 à 1) si connu, source."""

    image: bytes
    face: FaceBox | None
    source: str  # « carnet » ou « immich »

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.image).hexdigest()[:12]

    @property
    def content_type(self) -> str:
        head = self.image[:12]
        if head.startswith(b"\x89PNG"):
            return "image/png"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"


def frame_key(room_id: str, slot: str | int) -> str:
    """Clé d'un cadre : « chambre_d_ami/1 », « chambre_d_ami/porte »."""
    return f"{room_id}/{slot}"


def split_key(key: str) -> tuple[str, str]:
    """« chambre_d_ami/porte » -> (« chambre_d_ami », « porte »)."""
    room_id, slot = key.rsplit("/", 1)
    return room_id, slot


@dataclass
class PicPakSettings:
    """Réglages des cadres (options de l'intégration)."""

    interval_min: int
    photos: int  # repli sans calendrier
    quiet: tuple[time, time] | None
    logo_path: str
    title: str
    subtitle: str
    base_url: str
    logo_path_2: str = ""

    def logo_for(self, slot: str) -> str:
        """Logo d'un emplacement : le 2e logo pour le cadre n° 2 s'il est
        renseigné, sinon le logo principal (porte comprise)."""
        if slot == "2" and self.logo_path_2:
            return self.logo_path_2
        return self.logo_path

    @property
    def interval_s(self) -> int:
        return max(60, int(self.interval_min) * 60)


@dataclass
class FrameState:
    """État persistant d'un cadre."""

    signature: str | None = None
    guest: str | None = None          # invité(s) montré(s), pour l'affichage
    guest_name: str | None = None
    person_ids: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    index: int = -1
    advanced_at: str | None = None
    force_next: bool = False
    last_seen: str | None = None
    battery_mv: int | None = None
    battery_pct: int | None = None
    wake: str | None = None
    next_wake_s: int | None = None
    shown: str | None = None  # asset affiché, LOGO ou DOOR
    requests: int = 0
    state: str | None = None  # libre / programme / en_cours au dernier réveil

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameState:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        state = cls(**known)
        state.assets = [str(a) for a in (state.assets or [])]
        state.person_ids = [str(p) for p in (state.person_ids or [])]
        if not state.person_ids and data.get("person_id"):  # état v1
            state.person_ids = [str(data["person_id"])]
        return state


@dataclass(frozen=True)
class FrameReply:
    """Réponse à un réveil de cadre."""

    body: bytes
    next_wake_s: int


@dataclass(frozen=True)
class _Screen:
    """Ce qu'un cadre doit afficher à un instant donné."""

    kind: str          # LOGO, DOOR ou "photo"
    state: str         # libre / programme / en_cours
    caption: str = ""  # légende du logo
    transition: datetime | None = None  # prochain changement d'état


class PicPakManager:
    """Gère tous les cadres d'une config entry, et l'état de la maison
    (jeton des cadres, réinitialisation)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        api: ImmichApiClient,
        settings: PicPakSettings,
        carnet: CarnetClient | None = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._api = api
        self._carnet = carnet
        self.settings = settings
        # Même fichier qu'en v1 : le jeton (donc l'URL des cadres) est conservé.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.picpak.{entry_id}"
        )
        self.token: str = ""
        # Dernier appui sur le bouton de réinitialisation de la maison.
        self.closed_at: datetime | None = None
        # Chambres sans calendrier réinitialisées : rien jusqu'au prochain
        # changement d'invité.
        self._closed_rooms: set[str] = set()
        self._stored_frames: dict[str, Any] = {}
        self.rooms: dict[str, RoomRuntime] = {}
        self.frames: dict[str, FrameState] = {}
        # Tampons rendus : asset_id -> 30 000 octets (cache partagé, borné).
        self._rendered: dict[str, bytes] = {}
        self._render_tasks: dict[str, asyncio.Task[bytes | None]] = {}
        # Dernier tampon envoyé à chaque cadre (= ce qu'affiche la dalle).
        self._last_body: dict[str, bytes] = {}
        self._preview_png: dict[str, bytes] = {}
        self._body_rev: dict[str, int] = {}
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._logo_sig: tuple | None = None
        self._logos: dict[tuple[str, str], bytes] = {}  # (logo, légende) -> tampon
        self._door_cards: dict[str, tuple[tuple, bytes]] = {}
        # Portraits : person_id -> (instant de lecture, portrait).
        self._portraits: dict[str, tuple[datetime, Portrait]] = {}
        self._carnet_down_logged = False

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    async def async_load(self) -> None:
        """Charge l'état persistant (jeton, réinitialisation, cadres)."""
        data = await self._store.async_load() or {}
        self.token = str(data.get("token") or "") or secrets.token_urlsafe(16)
        closed = data.get("closed_at")
        self.closed_at = dt_util.parse_datetime(closed) if closed else None
        self._closed_rooms = set(data.get("closed_rooms") or [])
        self._stored_frames = dict(data.get("frames") or {})
        if not data.get("token"):
            self._schedule_save()

    def is_closed_without_calendar(self, room_id: str) -> bool:
        """Chambre sans calendrier réinitialisée (voir :class:`StayTracker`)."""
        return room_id in self._closed_rooms

    @callback
    def async_attach_rooms(self, rooms: dict[str, RoomRuntime]) -> None:
        """Déclare les chambres et crée leurs cadres."""
        self.rooms = rooms
        self.frames = {}
        for room_id, room in rooms.items():
            for slot in room.config.slots:
                key = frame_key(room_id, slot)
                raw = self._stored_frames.get(key)
                self.frames[key] = (
                    FrameState.from_dict(raw) if isinstance(raw, dict) else FrameState()
                )

    @callback
    def async_start(self) -> None:
        """Prépare en arrière-plan les sélections et l'image de chaque cadre,
        pour que les aperçus soient justes dès le démarrage."""

        async def _warm() -> None:
            for room_id in list(self.rooms):
                await self.async_ensure_room(room_id)
            for key in list(self.frames):
                if key not in self._last_body:
                    try:
                        body, _, _ = await self._async_compose(key, dt_util.now(), advance=False)
                    except Exception as err:  # noqa: BLE001 - aperçu seulement
                        _LOGGER.debug("PicPak %s : aperçu initial impossible: %s", key, err)
                        continue
                    self._set_body(key, body)
                self._prefetch(key)
                self._notify(key)

        self.hass.async_create_background_task(_warm(), f"{DOMAIN} picpak warm-up")

    async def async_unload(self) -> None:
        """Annule les rendus en cours et enregistre l'état."""
        for task in self._render_tasks.values():
            task.cancel()
        self._render_tasks.clear()
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict[str, Any]:
        frames = dict(self._stored_frames)
        frames.update({k: f.as_dict() for k, f in self.frames.items()})
        return {
            "token": self.token,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closed_rooms": sorted(self._closed_rooms),
            "frames": frames,
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def check_token(self, token: str | None) -> bool:
        """Comparaison à temps constant du jeton d'accès."""
        return bool(token) and hmac.compare_digest(str(token), self.token)

    # ------------------------------------------------------------------ #
    # Sélection des photos (par chambre)
    # ------------------------------------------------------------------ #
    def _photo_slots(self, room_id: str) -> list[str]:
        return [s for s in self.rooms[room_id].config.slots if s != DOOR_SLOT]

    async def _async_frame_size(self, room: RoomRuntime) -> tuple[int, str]:
        """Photos par cadre pour le séjour, et la fenêtre qui les justifie."""
        stay = await room.stay.async_status()
        if stay.window is None:
            return max(1, int(self.settings.photos)), "-"
        size = frame_photo_count(
            stay.window,
            self.settings.interval_s,
            self.settings.quiet,
            MAX_FRAME_PHOTOS,
        )
        return size, f"{stay.window[0].isoformat()}/{stay.window[1].isoformat()}"

    async def async_ensure_room(self, room_id: str, *, force: bool = False) -> None:
        """(Re)construit les sélections des cadres photo d'une chambre si les
        invités ou le séjour ont changé."""
        room = self.rooms.get(room_id)
        if room is None:
            return
        slots = self._photo_slots(room_id)
        if not slots:
            return
        keys = [frame_key(room_id, s) for s in slots]
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            engine = room.engine
            guests = engine.get_guests()
            size, window = await self._async_frame_size(room)
            signature = f"{'|'.join(guests)}#{len(slots)}#{size}#{window}"
            if not force and all(self.frames[k].signature == signature for k in keys):
                return
            groups = frames_guests(guests, len(slots))
            try:
                playlists = (
                    await engine.async_frame_playlists(groups, size)
                    if guests
                    else [[] for _ in slots]
                )
            except ImmichApiError as err:
                _LOGGER.warning("[%s] PicPak : sélection en échec: %s", room_id, err)
                return
            for key, group, assets in zip(keys, groups, playlists, strict=True):
                frame = self.frames[key]
                frame.signature = signature
                frame.guest = " & ".join(group) or None
                frame.guest_name = (
                    " & ".join(engine.display_name(g) or g for g in group) or None
                )
                frame.person_ids = [
                    pid for pid in [await engine.async_person_id(g) for g in group] if pid
                ]
                frame.assets = list(assets)
                frame.index = -1  # première photo au prochain réveil
                frame.force_next = False
                _LOGGER.info(
                    "[%s] PicPak %s : %s, %d photo(s) pour le séjour",
                    room_id,
                    key,
                    frame.guest_name or "aucun invité",
                    len(assets),
                )
            self._schedule_save()
        for key in keys:
            if key not in self._last_body:
                self._body_rev[key] = self._body_rev.get(key, 0) + 1
            self._prefetch(key)
            self._notify(key)

    # ------------------------------------------------------------------ #
    # Événements de la maison
    # ------------------------------------------------------------------ #
    @callback
    def async_reset_house(self) -> None:
        """Bouton « réinitialiser » : fin des séjours en cours.

        Les cadres ne peuvent pas être réveillés à distance : ils changent à
        leur prochain réveil (ou tout de suite sur un appui de leur bouton).
        Un séjour qui commence plus tard s'affichera normalement.
        """
        self.closed_at = dt_util.now()
        self._closed_rooms = set(self.rooms)
        for room in self.rooms.values():
            room.stay.invalidate()
        self._schedule_save()
        _LOGGER.info("PicPak : maison réinitialisée")
        for room_id in self.rooms:
            self.hass.async_create_background_task(
                self.async_ensure_room(room_id), f"{DOMAIN} picpak reset {room_id}"
            )
        for key in self.frames:
            self._invalidate_preview(key)

    @callback
    def async_room_changed(
        self, room_id: str, *, guests_changed: bool = True, force: bool = False
    ) -> None:
        """Invités, séjour ou personnes Immich modifiés : nouvelle sélection en
        arrière-plan.

        ``guests_changed`` : un nouvel invité met fin à la réinitialisation
        d'une chambre sans calendrier.
        ``force`` (2.3.1) : refait la sélection même si l'empreinte du cadre
        (invités, nombre de cadres, séjour) n'a pas changé — après une
        liaison de personne ou un « refresh », les photos changent sans que
        l'empreinte bouge.
        """
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.stay.invalidate()
        self._door_cards.pop(room_id, None)
        if guests_changed and room_id in self._closed_rooms:
            self._closed_rooms.discard(room_id)
            self._schedule_save()
        self.hass.async_create_background_task(
            self.async_ensure_room(room_id, force=force), f"{DOMAIN} picpak {room_id}"
        )
        for key in self.frames:
            if split_key(key)[0] == room_id:
                self._invalidate_preview(key)

    def request_next(self, room_id: str | None = None, slot: str | None = None) -> int:
        """Photo suivante au prochain réveil (cadres photo seulement)."""
        count = 0
        for key, frame in self.frames.items():
            r, s = split_key(key)
            if s == DOOR_SLOT or (room_id and r != room_id) or (slot and s != str(slot)):
                continue
            frame.force_next = True
            count += 1
        if count:
            self._schedule_save()
        return count

    # ------------------------------------------------------------------ #
    # Que doit afficher un cadre ?
    # ------------------------------------------------------------------ #
    def _names(self, room: RoomRuntime, stay: StayStatus, first: bool) -> list[str]:
        """Invités à nommer : les helpers, à défaut les titres du calendrier."""
        engine = room.engine
        names = [engine.display_name(g) or g for g in engine.get_guests()]
        if not names:
            names = list(stay.names)
        if first:
            names = [first_name(n) or n for n in names]
        return [n for n in names if n][:2]

    def _screen(self, key: str, stay: StayStatus) -> _Screen:
        room_id, slot = split_key(key)
        room = self.rooms[room_id]
        name = room.config.name
        frame = self.frames[key]
        if slot == DOOR_SLOT:
            if (stay.active or stay.days is not None) and self._names(room, stay, first=True):
                if stay.active:
                    end = stay.window[1] if stay.window else None
                    return _Screen(DOOR, ONGOING, transition=end)
                return _Screen(DOOR, PLANNED, transition=stay.next_start)
            return _Screen(LOGO, FREE, caption=name, transition=stay.next_start)
        if stay.active and frame.assets:
            end = stay.window[1] if stay.window else None
            return _Screen("photo", ONGOING, transition=end)
        if stay.days is not None or (stay.active and stay.names):
            names = self._names(room, stay, first=False)
            lines = [name, " et ".join(names), stay.label]
            transition = stay.next_start or (stay.window[1] if stay.window else None)
            return _Screen(
                LOGO,
                ONGOING if stay.active else PLANNED,
                caption="\n".join(ln for ln in lines if ln),
                transition=transition,
            )
        return _Screen(LOGO, FREE, caption=name, transition=stay.next_start)

    async def _async_compose(
        self,
        key: str,
        now: datetime,
        *,
        advance: bool = True,
        wake: str | None = None,
        quiet_left: float | None = None,
    ) -> tuple[bytes, int, bool]:
        """Image du cadre ``key`` à ``now`` : ``(tampon, prochain réveil, prêt)``.

        ``advance`` : faire avancer la rotation des photos (réveil réel) ;
        faux pour un simple aperçu.
        """
        room_id, slot = split_key(key)
        room = self.rooms[room_id]
        frame = self.frames[key]
        stay = await room.stay.async_status(now)
        screen = self._screen(key, stay)
        interval = self.settings.interval_s
        next_wake = interval
        ready = True

        if screen.kind == DOOR:
            body = await self._async_door_card(room_id, stay)
        elif screen.kind == "photo":
            if advance and self._should_advance(frame, now, interval, wake, quiet_left):
                frame.index = (frame.index + 1) % len(frame.assets)
                frame.advanced_at = now.isoformat()
                frame.force_next = False
            body, ready = await self._async_photo_body(key, frame, wait=advance)
            if not ready:
                next_wake = RETRY_SOON_S
        else:
            body = await self._async_logo(slot, screen.caption)

        if advance:
            frame.shown = (
                frame.assets[frame.index]
                if screen.kind == "photo" and ready and 0 <= frame.index < len(frame.assets)
                else (frame.shown if screen.kind == "photo" else screen.kind)
            )
            frame.state = screen.state
        if quiet_left is not None and ready:
            next_wake = int(quiet_left) + TRANSITION_MARGIN_S
        if screen.transition is not None:
            until = (screen.transition - now).total_seconds() + TRANSITION_MARGIN_S
            next_wake = int(min(next_wake, max(until, MIN_WAKE_S_FLOOR)))
        return body, next_wake, ready

    # ------------------------------------------------------------------ #
    # Réveil d'un cadre
    # ------------------------------------------------------------------ #
    async def async_wake(
        self,
        room_id: str,
        slot: str,
        *,
        battery: str | None = None,
        wake: str | None = None,
    ) -> FrameReply | None:
        """Traite le réveil d'un cadre ; ``None`` si le cadre n'existe pas."""
        key = frame_key(room_id, slot)
        frame = self.frames.get(key)
        if frame is None:
            return None
        now = dt_util.now()
        self._record_telemetry(frame, now, battery, wake)
        await self.async_ensure_room(room_id)
        quiet_left = quiet_remaining(now, self.settings.quiet)
        body, next_wake, _ = await self._async_compose(
            key, now, advance=True, wake=wake, quiet_left=quiet_left
        )
        frame.next_wake_s = int(max(MIN_WAKE_S_FLOOR, min(next_wake, MAX_WAKE_S)))
        self._set_body(key, body)
        self._schedule_save()
        self._prefetch(key)
        self._notify(key)
        return FrameReply(body=body, next_wake_s=frame.next_wake_s)

    @staticmethod
    def _should_advance(
        frame: FrameState,
        now: datetime,
        interval: int,
        wake: str | None,
        quiet_left: float | None,
    ) -> bool:
        """Faut-il passer à la photo suivante à ce réveil ?"""
        if frame.index < 0 or frame.index >= len(frame.assets):
            return True  # nouvelle sélection : première photo
        if frame.force_next or wake == "button":
            return True
        if quiet_left is not None:
            return False
        if not frame.advanced_at:
            return True
        last = dt_util.parse_datetime(frame.advanced_at)
        if last is None:
            return True
        # Tolérance : la minuterie du cadre dérive de quelques pour cent.
        tolerance = min(300, interval // 10)
        return (now - last).total_seconds() >= interval - tolerance

    def _record_telemetry(
        self,
        frame: FrameState,
        now: datetime,
        battery: str | None,
        wake: str | None,
    ) -> None:
        frame.last_seen = now.isoformat()
        frame.requests += 1
        frame.wake = wake if wake in ("timer", "button", "boot") else None
        if battery:
            mv, _, pct = battery.partition(";")
            try:
                frame.battery_mv = int(mv) if int(mv) > 0 else None
            except ValueError:
                frame.battery_mv = None
            try:
                frame.battery_pct = int(pct) if 0 <= int(pct) <= 100 else None
            except ValueError:
                frame.battery_pct = None

    # ------------------------------------------------------------------ #
    # Rendus
    # ------------------------------------------------------------------ #
    def _logo_signature(self) -> tuple:
        sig: list = [self.settings.title, self.settings.subtitle]
        for path in (self.settings.logo_path, self.settings.logo_path_2):
            try:
                mtime = os.path.getmtime(path) if path else None
            except OSError:
                mtime = None
            sig.append((path, mtime))
        return tuple(sig)

    async def _async_logo(self, slot: str, caption: str) -> bytes:
        """Logo légendé (nom de la chambre, invités, dates), mis en cache."""
        sig = await self.hass.async_add_executor_job(self._logo_signature)
        if sig != self._logo_sig:
            self._logos.clear()
            self._logo_sig = sig
        path = self.settings.logo_for(slot)
        cache_key = (path, caption)
        if cache_key not in self._logos:
            if len(self._logos) > 4 * max(1, len(self.frames)):
                self._logos.clear()
            self._logos[cache_key] = await self.hass.async_add_executor_job(
                picpak_render.render_logo,
                path,
                self.settings.title,
                self.settings.subtitle,
                caption,
            )
        return self._logos[cache_key]

    async def async_portrait(self, person_id: str | None) -> Portrait | None:
        """Visage d'une personne : portrait HD de Carnet s'il existe, sinon
        vignette Immich. Gardé 6 h ; une panne n'est pas mémorisée (nouvel
        essai au prochain réveil)."""
        if not person_id:
            return None
        now = dt_util.utcnow()
        cached = self._portraits.get(person_id)
        if cached and (now - cached[0]).total_seconds() < PORTRAIT_TTL_S:
            return cached[1]
        portrait: Portrait | None = None
        carnet_ok = True
        if self._carnet is not None:
            try:
                found = await self._carnet.async_get_portrait(person_id)
            except CarnetError as err:
                carnet_ok = False
                if not self._carnet_down_logged:
                    _LOGGER.warning(
                        "Carnet injoignable (%s) : vignettes Immich en attendant", err
                    )
                    self._carnet_down_logged = True
            else:
                self._carnet_down_logged = False
                if found is not None:
                    portrait = Portrait(found[0], found[1], "carnet")
        if portrait is None:
            try:
                thumb = await self._api.async_get_person_thumbnail(person_id)
            except ImmichApiError as err:
                _LOGGER.debug("PicPak : vignette de %s indisponible: %s", person_id, err)
                return cached[1] if cached else None
            if not thumb:
                return cached[1] if cached else None
            portrait = Portrait(thumb, None, "immich")
        if carnet_ok:
            self._portraits[person_id] = (now, portrait)
        return portrait

    async def async_guest_portrait(self, room_id: str, index: int) -> Portrait | None:
        """Portrait du n-ième invité renseigné de la chambre (1 ou 2)."""
        room = self.rooms.get(room_id)
        if room is None:
            return None
        guests = room.engine.get_guests()
        if not 1 <= index <= len(guests):
            return None
        return await self.async_portrait(await room.engine.async_person_id(guests[index - 1]))

    def portrait_urls(self, room_id: str) -> list[dict[str, str]]:
        """Dashboards : nom et adresse du portrait de chaque invité renseigné.

        Adresse relative (même origine que le dashboard), protégée par le
        jeton des cadres ; ``v`` change avec l'invité pour vider le cache du
        navigateur."""
        room = self.rooms.get(room_id)
        if room is None or not self.token:
            return []
        out = []
        for index, guest in enumerate(room.engine.get_guests()[:2], start=1):
            version = hashlib.sha1(guest.encode()).hexdigest()[:8]
            path = PORTRAIT_URL_PATH.format(room_id=room_id, index=index)
            out.append(
                {
                    "nom": room.engine.display_name(guest) or guest,
                    "url": f"{path}?k={self.token}&v={version}",
                }
            )
        return out

    async def _async_door_card(self, room_id: str, stay: StayStatus) -> bytes:
        """Carte de la porte : « Bientôt / Bienvenue dans la chambre … »,
        visages dans le losange du logo, prénoms, date prévue."""
        room = self.rooms[room_id]
        engine = room.engine
        guests: list[tuple[str, str | None]] = []
        for guest in engine.get_guests()[:2]:
            pid = await engine.async_person_id(guest)
            name = engine.display_name(guest) or guest
            guests.append((first_name(name) or name, pid))
        if not guests:
            guests = [(n, None) for n in self._names(room, stay, first=True)]
        upcoming = not stay.active
        portraits = [await self.async_portrait(pid) for _, pid in guests]
        signature = (
            tuple(guests),
            stay.label,
            room.config.name,
            upcoming,
            tuple(p.digest if p else None for p in portraits),
        )
        cached = self._door_cards.get(room_id)
        if cached and cached[0] == signature:
            return cached[1]
        faces = [
            (name, p.image if p else None, p.face if p else None)
            for (name, _), p in zip(guests, portraits, strict=True)
        ]
        body = await self.hass.async_add_executor_job(
            picpak_render.render_door, room.config.name, faces, stay.label, upcoming
        )
        # Un visage manquant sera retenté au prochain réveil.
        if all(p is not None for p in portraits):
            self._door_cards[room_id] = (signature, body)
        return body

    async def _async_render_asset(
        self, asset_id: str, person_ids: list[str]
    ) -> bytes | None:
        """Télécharge l'aperçu Immich et le convertit pour la dalle."""
        if asset_id in self._rendered:
            return self._rendered[asset_id]
        try:
            jpeg = await self._api.async_get_thumbnail(asset_id, size="preview")
        except ImmichApiError as err:
            _LOGGER.warning("PicPak : téléchargement de %s en échec: %s", asset_id, err)
            return None
        box = None
        try:
            faces = await self._api.async_get_faces(asset_id)
            box = framing.faces_box(faces, person_ids)
        except ImmichApiError as err:
            # Clé API sans la permission « face.read » : recadrage par défaut.
            _LOGGER.debug("PicPak : visages de %s indisponibles: %s", asset_id, err)
        try:
            body = await self.hass.async_add_executor_job(
                picpak_render.render_photo, jpeg, box
            )
        except Exception as err:  # noqa: BLE001 - image corrompue / format inconnu
            _LOGGER.warning("PicPak : rendu de %s impossible: %s", asset_id, err)
            return None
        self._remember(asset_id, body)
        return body

    def _remember(self, asset_id: str, body: bytes) -> None:
        """Cache borné : quelques images par cadre suffisent."""
        self._rendered[asset_id] = body
        limit = 4 * max(1, len(self.frames)) + 1
        while len(self._rendered) > limit:
            del self._rendered[next(iter(self._rendered))]

    def _render_task(self, asset_id: str, person_ids: list[str]) -> asyncio.Task:
        task = self._render_tasks.get(asset_id)
        if task is None or task.done():
            task = self.hass.async_create_background_task(
                self._async_render_asset(asset_id, person_ids),
                f"{DOMAIN} picpak render {asset_id}",
            )
            self._render_tasks[asset_id] = task
            task.add_done_callback(lambda _t, a=asset_id: self._render_tasks.pop(a, None))
        return task

    @callback
    def _prefetch(self, key: str) -> None:
        """Prépare l'image du prochain réveil de ``key``."""
        frame = self.frames.get(key)
        if frame is None or not frame.assets:
            return
        nxt = frame.assets[(frame.index + 1) % len(frame.assets)]
        if nxt not in self._rendered:
            self._render_task(nxt, frame.person_ids)

    async def _async_photo_body(
        self, key: str, frame: FrameState, *, wait: bool = True
    ) -> tuple[bytes, bool]:
        """Tampon de la photo courante ; ``(tampon, prêt)``.

        Si le rendu n'est pas prêt à temps, on renvoie l'image déjà affichée
        (le cadre ne rafraîchit pas) et on le fait revenir plus tôt.
        """
        if frame.index < 0 or frame.index >= len(frame.assets):
            frame_index = 0
        else:
            frame_index = frame.index
        deadline = RENDER_DEADLINE if wait else 30
        for attempt in range(min(MAX_ASSET_TRIES, len(frame.assets))):
            asset_id = frame.assets[(frame_index + attempt) % len(frame.assets)]
            task = self._render_task(asset_id, frame.person_ids)
            try:
                body = await asyncio.wait_for(asyncio.shield(task), deadline)
            except TimeoutError:
                _LOGGER.debug("PicPak %s : rendu de %s pas prêt à temps", key, asset_id)
                break
            if body is not None:
                if wait and attempt:
                    frame.index = (frame_index + attempt) % len(frame.assets)
                return body, True
            _LOGGER.debug("PicPak %s : essai %d en échec, photo suivante", key, attempt + 1)
        previous = self._last_body.get(key)
        if previous is not None:
            return previous, False
        _, slot = split_key(key)
        return await self._async_logo(slot, self.rooms[split_key(key)[0]].config.name), False

    # ------------------------------------------------------------------ #
    # Aperçu, entités
    # ------------------------------------------------------------------ #
    def _set_body(self, key: str, body: bytes) -> None:
        """Mémorise le tampon affiché par ``key`` (invalide l'aperçu)."""
        if self._last_body.get(key) != body:
            self._last_body[key] = body
            self._preview_png.pop(key, None)
            self._body_rev[key] = self._body_rev.get(key, 0) + 1

    def _invalidate_preview(self, key: str) -> None:
        """L'aperçu d'un cadre qui ne s'est jamais connecté doit être refait."""
        frame = self.frames.get(key)
        if frame is not None and frame.last_seen is None:
            self._last_body.pop(key, None)
            self._preview_png.pop(key, None)
            self._body_rev[key] = self._body_rev.get(key, 0) + 1
        self._notify(key)

    def body_revision(self, key: str) -> int:
        """Change à chaque nouveau contenu de dalle (pour l'entité image)."""
        return self._body_rev.get(key, 0)

    @callback
    def _notify(self, key: str) -> None:
        async_dispatcher_send(self.hass, SIGNAL_PICPAK_UPDATE.format(self.entry_id, key))

    async def async_preview(self, key: str) -> bytes | None:
        """PNG de l'écran du cadre : son dernier tampon reçu, ou à défaut
        (cadre pas encore connecté) l'image qu'il affichera."""
        if key not in self.frames:
            return None
        body = self._last_body.get(key)
        if body is None:
            try:
                body, _, _ = await self._async_compose(key, dt_util.now(), advance=False)
            except Exception as err:  # noqa: BLE001 - aperçu seulement
                _LOGGER.debug("PicPak %s : aperçu impossible: %s", key, err)
                return None
            self._last_body[key] = body
        if key not in self._preview_png:
            self._preview_png[key] = await self.hass.async_add_executor_job(
                picpak_render.preview_png, body
            )
        return self._preview_png[key]

    # ------------------------------------------------------------------ #
    # URL des cadres
    # ------------------------------------------------------------------ #
    def base_url(self, override: str | None = None) -> str | None:
        """URL locale de Home Assistant telle que le cadre doit l'appeler."""
        url = (override or self.settings.base_url or "").strip().rstrip("/")
        if url:
            return url
        try:
            return get_url(
                self.hass,
                allow_external=False,
                allow_cloud=False,
                allow_ip=True,
                prefer_external=False,
            ).rstrip("/")
        except NoURLAvailableError:
            return None

    def urls(self, override: str | None = None) -> list[dict[str, Any]]:
        """URL à saisir dans chaque cadre (commande SETURL)."""
        base = self.base_url(override)
        out: list[dict[str, Any]] = []
        for key, frame in self.frames.items():
            room_id, slot = split_key(key)
            path = PICPAK_URL_PATH.format(room_id=room_id, slot=slot)
            url = f"{base}{path}?k={self.token}" if base else None
            out.append(
                {
                    "room": room_id,
                    "room_name": self.rooms[room_id].config.name,
                    "slot": slot,
                    "emplacement": "porte" if slot == DOOR_SLOT else f"chambre n°{slot}",
                    "url": url,
                    "length": len(url) if url else None,
                    "too_long": bool(url) and len(url) > PICPAK_URL_MAX,
                    "last_seen": frame.last_seen,
                }
            )
        return out


class PicPakFrameView(HomeAssistantView):
    """Vue HTTP appelée par les cadres (sans authentification HA : jeton)."""

    url = PICPAK_URL_PATH
    name = "api:immich_guest_slideshow:picpak"
    requires_auth = False

    async def get(self, request: web.Request, room_id: str, slot: str) -> web.Response:
        """Renvoie le tampon d'affichage du cadre."""
        hass: HomeAssistant = request.app["hass"]
        token = request.query.get("k")
        manager: PicPakManager | None = None
        for runtime in hass.data.get(DOMAIN, {}).values():
            candidate = getattr(runtime, "picpak", None)
            if candidate is not None and candidate.check_token(token):
                manager = candidate
                break
        if manager is None:
            return web.Response(status=403)
        reply = await manager.async_wake(
            room_id,
            slot,
            battery=request.headers.get("X-Picpak-Battery"),
            wake=request.headers.get("X-Picpak-Wake"),
        )
        if reply is None:
            return web.Response(status=404)
        return web.Response(
            body=reply.body,
            content_type="application/octet-stream",
            headers={
                "X-Next-Wake-Seconds": str(reply.next_wake_s),
                "Cache-Control": "no-store",
            },
        )


class PicPakPortraitView(HomeAssistantView):
    """Portraits des invités pour les dashboards (jeton des cadres, comme les
    cadres eux-mêmes : une balise ``<img>`` ne peut pas s'authentifier)."""

    url = PORTRAIT_URL_PATH
    name = "api:immich_guest_slideshow:portrait"
    requires_auth = False

    async def get(self, request: web.Request, room_id: str, index: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        token = request.query.get("k")
        manager: PicPakManager | None = None
        for runtime in hass.data.get(DOMAIN, {}).values():
            candidate = getattr(runtime, "picpak", None)
            if candidate is not None and candidate.check_token(token):
                manager = candidate
                break
        if manager is None:
            return web.Response(status=403)
        if not index.isdigit():
            return web.Response(status=404)
        portrait = await manager.async_guest_portrait(room_id, int(index))
        if portrait is None:
            return web.Response(status=404)
        return web.Response(
            body=portrait.image,
            content_type=portrait.content_type,
            headers={"Cache-Control": "private, max-age=3600", "X-Portrait-Source": portrait.source},
        )


def frame_device_info(entry_id: str, manager: PicPakManager, key: str) -> DeviceInfo:
    """Appareil HA d'un cadre, rattaché à l'appareil de sa chambre."""
    room_id, slot = split_key(key)
    room_name = manager.rooms[room_id].config.name
    label = "porte" if slot == DOOR_SLOT else slot
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{room_id}_picpak_{slot}")},
        name=f"PicPak {room_name} {label}",
        manufacturer=PICPAK_MANUFACTURER,
        model=PICPAK_MODEL,
        via_device=(DOMAIN, f"{entry_id}_{room_id}"),
    )

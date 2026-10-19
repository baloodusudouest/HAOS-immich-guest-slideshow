"""Cadres e-ink PicPak alimentés par Home Assistant.

Chaque cadre (un par lit) tourne avec le firmware open-PicPak patché « tirage
Wi-Fi » : à chaque réveil il se connecte, télécharge sur
``/api/immich_guest_slideshow/picpak/<chambre>/<lit>?k=<jeton>`` une image déjà
rendue de 30 000 octets, l'affiche si elle a changé, puis se rendort pendant
la durée indiquée par l'en-tête ``X-Next-Wake-Seconds``.

Toute l'intelligence est ici, côté Home Assistant :

- **sélection** : 50 photos par cadre et par séjour, tirées d'Immich avec la
  même logique que les albums (un invité par lit ; un invité seul dans une
  chambre à deux cadres alimente les deux avec des photos différentes) ;
- **rotation** : la photo n'avance qu'une fois l'intervalle écoulé, ou sur un
  appui du bouton du cadre, ou via le service ``picpak_next``. Une requête
  entre-temps (nouvel essai après un échec Wi-Fi) renvoie la même image, que
  le cadre reconnaît et n'affiche pas : aucun rafraîchissement inutile ;
- **pause nocturne** facultative : pas de changement de photo la nuit, le
  cadre dort jusqu'à la fin de la pause ;
- **chambre libre** : le logo, statique, donc jamais ré-affiché ;
- **séjour pas encore commencé** (ou terminé) : le logo aussi. Les photos ne
  s'affichent que pendant le séjour, d'après le calendrier de la chambre
  (``calendar.<chambre>``) : du jour d'arrivée à l'heure d'arrivée (9 h par
  défaut) jusqu'à la fin du jour de départ. Sans calendrier, l'invité est
  montré dès qu'il est renseigné ;
- **rendu** : photo recadrée pour remplir l'écran, centrée sur le visage de
  l'invité (visages détectés par Immich), réduite aux 4 couleurs de la dalle
  (voir :mod:`.picpak_render`). Le rendu de la photo
  suivante est préparé à l'avance pour répondre au cadre en quelques
  millisecondes (son délai d'attente HTTP est de 15 s).

Le cadre transmet à chaque réveil sa batterie et la cause du réveil : elles
alimentent les capteurs de chaque cadre.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import picpak_render
from .api import ImmichApiClient, ImmichApiError
from .const import (
    DOMAIN,
    PICPAK_CALENDAR,
    PICPAK_URL_MAX,
    PICPAK_URL_PATH,
    SIGNAL_PICPAK_UPDATE,
)

if TYPE_CHECKING:
    from .slideshow import SlideshowEngine

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 15

# Délai au-delà duquel on répond au cadre sans attendre la fin du rendu.
RENDER_DEADLINE = 9.0
# Réveil anticipé quand le rendu n'était pas prêt à temps.
RETRY_SOON_S = 120
MIN_WAKE_S_FLOOR = 60
MAX_WAKE_S = 12 * 3600
# Nombre de photos essayées quand Immich échoue sur l'une d'elles.
MAX_ASSET_TRIES = 3

LOGO = "__logo__"

_QUIET_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_quiet(value: str | None) -> tuple[time, time] | None:
    """« 23:00-07:00 » -> (23:00, 07:00). Vide ou invalide -> ``None``."""
    match = _QUIET_RE.match(value or "")
    if not match:
        return None
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) == (h2, m2):
        return None
    return time(h1, m1), time(h2, m2)


def quiet_remaining(now: datetime, quiet: tuple[time, time] | None) -> float | None:
    """Secondes restantes de pause nocturne à ``now`` (heure locale), sinon ``None``.

    Gère les fenêtres qui passent minuit (23:00-07:00).
    """
    if quiet is None:
        return None
    start, end = quiet
    t = now.timetz().replace(tzinfo=None)
    if start < end:
        inside = start <= t < end
    else:
        inside = t >= start or t < end
    if not inside:
        return None
    end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= now:
        end_dt += timedelta(days=1)
    return (end_dt - now).total_seconds()


# --------------------------------------------------------------------------- #
# Période de séjour (calendrier de la chambre)
# --------------------------------------------------------------------------- #
CALENDAR_CACHE_S = 600
CALENDAR_LOOKAHEAD_DAYS = 30


def _as_local(value: date | datetime | str) -> tuple[date, bool]:
    """Date locale d'un début/fin d'événement ; ``True`` si journée entière."""
    if isinstance(value, str):
        if len(value) == 10:
            return date.fromisoformat(value), True
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            raise ValueError(value)
        value = parsed
    if isinstance(value, datetime):
        return dt_util.as_local(value).date(), False
    return value, True


def stay_window(
    start: date | datetime | str,
    end: date | datetime | str,
    arrival_hour: int,
) -> tuple[datetime, datetime]:
    """Fenêtre d'affichage des photos pour un séjour du calendrier.

    Du jour d'arrivée à ``arrival_hour`` jusqu'à la fin du jour de départ.
    Le jour de départ est la date de fin de l'événement : pour un événement
    « journée entière », la fin est exclusive et correspond à la date de
    départ (format des calendriers de réservation : une nuit du samedi =
    samedi → dimanche).
    """
    start_day, _ = _as_local(start)
    end_day, _ = _as_local(end)
    end_day = max(end_day, start_day)
    tz = dt_util.get_default_time_zone()
    win_start = datetime.combine(start_day, time(arrival_hour), tzinfo=tz)
    win_end = datetime.combine(end_day + timedelta(days=1), time(0), tzinfo=tz)
    return win_start, win_end


@dataclass(frozen=True)
class StayStatus:
    """Le séjour est-il en cours, et sinon quand commence le prochain ?"""

    active: bool
    next_start: datetime | None = None
    restricted: bool = True  # False : pas de calendrier, aucune restriction


NO_CALENDAR = StayStatus(active=True, restricted=False)


def stay_status(
    events: list[tuple[date | datetime | str, date | datetime | str]],
    now: datetime,
    arrival_hour: int,
) -> StayStatus:
    """Statut du séjour à ``now`` d'après les événements du calendrier."""
    upcoming: list[datetime] = []
    for start, end in events:
        try:
            win_start, win_end = stay_window(start, end, arrival_hour)
        except ValueError:
            continue
        if win_start <= now < win_end:
            return StayStatus(active=True)
        if win_start > now:
            upcoming.append(win_start)
    return StayStatus(active=False, next_start=min(upcoming) if upcoming else None)


def frame_key(room_id: str, bed: int) -> str:
    """Clé d'un cadre : « chambre_d_ami/1 »."""
    return f"{room_id}/{bed}"


@dataclass
class PicPakSettings:
    """Réglages des cadres (issus des options de l'intégration)."""

    interval_min: int
    photos: int
    quiet: tuple[time, time] | None
    logo_path: str
    title: str
    subtitle: str
    base_url: str
    arrival_hour: int = 9

    @property
    def interval_s(self) -> int:
        return max(60, int(self.interval_min) * 60)


@dataclass
class FrameState:
    """État persistant d'un cadre."""

    signature: str | None = None
    guest: str | None = None
    guest_name: str | None = None
    person_id: str | None = None
    assets: list[str] = field(default_factory=list)
    index: int = -1
    advanced_at: str | None = None
    force_next: bool = False
    last_seen: str | None = None
    battery_mv: int | None = None
    battery_pct: int | None = None
    wake: str | None = None
    next_wake_s: int | None = None
    shown: str | None = None  # asset affiché (ou LOGO)
    requests: int = 0
    in_stay: bool | None = None  # séjour en cours au dernier réveil

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameState":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        state = cls(**known)
        state.assets = [str(a) for a in (state.assets or [])]
        return state


@dataclass(frozen=True)
class FrameReply:
    """Réponse à un réveil de cadre."""

    body: bytes
    next_wake_s: int


class PicPakManager:
    """Gère tous les cadres d'une config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        api: ImmichApiClient,
        engines: dict[str, SlideshowEngine],
        settings: PicPakSettings,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._api = api
        self._engines = engines
        self.settings = settings
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.picpak.{entry_id}"
        )
        self.token: str = ""
        self.frames: dict[str, FrameState] = {}
        # Tampons rendus : asset_id (ou LOGO) -> 30 000 octets. Petit cache
        # partagé par tous les cadres, borné.
        self._rendered: dict[str, bytes] = {}
        self._render_tasks: dict[str, asyncio.Task[bytes | None]] = {}
        # Dernier tampon envoyé à chaque cadre (= ce qu'affiche la dalle).
        self._last_body: dict[str, bytes] = {}
        self._preview_png: dict[str, bytes] = {}
        self._body_rev: dict[str, int] = {}
        self._room_locks: dict[str, asyncio.Lock] = {}
        # Statut de séjour par chambre : (statut, instant du calcul).
        self._stays: dict[str, tuple[StayStatus, datetime]] = {}
        self._logo_sig: tuple | None = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    async def async_load(self) -> None:
        """Charge l'état persistant et crée les cadres manquants."""
        data = await self._store.async_load() or {}
        self.token = str(data.get("token") or "") or secrets.token_urlsafe(16)
        stored = data.get("frames") or {}
        self.frames = {}
        for room_id, engine in self._engines.items():
            for bed in range(1, len(engine.helpers) + 1):
                key = frame_key(room_id, bed)
                raw = stored.get(key)
                self.frames[key] = (
                    FrameState.from_dict(raw) if isinstance(raw, dict) else FrameState()
                )
        if not data.get("token"):
            self._schedule_save()

    @callback
    def async_start(self) -> None:
        """Prépare en arrière-plan les sélections et l'image de chaque cadre.

        Après un redémarrage, les tampons rendus sont perdus : on les refait
        pour que les entités image montrent tout de suite le contenu des
        cadres, sans attendre leur prochain réveil.
        """
        async def _warm() -> None:
            await self._async_logo()
            # Sélections à jour dès le démarrage (et non au premier réveil) :
            # l'aperçu d'un cadre pas encore réveillé montre ainsi la bonne
            # image, et la première requête du cadre répond sans attendre Immich.
            for room_id in list(self._engines):
                await self.async_ensure_room(room_id)
            for key, frame in self.frames.items():
                if frame.shown and frame.shown != LOGO:
                    body = await self._async_render_asset(frame.shown, frame.person_id)
                    if body is not None and key not in self._last_body:
                        self._set_body(key, body)
                elif frame.shown == LOGO and key not in self._last_body:
                    self._set_body(key, self._rendered[LOGO])
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
        return {
            "token": self.token,
            "frames": {k: f.as_dict() for k, f in self.frames.items()},
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def check_token(self, token: str | None) -> bool:
        """Comparaison à temps constant du jeton d'accès."""
        return bool(token) and hmac.compare_digest(str(token), self.token)

    def beds(self, room_id: str) -> int:
        engine = self._engines.get(room_id)
        return len(engine.helpers) if engine else 0

    # ------------------------------------------------------------------ #
    # Sélection des photos (par chambre)
    # ------------------------------------------------------------------ #
    def _room_signature(self, room_id: str) -> str:
        """Empreinte de l'occupation : invités par lit + taille demandée."""
        beds = self._engines[room_id].get_guests_by_bed()
        return "|".join(b or "" for b in beds) + f"#{self.settings.photos}"

    async def async_ensure_room(self, room_id: str, *, force: bool = False) -> None:
        """(Re)construit les sélections d'une chambre si l'occupation a changé."""
        engine = self._engines.get(room_id)
        if engine is None:
            return
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        async with lock:
            signature = self._room_signature(room_id)
            keys = [frame_key(room_id, b) for b in range(1, self.beds(room_id) + 1)]
            if not force and all(self.frames[k].signature == signature for k in keys):
                return
            if not any(engine.get_guests_by_bed()):
                plans: list[tuple[str | None, list[str]]] = [(None, []) for _ in keys]
            else:
                try:
                    plans = await engine.async_bed_playlists(
                        self.settings.photos, fill_empty=True
                    )
                except ImmichApiError as err:
                    _LOGGER.warning("[%s] PicPak : sélection en échec: %s", room_id, err)
                    return
            for key, (guest, assets) in zip(keys, plans, strict=False):
                frame = self.frames[key]
                person_id = await engine.async_person_id(guest) if guest else None
                frame.signature = signature
                frame.guest = guest
                frame.guest_name = engine.display_name(guest)
                frame.person_id = person_id
                frame.assets = list(assets)
                frame.index = -1  # première photo au prochain réveil
                frame.force_next = False
                _LOGGER.info(
                    "[%s] PicPak %s : %s, %d photo(s)",
                    room_id,
                    key,
                    frame.guest_name or "chambre libre (logo)",
                    len(assets),
                )
            self._schedule_save()
        for key in keys:
            if key not in self._last_body:
                # Pas encore de tampon reçu : l'aperçu montre l'image à venir,
                # qui vient de changer.
                self._body_rev[key] = self._body_rev.get(key, 0) + 1
            self._prefetch(key)
            self._notify(key)

    # ------------------------------------------------------------------ #
    # Séjour en cours ?
    # ------------------------------------------------------------------ #
    async def _async_calendar_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[str, str]]:
        """Événements du calendrier entre ``start`` et ``end``."""
        response = await self.hass.services.async_call(
            "calendar",
            "get_events",
            {"start_date_time": start, "end_date_time": end},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        events = ((response or {}).get(entity_id) or {}).get("events") or []
        return [
            (str(e["start"]), str(e["end"]))
            for e in events
            if isinstance(e, dict) and e.get("start") and e.get("end")
        ]

    async def async_stay(self, room_id: str, now: datetime | None = None) -> StayStatus:
        """Le séjour de la chambre est-il en cours (calendrier, mis en cache) ?"""
        now = now or dt_util.now()
        cached = self._stays.get(room_id)
        if cached is not None:
            status, at = cached
            fresh = (now - at).total_seconds() < CALENDAR_CACHE_S
            crossed = status.next_start is not None and now >= status.next_start
            if fresh and not crossed:
                return status
        entity_id = PICPAK_CALENDAR.format(room_id=room_id)
        if self.hass.states.get(entity_id) is None or not self.hass.services.has_service(
            "calendar", "get_events"
        ):
            status = NO_CALENDAR
        else:
            try:
                events = await self._async_calendar_events(
                    entity_id,
                    now - timedelta(days=CALENDAR_LOOKAHEAD_DAYS),
                    now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS),
                )
            except Exception as err:  # noqa: BLE001 - calendrier indisponible
                _LOGGER.warning("PicPak : calendrier %s illisible: %s", entity_id, err)
                return cached[0] if cached else NO_CALENDAR
            status = stay_status(events, now, self.settings.arrival_hour)
        self._stays[room_id] = (status, now)
        return status

    @callback
    def async_room_changed(self, room_id: str) -> None:
        """Un helper de la chambre a changé : nouvelle sélection en arrière-plan."""
        self._stays.pop(room_id, None)  # la réservation a pu changer aussi
        if room_id in self._engines:
            self.hass.async_create_background_task(
                self.async_ensure_room(room_id), f"{DOMAIN} picpak {room_id}"
            )

    def request_next(self, room_id: str | None = None, bed: int | None = None) -> int:
        """Demande le passage à la photo suivante au prochain réveil."""
        count = 0
        for key, frame in self.frames.items():
            r, b = key.rsplit("/", 1)
            if room_id and r != room_id:
                continue
            if bed and int(b) != bed:
                continue
            frame.force_next = True
            count += 1
        if count:
            self._schedule_save()
        return count

    # ------------------------------------------------------------------ #
    # Rendu
    # ------------------------------------------------------------------ #
    def _logo_signature(self) -> tuple:
        path = self.settings.logo_path
        try:
            mtime = os.path.getmtime(path) if path else None
        except OSError:
            mtime = None
        return (path, mtime, self.settings.title, self.settings.subtitle)

    async def _async_logo(self) -> bytes:
        sig = await self.hass.async_add_executor_job(self._logo_signature)
        if sig != self._logo_sig or LOGO not in self._rendered:
            self._rendered[LOGO] = await self.hass.async_add_executor_job(
                picpak_render.render_logo,
                self.settings.logo_path,
                self.settings.title,
                self.settings.subtitle,
            )
            self._logo_sig = sig
        return self._rendered[LOGO]

    async def _async_render_asset(
        self, asset_id: str, person_id: str | None
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
            box = picpak_render.face_box(faces, person_id)
        except ImmichApiError as err:
            # Clé API sans la permission « face.read » : recadrage centré.
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
            oldest = next(k for k in self._rendered if k != LOGO)
            del self._rendered[oldest]

    def _render_task(self, asset_id: str, person_id: str | None) -> asyncio.Task:
        task = self._render_tasks.get(asset_id)
        if task is None or task.done():
            task = self.hass.async_create_background_task(
                self._async_render_asset(asset_id, person_id),
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
            self._render_task(nxt, frame.person_id)

    # ------------------------------------------------------------------ #
    # Réveil d'un cadre
    # ------------------------------------------------------------------ #
    async def async_wake(
        self,
        room_id: str,
        bed: int,
        *,
        battery: str | None = None,
        wake: str | None = None,
    ) -> FrameReply | None:
        """Traite le réveil d'un cadre ; ``None`` si le cadre n'existe pas."""
        key = frame_key(room_id, bed)
        frame = self.frames.get(key)
        if frame is None:
            return None
        now = dt_util.now()
        self._record_telemetry(frame, now, battery, wake)

        await self.async_ensure_room(room_id)

        interval = self.settings.interval_s
        quiet_left = quiet_remaining(now, self.settings.quiet)
        next_wake = interval
        stay = await self.async_stay(room_id, now) if frame.assets else NO_CALENDAR
        frame.in_stay = stay.active if stay.restricted else None

        if not frame.assets or not stay.active:
            # Chambre libre, ou séjour pas encore commencé / terminé : le logo.
            body = await self._async_logo()
            frame.shown = LOGO
            if stay.next_start is not None:
                # Se réveiller juste après le début du séjour (9 h le jour J).
                until = (stay.next_start - now).total_seconds() + 60
                next_wake = int(min(next_wake, until))
        else:
            if self._should_advance(frame, now, interval, wake, quiet_left):
                frame.index = (frame.index + 1) % len(frame.assets)
                frame.advanced_at = now.isoformat()
                frame.force_next = False
            body, ready = await self._async_body_for(key, frame)
            if not ready:
                next_wake = RETRY_SOON_S
            elif quiet_left is not None:
                next_wake = int(quiet_left) + 60

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

    async def _async_body_for(
        self, key: str, frame: FrameState
    ) -> tuple[bytes, bool]:
        """Tampon de la photo courante ; ``(tampon, prêt)``.

        Si le rendu n'est pas prêt à temps, on renvoie l'image déjà affichée
        (le cadre ne rafraîchit pas) et on le fait revenir plus tôt.
        """
        for attempt in range(min(MAX_ASSET_TRIES, len(frame.assets))):
            asset_id = frame.assets[frame.index]
            task = self._render_task(asset_id, frame.person_id)
            try:
                body = await asyncio.wait_for(asyncio.shield(task), RENDER_DEADLINE)
            except TimeoutError:
                _LOGGER.debug("PicPak %s : rendu de %s pas prêt à temps", key, asset_id)
                previous = self._last_body.get(key)
                return (previous or await self._async_logo()), False
            if body is not None:
                frame.shown = asset_id
                return body, True
            # Photo inaccessible : on passe à la suivante.
            frame.index = (frame.index + 1) % len(frame.assets)
            _LOGGER.debug("PicPak %s : essai %d en échec, photo suivante", key, attempt + 1)
        previous = self._last_body.get(key)
        return (previous or await self._async_logo()), False

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

    def _set_body(self, key: str, body: bytes) -> None:
        """Mémorise le tampon affiché par ``key`` (invalide l'aperçu)."""
        if self._last_body.get(key) != body:
            self._last_body[key] = body
            self._preview_png.pop(key, None)
            self._body_rev[key] = self._body_rev.get(key, 0) + 1

    def body_revision(self, key: str) -> int:
        """Change à chaque nouveau contenu de dalle (pour l'entité image)."""
        return self._body_rev.get(key, 0)

    @callback
    def _notify(self, key: str) -> None:
        async_dispatcher_send(
            self.hass, SIGNAL_PICPAK_UPDATE.format(self.entry_id, key)
        )

    # ------------------------------------------------------------------ #
    # Aperçu et URL
    # ------------------------------------------------------------------ #
    def _upcoming_asset(self, frame: FrameState) -> str | None:
        """Photo que le cadre affichera (ou affiche) : utile avant son premier
        réveil, ou après un redémarrage de HA tant que le rendu n'est pas fait."""
        if not frame.assets:
            return None
        if frame.shown and frame.shown in frame.assets:
            return frame.shown
        if 0 <= frame.index < len(frame.assets):
            return frame.assets[frame.index]
        return frame.assets[0]

    async def async_preview(self, key: str) -> bytes | None:
        """PNG de l'écran du cadre.

        - Le cadre s'est déjà connecté : son dernier tampon reçu.
        - Sinon (cadre pas encore flashé, ou HA vient de redémarrer) : l'image
          qu'il affichera à son prochain réveil — la photo prévue, ou le logo
          si la chambre est libre.
        """
        frame = self.frames.get(key)
        if frame is None:
            return None
        body = self._last_body.get(key)
        if body is not None:
            if key not in self._preview_png:
                self._preview_png[key] = await self.hass.async_add_executor_job(
                    picpak_render.preview_png, body
                )
            return self._preview_png[key]

        asset_id = self._upcoming_asset(frame)
        cached = self._stays.get(key.rsplit("/", 1)[0])
        if cached is not None and not cached[0].active:
            asset_id = None  # hors séjour : le cadre affichera le logo
        source = asset_id or LOGO
        cache_key = f"upcoming:{source}"
        if cache_key in self._preview_png:
            return self._preview_png[cache_key]
        if asset_id is None:
            body = await self._async_logo()
        else:
            task = self._render_task(asset_id, frame.person_id)
            try:
                body = await asyncio.wait_for(asyncio.shield(task), 30)
            except TimeoutError:
                body = None
            if body is None:
                _LOGGER.debug("PicPak %s : aperçu de %s indisponible", key, asset_id)
                return None
        png = await self.hass.async_add_executor_job(picpak_render.preview_png, body)
        # Aperçus « à venir » : un par source, bornés comme les rendus.
        stale = [k for k in self._preview_png if k.startswith("upcoming:")]
        for k in stale[: max(0, len(stale) - 2 * len(self.frames))]:
            del self._preview_png[k]
        self._preview_png[cache_key] = png
        return png

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
            room_id, bed = key.rsplit("/", 1)
            path = PICPAK_URL_PATH.format(room_id=room_id, bed=bed)
            url = f"{base}{path}?k={self.token}" if base else None
            engine = self._engines[room_id]
            out.append(
                {
                    "room": room_id,
                    "room_name": engine.room_name,
                    "bed": int(bed),
                    "helper": engine.helpers[int(bed) - 1],
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

    async def get(self, request: web.Request, room_id: str, bed: str) -> web.Response:
        """Renvoie le tampon d'affichage du cadre."""
        hass: HomeAssistant = request.app["hass"]
        token = request.query.get("k")
        try:
            bed_no = int(bed)
        except ValueError:
            return web.Response(status=404)
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
            bed_no,
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


def frame_device_info(entry_id: str, manager: PicPakManager, key: str):
    """Appareil HA d'un cadre, rattaché à l'appareil de sa chambre."""
    from homeassistant.helpers.device_registry import DeviceInfo

    from .const import PICPAK_MANUFACTURER, PICPAK_MODEL

    room_id, bed = key.rsplit("/", 1)
    engine = manager._engines[room_id]  # noqa: SLF001 - même module fonctionnel
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{room_id}_picpak_{bed}")},
        name=f"PicPak {engine.room_name} {bed}",
        manufacturer=PICPAK_MANUFACTURER,
        model=PICPAK_MODEL,
        via_device=(DOMAIN, f"{entry_id}_{room_id}"),
    )

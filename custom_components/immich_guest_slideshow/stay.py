"""Séjours : lecture du calendrier d'une chambre et calendrier des réveils.

La partie pure (sans Home Assistant) calcule, à partir des événements du
calendrier :

- la **fenêtre d'un séjour** : du jour d'arrivée à l'heure d'arrivée (9 h par
  défaut) jusqu'au **lendemain du dernier jour à 00:01**. Un séjour saisi du
  samedi au dimanche est donc en cours de samedi 9 h à lundi 00:01 ;
- le **statut** de la chambre : séjour en cours, prochain séjour programmé
  (quelle que soit sa date), ou chambre libre ;
- le **nombre de photos** d'un cadre pour le séjour : une par intervalle de
  réveil, pause nocturne exclue.

:class:`StayTracker` lit le calendrier de la chambre via ``calendar.get_events``
et met le résultat en cache.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Fin d'un séjour : le lendemain du dernier jour, à cette heure.
STAY_END_TIME = time(0, 1)
CALENDAR_CACHE_S = 600
CALENDAR_LOOKBACK_DAYS = 30
CALENDAR_LOOKAHEAD_DAYS = 400  # « le prochain séjour, quelle que soit sa date »

_QUIET_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


# --------------------------------------------------------------------------- #
# Pause nocturne
# --------------------------------------------------------------------------- #
def parse_quiet(value: str | None) -> tuple[time, time] | None:
    """« 01:00-07:00 » -> (01:00, 07:00). Vide ou invalide -> ``None``."""
    match = _QUIET_RE.match(value or "")
    if not match:
        return None
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) == (h2, m2):
        return None
    return time(h1, m1), time(h2, m2)


def in_quiet(t: time, quiet: tuple[time, time] | None) -> bool:
    """``t`` (heure locale, sans fuseau) est-elle dans la pause nocturne ?"""
    if quiet is None:
        return False
    start, end = quiet
    if start < end:
        return start <= t < end
    return t >= start or t < end


def quiet_remaining(now: datetime, quiet: tuple[time, time] | None) -> float | None:
    """Secondes restantes de pause nocturne à ``now``, sinon ``None``.

    Gère les fenêtres qui passent minuit (23:00-07:00).
    """
    if quiet is None or not in_quiet(now.timetz().replace(tzinfo=None), quiet):
        return None
    end = quiet[1]
    end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= now:
        end_dt += timedelta(days=1)
    return (end_dt - now).total_seconds()


# --------------------------------------------------------------------------- #
# Fenêtre d'un séjour
# --------------------------------------------------------------------------- #
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


def stay_days(
    start: date | datetime | str, end: date | datetime | str
) -> tuple[date, date]:
    """Jour d'arrivée et dernier jour couvert par un événement.

    Un événement « journée entière » a une fin exclusive dans Home Assistant
    (samedi → dimanche inclus est stocké « samedi → lundi ») : le dernier jour
    couvert est la veille de cette fin. Un événement avec heures couvre le
    jour de sa fin.
    """
    start_day, _ = _as_local(start)
    end_day, all_day = _as_local(end)
    if all_day:
        end_day -= timedelta(days=1)
    return start_day, max(end_day, start_day)


def stay_window(
    start: date | datetime | str,
    end: date | datetime | str,
    arrival_hour: int,
) -> tuple[datetime, datetime]:
    """Fenêtre d'un séjour : jour d'arrivée à ``arrival_hour`` → lendemain du
    dernier jour à 00:01."""
    first, last = stay_days(start, end)
    tz = dt_util.get_default_time_zone()
    return (
        datetime.combine(first, time(arrival_hour), tzinfo=tz),
        datetime.combine(last + timedelta(days=1), STAY_END_TIME, tzinfo=tz),
    )


_JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_MOIS = (
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
)


def format_stay(first: date, last: date) -> str:
    """« ven. 23 → sam. 24 oct. », « ven. 30 oct. → lun. 2 nov. », « sam. 24 oct. »."""

    def day(d: date) -> str:
        return f"{_JOURS[d.weekday()]} {d.day}"

    if first == last:
        return f"{day(first)} {_MOIS[first.month - 1]}"
    if first.month == last.month:
        return f"{day(first)} → {day(last)} {_MOIS[last.month - 1]}"
    return f"{day(first)} {_MOIS[first.month - 1]} → {day(last)} {_MOIS[last.month - 1]}"


# --------------------------------------------------------------------------- #
# Statut de la chambre
# --------------------------------------------------------------------------- #
FREE = "libre"
PLANNED = "programme"
ONGOING = "en_cours"


@dataclass(frozen=True)
class StayStatus:
    """Séjour en cours, prochain séjour programmé, ou chambre libre.

    ``days`` / ``window`` / ``names`` décrivent le séjour en cours, ou à défaut
    le prochain séjour programmé. ``names`` : titres des événements (un
    événement par invité, ou un titre commun).
    """

    active: bool
    restricted: bool = True  # False : pas de calendrier, aucune restriction
    days: tuple[date, date] | None = None
    window: tuple[datetime, datetime] | None = None
    names: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """« en_cours », « programme » ou « libre »."""
        if self.active:
            return ONGOING
        return PLANNED if self.days is not None else FREE

    @property
    def next_start(self) -> datetime | None:
        """Début du prochain séjour (s'il n'est pas déjà en cours)."""
        return None if self.active or self.window is None else self.window[0]

    @property
    def label(self) -> str:
        """Dates lisibles du séjour (« ven. 23 → sam. 24 oct. »), sinon ``""``."""
        return format_stay(*self.days) if self.days else ""


# Sans calendrier : l'invité est montré dès qu'il est renseigné.
NO_CALENDAR = StayStatus(active=True, restricted=False)


def stay_status(
    events: list[tuple],
    now: datetime,
    arrival_hour: int,
    closed_at: datetime | None = None,
) -> StayStatus:
    """Statut de la chambre à ``now`` d'après les événements du calendrier.

    ``events`` : ``(début, fin)`` ou ``(début, fin, titre)``. Les événements
    qui commencent le même jour forment un même séjour (un événement par
    invité). ``closed_at`` : instant d'une réinitialisation de la maison ;
    tout séjour déjà commencé à cet instant est considéré comme terminé.
    """
    current: list[tuple[date, date, datetime, datetime, str]] = []
    upcoming: list[tuple[date, date, datetime, datetime, str]] = []
    for event in events:
        title = str(event[2]).strip() if len(event) > 2 and event[2] else ""
        try:
            first, last = stay_days(event[0], event[1])
            win_start, win_end = stay_window(event[0], event[1], arrival_hour)
        except ValueError:
            continue
        if closed_at is not None and win_start <= closed_at:
            continue
        item = (first, last, win_start, win_end, title)
        if win_start <= now < win_end:
            current.append(item)
        elif win_start > now:
            upcoming.append(item)

    def _status(group: list, active: bool) -> StayStatus:
        return StayStatus(
            active=active,
            days=(min(g[0] for g in group), max(g[1] for g in group)),
            window=(min(g[2] for g in group), max(g[3] for g in group)),
            names=tuple(dict.fromkeys(g[4] for g in group if g[4])),
        )

    if current:
        return _status(current, True)
    if not upcoming:
        return StayStatus(active=False)
    first_day = min(u[0] for u in upcoming)
    return _status([u for u in upcoming if u[0] == first_day], False)


def frame_photo_count(
    window: tuple[datetime, datetime],
    interval_s: int,
    quiet: tuple[time, time] | None,
    maximum: int,
) -> int:
    """Photos nécessaires pour un séjour : une par réveil hors pause nocturne.

    Ex. séjour du samedi 9 h au lundi 00:01, une photo par heure, pause de
    01:00 à 07:00 : 39 heures moins 6 heures de pause = 33 photos.
    """
    start, end = window
    step = timedelta(seconds=max(60, interval_s))
    count = 0
    t = start
    while t < end and count < maximum:
        if not in_quiet(dt_util.as_local(t).time(), quiet):
            count += 1
        t += step
    return max(1, count)


# --------------------------------------------------------------------------- #
# Lecture du calendrier (Home Assistant)
# --------------------------------------------------------------------------- #
class StayTracker:
    """Statut de séjour d'une chambre, d'après son calendrier (mis en cache)."""

    def __init__(
        self,
        hass: HomeAssistant,
        calendar: str | None,
        arrival_hour: int,
        closed_at: Callable[[], datetime | None],
        closed_without_calendar: Callable[[], bool],
    ) -> None:
        """
        Args:
            calendar: ``calendar.xxx`` de la chambre (``None`` : pas de calendrier).
            closed_at: instant de la dernière réinitialisation de la maison.
            closed_without_calendar: chambre sans calendrier réinitialisée
                (pas d'invité montré jusqu'au prochain changement d'invité).
        """
        self._hass = hass
        self.calendar = calendar
        self._arrival_hour = arrival_hour
        self._closed_at = closed_at
        self._closed_without_calendar = closed_without_calendar
        self._cached: tuple[StayStatus, datetime] | None = None

    def invalidate(self) -> None:
        """Oublie le statut en cache (réservation ou invités modifiés)."""
        self._cached = None

    @property
    def cached(self) -> StayStatus | None:
        """Dernier statut connu, sans lire le calendrier."""
        return self._cached[0] if self._cached else None

    async def _async_events(
        self, start: datetime, end: datetime
    ) -> list[tuple[str, str, str]]:
        entity_id = self.calendar
        response = await self._hass.services.async_call(
            "calendar",
            "get_events",
            {"start_date_time": start, "end_date_time": end},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        events = ((response or {}).get(entity_id) or {}).get("events") or []
        return [
            (str(e["start"]), str(e["end"]), str(e.get("summary") or ""))
            for e in events
            if isinstance(e, dict) and e.get("start") and e.get("end")
        ]

    async def async_status(self, now: datetime | None = None) -> StayStatus:
        """Statut à ``now`` (calendrier relu au plus toutes les 10 minutes,
        et dès qu'un séjour commence ou se termine)."""
        now = now or dt_util.now()
        if self._cached is not None:
            status, at = self._cached
            fresh = (now - at).total_seconds() < CALENDAR_CACHE_S
            crossed = status.window is not None and (
                (status.next_start is not None and now >= status.next_start)
                or (status.active and now >= status.window[1])
            )
            if fresh and not crossed:
                return status
        if self.calendar and (
            self._hass.states.get(self.calendar) is None
            or not self._hass.services.has_service("calendar", "get_events")
        ):
            # Calendrier configuré mais pas (encore) chargé — typiquement au
            # démarrage de HA : pas de mise en cache, on relira au prochain appel.
            # En attendant : chambre considérée libre (jamais un faux séjour).
            if self._cached is not None:
                return self._cached[0]
            return StayStatus(active=False)
        if not self.calendar:
            status = (
                StayStatus(active=False, restricted=False)
                if self._closed_without_calendar()
                else NO_CALENDAR
            )
        else:
            try:
                events = await self._async_events(
                    now - timedelta(days=CALENDAR_LOOKBACK_DAYS),
                    now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS),
                )
            except Exception as err:  # noqa: BLE001 - calendrier indisponible
                _LOGGER.warning("Calendrier %s illisible: %s", self.calendar, err)
                return self._cached[0] if self._cached else NO_CALENDAR
            status = stay_status(events, now, self._arrival_hour, self._closed_at())
        self._cached = (status, now)
        return status

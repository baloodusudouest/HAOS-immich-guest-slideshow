"""Client de Carnet (contacts, NAS) : portraits HD des invités.

Carnet choisit pour chaque contact lié à Immich la meilleure photo de son
visage (grande, nette, de face) et la découpe en portrait carré « tête et
épaules » (800 px). Il la sert à Home Assistant sur
``GET /ha/portrait?immich=<uuid>&cadre=centre`` (jeton
``Authorization: Bearer``) : carré découpé dans la photo d'origine avec le
visage entier au milieu (losange du cadre de porte, avatars ronds), et le
cadre du visage dans l'en-tête ``X-Visage: x1,y1,x2,y2`` (de 0 à 1).

404 = pas de portrait HD pour cette personne : l'intégration reprend alors la
vignette Immich, comme sans Carnet.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
PORTRAIT_MAX_BYTES = 2_000_000  # un portrait Carnet fait ~100 à 200 Ko

FaceBox = tuple[float, float, float, float]


class CarnetError(Exception):
    """Carnet injoignable ou réponse inattendue."""


class CarnetAuthError(CarnetError):
    """Jeton refusé (401) ou non défini dans la stack de Carnet (503)."""


def clean_token(value: str | None) -> str:
    """Jeton saisi avec ou sans « Bearer » devant (secrets.yaml le contient
    souvent avec le préfixe)."""
    token = (value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def parse_face_box(value: str | None) -> FaceBox | None:
    """En-tête ``X-Visage`` -> (x1, y1, x2, y2) entre 0 et 1, ou ``None``."""
    if not value:
        return None
    try:
        parts = [float(p) for p in value.split(",")]
    except ValueError:
        return None
    if len(parts) != 4:
        return None
    x1, y1, x2, y2 = (min(max(p, 0.0), 1.0) for p in parts)
    if x2 - x1 <= 0.01 or y2 - y1 <= 0.01:
        return None
    return (x1, y1, x2, y2)


class CarnetClient:
    """Accès en lecture seule aux adresses ``/ha/…`` de Carnet."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.strip().rstrip("/")
        self._token = clean_token(token)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def async_validate(self) -> None:
        """Vérifie l'adresse et le jeton (``/ha/personnes``, déjà utilisé par
        la carte de réservation)."""
        try:
            async with self._session.get(
                f"{self.url}/ha/personnes", headers=self._headers, timeout=TIMEOUT
            ) as resp:
                if resp.status in (401, 503):
                    raise CarnetAuthError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise CarnetError(f"HTTP {resp.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CarnetError(str(err) or type(err).__name__) from err

    async def async_get_portrait(self, person_id: str) -> tuple[bytes, FaceBox | None] | None:
        """Portrait HD de la personne Immich, ``None`` si Carnet n'en a pas."""
        try:
            async with self._session.get(
                f"{self.url}/ha/portrait",
                params={"immich": person_id, "cadre": "centre"},
                headers=self._headers,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status in (401, 503):
                    raise CarnetAuthError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise CarnetError(f"HTTP {resp.status}")
                content_type = resp.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    raise CarnetError(f"type inattendu {content_type or '?'}")
                body = await resp.read()
                if not body or len(body) > PORTRAIT_MAX_BYTES:
                    raise CarnetError("portrait vide ou trop lourd")
                return body, parse_face_box(resp.headers.get("X-Visage"))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CarnetError(str(err) or type(err).__name__) from err

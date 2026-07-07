"""Client asynchrone pour l'API Immich (v1.x / v3.x, testé avec Immich 3.0.1).

La clé API n'est jamais stockée en dur : elle est fournie par le Config Flow
et injectée dans ce client au moment de l'initialisation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ImmichApiError(Exception):
    """Erreur générique de l'API Immich."""


class ImmichAuthError(ImmichApiError):
    """Clé API invalide ou non autorisée."""


class ImmichConnectionError(ImmichApiError):
    """Impossible de joindre le serveur Immich."""


class ImmichApiClient:
    """Client HTTP minimaliste et typé pour Immich."""

    def __init__(
        self,
        url: str,
        api_key: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialise le client.

        Args:
            url: URL de base d'Immich, ex. ``http://192.168.1.100:2283``.
            api_key: Clé API Immich (fournie via le Config Flow).
            session: Session aiohttp partagée fournie par Home Assistant.
        """
        self._base_url = url.rstrip("/")
        self._session = session
        self._headers = {
            "x-api-key": api_key,
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        """Effectue une requête HTTP et gère les erreurs communes."""
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers,
                json=json,
                params=params,
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise ImmichAuthError(f"Authentification refusée ({resp.status})")
                if resp.status >= 400:
                    body = await resp.text()
                    raise ImmichApiError(
                        f"Erreur API {resp.status} sur {path}: {body[:200]}"
                    )
                if raw:
                    return await resp.read()
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ImmichConnectionError(f"Connexion à Immich impossible: {err}") from err

    async def async_validate_connection(self) -> dict[str, Any]:
        """Valide l'URL et la clé API.

        Returns:
            Les informations serveur (version, etc.).
        """
        # /api/server/ping ne nécessite pas d'auth : on valide donc la clé
        # avec un endpoint authentifié.
        await self._request("GET", "/api/server/ping")
        return await self._request("GET", "/api/server/about")

    async def async_find_person(self, full_name: str) -> dict[str, Any] | None:
        """Recherche une personne par son nom complet (insensible à la casse).

        Returns:
            Le dictionnaire de la personne Immich, ou ``None`` si introuvable.
        """
        results: list[dict[str, Any]] = await self._request(
            "GET", "/api/search/person", params={"name": full_name}
        )
        wanted = full_name.strip().casefold()
        for person in results or []:
            if str(person.get("name", "")).strip().casefold() == wanted:
                return person
        # Repli : premier résultat approchant
        return results[0] if results else None

    async def async_search_assets(
        self,
        person_ids: list[str],
        *,
        limit: int = 400,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Retourne les photos contenant TOUTES les personnes de ``person_ids``.

        La pagination Immich est suivie via ``assets.nextPage`` jusqu'à
        atteindre ``limit`` résultats ou la fin des pages.
        """
        items: list[dict[str, Any]] = []
        page: int | None = 1
        while page is not None and len(items) < limit:
            payload: dict[str, Any] = {
                "personIds": person_ids,
                "type": "IMAGE",
                "page": page,
                "size": min(page_size, limit - len(items)),
                "withExif": False,
            }
            data = await self._request("POST", "/api/search/metadata", json=payload)
            assets = data.get("assets", {})
            items.extend(assets.get("items", []))
            next_page = assets.get("nextPage")
            page = int(next_page) if next_page else None
        return items[:limit]

    async def async_get_thumbnail(
        self, asset_id: str, *, size: str = "preview"
    ) -> bytes:
        """Récupère les octets JPEG de la miniature d'un asset."""
        return await self._request(
            "GET",
            f"/api/assets/{asset_id}/thumbnail",
            params={"size": size},
            raw=True,
        )

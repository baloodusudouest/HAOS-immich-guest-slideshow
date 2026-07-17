"""Client asynchrone pour l'API Immich (v1.x / v3.x, testé avec Immich 3.0.1).

La clé API n'est jamais stockée en dur : elle est fournie par le Config Flow
et injectée dans ce client au moment de l'initialisation.
"""
from __future__ import annotations

import asyncio
import logging
import unicodedata
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


def normalize_name(value: str) -> str:
    """Normalise un nom en forme NFC, sans espaces superflus.

    Les noms saisis depuis iOS/macOS arrivent souvent en forme NFD
    (« é » = « e » + accent combinant) alors qu'Immich stocke en NFC.
    Sans cette normalisation, « Chloé » (NFD) != « Chloé » (NFC).
    """
    return unicodedata.normalize("NFC", value).strip()


def _fold(value: str) -> str:
    """Clé de comparaison insensible à la casse (mais accents conservés)."""
    return normalize_name(value).casefold()


def _fold_no_accents(value: str) -> str:
    """Clé de comparaison insensible à la casse ET aux accents.

    Permet à « Chloe » de correspondre à « Chloé » en dernier recours.
    """
    decomposed = unicodedata.normalize("NFD", _fold(value))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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

    async def _search_person_raw(self, name: str) -> list[dict[str, Any]]:
        """Interroge /api/search/person et retourne la liste brute."""
        results = await self._request(
            "GET", "/api/search/person", params={"name": name}
        )
        return results or []

    async def async_find_person(self, full_name: str) -> dict[str, Any] | None:
        """Recherche une personne par son nom complet.

        La comparaison est insensible à la casse et robuste aux accents :

        1. Le nom est normalisé en NFC avant l'appel API (les saisies iOS
           arrivent en NFD et ne matchent sinon jamais côté Immich).
        2. Correspondance exacte (NFC + casefold).
        3. Correspondance sans accents (« Chloe » ↔ « Chloé »).
        4. Si la recherche accentuée ne renvoie rien, nouvel essai avec le
           nom désaccentué (l'index de recherche Immich peut être strict).
        5. Repli final : premier résultat approchant.

        Returns:
            Le dictionnaire de la personne Immich, ou ``None`` si introuvable.
        """
        query = normalize_name(full_name)
        results = await self._search_person_raw(query)

        # Nouvel essai sans accents si la recherche stricte ne donne rien.
        folded_query = _fold_no_accents(query)
        if not results and folded_query != _fold(query):
            _LOGGER.debug(
                "Recherche Immich vide pour '%s', nouvel essai sans accents", query
            )
            results = await self._search_person_raw(folded_query)

        if not results:
            return None

        wanted = _fold(query)
        wanted_no_accents = folded_query

        # 1) Correspondance exacte (accents inclus)
        for person in results:
            if _fold(str(person.get("name", ""))) == wanted:
                return person
        # 2) Correspondance insensible aux accents
        for person in results:
            if _fold_no_accents(str(person.get("name", ""))) == wanted_no_accents:
                return person
        # 3) Repli : premier résultat approchant
        return results[0]

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

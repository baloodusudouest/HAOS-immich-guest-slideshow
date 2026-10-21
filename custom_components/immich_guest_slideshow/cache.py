"""Cache local de photos pour une chambre.

Le cache stocke des identifiants d'assets Immich (et le libellé de la
recherche dont ils proviennent) afin de ne pas interroger Immich à chaque
changement d'image. La rotation est aléatoire et évite les doublons
immédiats grâce à un historique récent.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CachedPhoto:
    """Une photo mise en cache."""

    asset_id: str
    search_label: str


@dataclass
class PhotoCache:
    """Cache borné, mélangé une fois, parcouru dans l'ordre.

    L'ordre est tiré au hasard à chaque remplissage, puis la rotation avance
    (ou recule, bouton « précédente ») d'une photo à la fois : aucune photo
    ne revient avant que toutes aient été vues, et « précédente » ramène
    exactement la photo d'avant.
    """

    max_size: int = 50
    _photos: list[CachedPhoto] = field(default_factory=list)
    _pos: int = -1

    def __len__(self) -> int:
        return len(self._photos)

    @property
    def is_empty(self) -> bool:
        """Vrai si le cache ne contient aucune photo."""
        return not self._photos

    def replace(self, photos: list[CachedPhoto]) -> None:
        """Remplace intégralement le contenu du cache (dédupliqué, borné)."""
        seen: set[str] = set()
        unique: list[CachedPhoto] = []
        for photo in photos:
            if photo.asset_id not in seen:
                seen.add(photo.asset_id)
                unique.append(photo)
        random.shuffle(unique)
        self._photos = unique[: self.max_size]
        self._pos = -1

    def clear(self) -> None:
        """Vide le cache."""
        self._photos.clear()
        self._pos = -1

    def _step(self, delta: int) -> CachedPhoto | None:
        if not self._photos:
            return None
        if self._pos < 0:
            self._pos = 0 if delta > 0 else len(self._photos) - 1
        else:
            self._pos = (self._pos + delta) % len(self._photos)
        return self._photos[self._pos]

    def next_photo(self) -> CachedPhoto | None:
        """Photo suivante (après la dernière : retour à la première)."""
        return self._step(1)

    def previous_photo(self) -> CachedPhoto | None:
        """Photo précédente."""
        return self._step(-1)

"""Cache local de photos pour une chambre.

Le cache stocke des identifiants d'assets Immich (et le libellé de la
recherche dont ils proviennent) afin de ne pas interroger Immich à chaque
changement d'image. La rotation est aléatoire et évite les doublons
immédiats grâce à un historique récent.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CachedPhoto:
    """Une photo mise en cache."""

    asset_id: str
    search_label: str


@dataclass
class PhotoCache:
    """Cache borné avec rotation aléatoire sans doublon immédiat."""

    max_size: int = 50
    _photos: list[CachedPhoto] = field(default_factory=list)
    _recent: deque[str] = field(default_factory=lambda: deque(maxlen=5))

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
        self._recent.clear()

    def clear(self) -> None:
        """Vide le cache."""
        self._photos.clear()
        self._recent.clear()

    def next_photo(self) -> CachedPhoto | None:
        """Retourne une photo aléatoire, en évitant les dernières affichées."""
        if not self._photos:
            return None
        candidates = [p for p in self._photos if p.asset_id not in self._recent]
        if not candidates:  # toutes récemment vues -> on repart de zéro
            self._recent.clear()
            candidates = self._photos
        photo = random.choice(candidates)
        self._recent.append(photo.asset_id)
        return photo

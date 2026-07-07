"""Tests du cache local de photos."""
from custom_components.immich_guest_slideshow.cache import CachedPhoto, PhotoCache


def _photos(n: int) -> list[CachedPhoto]:
    return [CachedPhoto(asset_id=f"id{i}", search_label="s") for i in range(n)]


def test_replace_deduplicates_and_bounds() -> None:
    """Le cache déduplique et respecte sa taille maximale."""
    cache = PhotoCache(max_size=3)
    cache.replace(_photos(5) + _photos(5))
    assert len(cache) == 3


def test_next_photo_avoids_immediate_repeat() -> None:
    """Deux tirages consécutifs ne retournent jamais la même photo."""
    cache = PhotoCache(max_size=10)
    cache.replace(_photos(10))
    previous = cache.next_photo()
    for _ in range(50):
        current = cache.next_photo()
        assert current is not None
        assert current.asset_id != previous.asset_id
        previous = current


def test_empty_cache_returns_none() -> None:
    """Cache vide -> None (entités unavailable)."""
    cache = PhotoCache()
    assert cache.next_photo() is None
    assert cache.is_empty

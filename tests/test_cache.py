"""Tests du cache local de photos (ordre mélangé, suivante / précédente)."""
from custom_components.immich_guest_slideshow.cache import CachedPhoto, PhotoCache


def _photos(n: int) -> list[CachedPhoto]:
    return [CachedPhoto(asset_id=f"id{i}", search_label="s") for i in range(n)]


def test_replace_deduplicates_and_bounds() -> None:
    cache = PhotoCache(max_size=3)
    cache.replace(_photos(5) + _photos(5))
    assert len(cache) == 3


def test_every_photo_before_any_repeat() -> None:
    cache = PhotoCache(max_size=10)
    cache.replace(_photos(10))
    first = [cache.next_photo().asset_id for _ in range(10)]
    assert sorted(first) == sorted(f"id{i}" for i in range(10))
    assert cache.next_photo().asset_id == first[0]  # nouveau tour


def test_previous_returns_the_photo_before() -> None:
    cache = PhotoCache(max_size=10)
    cache.replace(_photos(5))
    a = cache.next_photo()
    b = cache.next_photo()
    assert b.asset_id != a.asset_id
    assert cache.previous_photo().asset_id == a.asset_id
    assert cache.next_photo().asset_id == b.asset_id
    # Avant la première : la dernière.
    cache.replace(_photos(3))
    last = cache.previous_photo()
    assert last is not None and cache.next_photo().asset_id != last.asset_id


def test_empty_cache_returns_none() -> None:
    cache = PhotoCache()
    assert cache.next_photo() is None and cache.previous_photo() is None
    assert cache.is_empty

"""Sélection par niveaux : propriétaires, puis invités ensemble, puis seuls."""
from __future__ import annotations

import asyncio
from typing import Any

from custom_components.immich_guest_slideshow.slideshow import (
    SlideshowEngine,
    deal,
    interleave,
)

OWNERS = ["Julien", "Yves"]


class FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class FakeHass:
    def __init__(self, values: dict[str, str]) -> None:
        self.states = self
        self._values = values

    def get(self, entity_id: str) -> FakeState | None:
        value = self._values.get(entity_id)
        return FakeState(value) if value is not None else None


class FakeImmich:
    """Photos : identifiant -> ensemble des personnes présentes.

    Comme Immich, une recherche renvoie les photos contenant AU MOINS toutes
    les personnes demandées.
    """

    def __init__(self, photos: dict[str, set[str]]) -> None:
        self.photos = photos
        self.searches: list[frozenset[str]] = []

    async def async_find_person(self, name: str) -> dict[str, Any]:
        return {"id": name, "name": name}

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        wanted = set(person_ids)
        self.searches.append(frozenset(wanted))
        return [{"id": k} for k, people in self.photos.items() if wanted <= people]


def _engine(api: FakeImmich, beds: list[str], size: int = 100) -> SlideshowEngine:
    helpers = [f"input_text.lit{i + 1}" for i in range(len(beds))]
    return SlideshowEngine(
        FakeHass(dict(zip(helpers, beds, strict=True))),  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_ami",
        room_name="Chambre d'ami",
        helpers=helpers,
        permanents=OWNERS,
        cache_size=size,
    )


def _photos(prefix: str, n: int, people: set[str]) -> dict[str, set[str]]:
    return {f"{prefix}{i}": set(people) for i in range(n)}


# --------------------------------------------------------------------------- #
# Briques pures
# --------------------------------------------------------------------------- #
def test_interleave_alternates_without_duplicates() -> None:
    assert interleave([["a1", "a2", "x"], ["b1", "x"]]) == ["a1", "b1", "a2", "x"]


def test_deal_shares_common_photos_without_duplicates() -> None:
    picked: list[list[str]] = [[], []]
    used: set[str] = set()
    common = ["c1", "c2", "c3", "c4"]
    deal([common, common], picked, used, size=10)
    assert picked == [["c1", "c3"], ["c2", "c4"]]


def test_deal_respects_size() -> None:
    picked: list[list[str]] = [["x"], []]
    used = {"x"}
    deal([["a", "b"], ["c", "d", "e"]], picked, used, size=2)
    assert picked == [["x", "a"], ["c", "d"]]


# --------------------------------------------------------------------------- #
# Cadres : un invité par cadre, sans doublon
# --------------------------------------------------------------------------- #
def _two_guests_library() -> dict[str, set[str]]:
    lib: dict[str, set[str]] = {}
    lib |= _photos("A+J+Y", 1, {"Alice", "Julien", "Yves"})
    lib |= _photos("A+J", 3, {"Alice", "Julien"})
    lib |= _photos("A+Y", 2, {"Alice", "Yves"})
    lib |= _photos("B+Y", 2, {"Bob", "Yves"})
    lib |= _photos("A+B", 6, {"Alice", "Bob"})
    lib |= _photos("A", 10, {"Alice"})
    lib |= _photos("B", 10, {"Bob"})
    return lib


def _tier(asset: str) -> int:
    """Niveau de priorité d'une photo de la bibliothèque de test."""
    for level, prefix in enumerate(("A+J+Y", "A+J", "A+Y", "B+Y", "A+B")):
        if asset.startswith(prefix + "#") or asset.rstrip("0123456789") == prefix:
            return {0: 0, 1: 1, 2: 2, 3: 2, 4: 3}[level]
    return 4


def test_two_guests_one_per_frame_priority_and_no_duplicates() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", "Bob"])

    alice, bob = asyncio.run(engine.async_frame_playlists([["Alice"], ["Bob"]], 10))

    assert not set(alice) & set(bob)  # jamais la même photo sur les 2 cadres
    assert len(alice) == 10 and len(bob) == 10
    # Alice : avec Julien et Yves, puis avec Julien, puis avec Yves…
    assert alice[0].startswith("A+J+Y")
    assert [_tier(a) for a in alice] == sorted(_tier(a) for a in alice)
    assert {a for a in alice[:6]} == {a for a in alice if a.startswith(("A+J", "A+Y"))}
    # Les 6 photos « ensemble » sont réparties entre les deux cadres.
    together = [a for a in alice + bob if a.startswith("A+B")]
    assert len(together) == 6
    assert any(a.startswith("A+B") for a in alice) and any(a.startswith("A+B") for a in bob)


def test_bob_order_owner_then_together_then_alone() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", "Bob"])

    _, bob = asyncio.run(engine.async_frame_playlists([["Alice"], ["Bob"]], 10))

    tiers = [
        0 if a.startswith("B+Y") else 1 if a.startswith("A+B") else 2 for a in bob
    ]
    assert tiers == sorted(tiers)
    assert tiers.count(0) == 2


def test_single_guest_two_frames_disjoint() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", ""])

    f1, f2 = asyncio.run(engine.async_frame_playlists([["Alice"], ["Alice"]], 5))

    assert not set(f1) & set(f2)
    assert len(f1) == len(f2) == 5
    own = [a for a in f1 + f2 if a.startswith(("A+J", "A+Y"))]
    assert len(own) == 6  # toutes ses photos avec les propriétaires utilisées


def test_one_frame_two_guests_mixed() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", "Bob"])

    (mixed,) = asyncio.run(engine.async_frame_playlists([["Alice", "Bob"]], 12))

    assert any(a.startswith("A") and not a.startswith("A+B") for a in mixed)
    assert any(a.startswith("B") for a in mixed)
    assert len(mixed) == len(set(mixed)) == 12


def test_target_reached_with_owners_skips_other_searches() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", ""])

    f1, f2 = asyncio.run(engine.async_frame_playlists([["Alice"], ["Alice"]], 3))

    assert len(f1) == len(f2) == 3
    assert frozenset({"Alice"}) not in api.searches  # cible atteinte avant


# --------------------------------------------------------------------------- #
# Diaporama de la chambre
# --------------------------------------------------------------------------- #
def test_room_slideshow_follows_the_same_priorities() -> None:
    api = FakeImmich(_two_guests_library())
    engine = _engine(api, ["Alice", "Bob"], size=12)

    asyncio.run(engine.async_rebuild())

    ids = {p.asset_id for p in engine._cache._photos}  # noqa: SLF001
    own = {a for a in ids if a.startswith(("A+J", "A+Y", "B+Y"))}
    together = {a for a in ids if a.startswith("A+B")}
    assert len(own) == 8  # tous les niveaux avec propriétaires
    assert len(together) == 4  # complété par « ensemble », sans aller au seul
    assert len(ids) == 12

"""Tests du repli : invité sans photo avec les propriétaires."""
from __future__ import annotations

import asyncio
from typing import Any

from custom_components.immich_guest_slideshow.slideshow import SlideshowEngine

OWNERS = ["Propriétaire 1", "Propriétaire 2"]


class FakeState:
    """État minimal d'un helper input_text."""

    def __init__(self, state: str) -> None:
        self.state = state


class FakeHass:
    """HomeAssistant minimal exposant states.get."""

    def __init__(self, values: dict[str, str]) -> None:
        self.states = self
        self._values = values

    def get(self, entity_id: str) -> FakeState | None:
        value = self._values.get(entity_id)
        return FakeState(value) if value is not None else None


class FakeApi:
    """Client Immich factice : photos définies par combinaison d'ids."""

    def __init__(self, results: dict[frozenset[str], int]) -> None:
        self._results = results
        self.search_calls: list[list[str]] = []

    async def async_find_person(self, name: str) -> dict[str, Any]:
        return {"id": name, "name": name}

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.search_calls.append(sorted(person_ids))
        count = self._results.get(frozenset(person_ids), 0)
        key = "-".join(sorted(person_ids))
        return [{"id": f"{key}#{i}"} for i in range(count)]


def _engine(
    api: FakeApi, guests: dict[str, str], cache_size: int = 100
) -> SlideshowEngine:
    hass = FakeHass(guests)
    return SlideshowEngine(
        hass,  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_test",
        room_name="Chambre test",
        helpers=list(guests),
        permanents=OWNERS,
        cache_size=cache_size,
    )


def test_fallback_to_guest_alone_when_no_owner_photos() -> None:
    """Aucune photo invité+propriétaires -> photos de l'invité seul."""
    api = FakeApi({frozenset(["Alice"]): 4})  # combos avec proprios: 0 photo
    engine = _engine(api, {"input_text.h1": "Alice"})

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 4
    assert "Alice" in [c.label for c in engine.combos]
    photo = engine.next_photo()
    assert photo is not None and photo.search_label == "Alice"


def test_no_fallback_when_owner_photos_reach_the_target() -> None:
    """Assez de photos invité+propriétaire -> pas de recherche seule."""
    api = FakeApi({frozenset(["Alice", "Propriétaire 1"]): 3})
    engine = _engine(api, {"input_text.h1": "Alice"}, cache_size=3)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 3
    assert ["Alice"] not in api.search_calls  # jamais cherchée seule
    # Ordre v2 : invité + tous les propriétaires, puis + le premier : cible
    # atteinte, le second propriétaire n'est même pas cherché.
    assert [c.label for c in engine.combos] == [
        "Alice + Propriétaire 1 + Propriétaire 2",
        "Alice + Propriétaire 1",
    ]


def test_fallback_is_per_guest() -> None:
    """Deux invités : seul celui sans photo commune bascule en repli."""
    api = FakeApi(
        {
            frozenset(["Alice", "Propriétaire 1"]): 2,  # Alice OK avec proprio
            frozenset(["Bob"]): 5,  # Bob n'a que des photos seul
        }
    )
    engine = _engine(api, {"input_text.h1": "Alice", "input_text.h2": "Bob"})

    asyncio.run(engine.async_rebuild())

    labels = [c.label for c in engine.combos]
    # Sous la cible : niveau 2 (Alice + Bob), puis chacun seul.
    assert "Alice + Bob" in labels and "Bob" in labels
    assert engine.photo_count == 7

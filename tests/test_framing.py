"""Cadrage du diaporama : les visages des invités restent toujours visibles."""
from __future__ import annotations

import itertools
from typing import Any

import pytest

from custom_components.immich_guest_slideshow import framing
from custom_components.immich_guest_slideshow.api import (
    ImmichApiError,
    ImmichAuthError,
)
from custom_components.immich_guest_slideshow.slideshow import SlideshowEngine


def _face(x1: int, y1: int, x2: int, y2: int, person: str | None) -> dict[str, Any]:
    return {
        "imageWidth": 1000,
        "imageHeight": 1000,
        "boundingBoxX1": x1,
        "boundingBoxY1": y1,
        "boundingBoxX2": x2,
        "boundingBoxY2": y2,
        "person": {"id": person} if person else None,
    }


def _parse(position: str) -> tuple[float, float]:
    x, y = (float(v.rstrip("%")) / 100 for v in position.split())
    return x, y


def _visible(p: float, v: float) -> tuple[float, float]:
    """Fenêtre visible (fractions de la photo) pour object-position ``p``.

    ``v`` : part de la photo visible sur cet axe (zone d'affichage / photo
    mise à l'échelle par ``object-fit: cover``).
    """
    start = p * (1 - v)
    return start, start + v


# --------------------------------------------------------------------------- #
# Boîte des visages
# --------------------------------------------------------------------------- #
def test_faces_box_prefers_guests_and_unions_them() -> None:
    faces = [
        _face(100, 100, 200, 200, "alice"),
        _face(600, 300, 700, 420, "bob"),
        _face(800, 800, 900, 900, "inconnu"),
    ]
    assert framing.faces_box(faces, ["alice", "bob"]) == (0.1, 0.1, 0.7, 0.42)


def test_faces_box_falls_back_to_all_faces() -> None:
    faces = [_face(100, 100, 200, 200, None), _face(300, 100, 400, 200, "x")]
    assert framing.faces_box(faces, ["alice"]) == (0.1, 0.1, 0.4, 0.2)


def test_faces_box_ignores_unusable_faces() -> None:
    assert framing.faces_box([{"imageWidth": 0}], ["alice"]) is None
    assert framing.faces_box([], ["alice"]) is None


def test_expand_box_adds_hair_and_chin() -> None:
    x1, y1, x2, y2 = framing.expand_box((0.4, 0.4, 0.5, 0.5))
    assert y1 == pytest.approx(0.34)  # front et cheveux
    assert y2 == pytest.approx(0.525)  # menton
    assert x1 == pytest.approx(0.37) and x2 == pytest.approx(0.53)


def test_expand_box_stays_inside_photo() -> None:
    assert framing.expand_box((0.0, 0.0, 1.0, 1.0)) == (0.0, 0.0, 1.0, 1.0)


# --------------------------------------------------------------------------- #
# object-position : la zone reste visible quelle que soit la taille d'affichage
# --------------------------------------------------------------------------- #
def test_object_position_default_without_faces() -> None:
    assert framing.object_position(None) == framing.DEFAULT_OBJECT_POSITION


@pytest.mark.parametrize(
    ("lo", "hi"),
    [(0.0, 0.2), (0.05, 0.3), (0.3, 0.5), (0.6, 0.95), (0.8, 1.0), (0.1, 0.9)],
)
def test_zone_fully_visible_for_any_window_big_enough(lo: float, hi: float) -> None:
    p, _ = _parse(framing.object_position((lo, lo, hi, hi)))
    for i in range(1, 101):
        v = i / 100
        if v < hi - lo:
            continue
        start, end = _visible(p, v)
        assert start <= lo + 1e-3, (lo, hi, v)
        assert end >= hi - 1e-3, (lo, hi, v)


def test_zone_covering_whole_axis_is_centered() -> None:
    assert framing.object_position((0.0, 0.0, 1.0, 1.0)) == "50.0% 50.0%"


def test_portrait_face_at_top_is_never_cut() -> None:
    """Cas réel : photo en hauteur, visages en haut, carte large et basse."""
    faces = [_face(250, 60, 450, 260, "julien"), _face(550, 40, 750, 240, "yves")]
    box = framing.expand_box(framing.faces_box(faces, ["julien", "yves"]))
    _, py = _parse(framing.object_position(box))
    # Carte 600 px de large, 300 px de haut ; photo 3:4 mise à l'échelle en
    # 600×800 : 37,5 % de la hauteur visible.
    start, end = _visible(py, 300 / 800)
    assert start <= box[1] and end >= box[3]


# --------------------------------------------------------------------------- #
# Moteur : un appel Immich par photo, invités de la chambre
# --------------------------------------------------------------------------- #
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
    def __init__(self, faces: dict[str, list[dict[str, Any]]], error=None) -> None:
        self.faces = faces
        self.error = error
        self.calls: list[str] = []

    async def async_find_person(self, name: str) -> dict[str, Any]:
        return {"id": name.lower(), "name": name}

    async def async_get_faces(self, asset_id: str) -> list[dict[str, Any]]:
        self.calls.append(asset_id)
        if self.error:
            raise self.error
        return self.faces.get(asset_id, [])


def _engine(api: FakeImmich) -> SlideshowEngine:
    helpers = ["input_text.lit1", "input_text.lit2"]
    hass = FakeHass(
        {"input_text.lit1": "Alice (2026-10-20 → 2026-10-22)", "input_text.lit2": "Bob"}
    )
    return SlideshowEngine(
        hass,  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_ami",
        room_name="Chambre d'ami",
        helpers=helpers,
        permanents=["Julien"],
        cache_size=10,
    )


async def test_engine_frames_on_room_guests_and_memoizes() -> None:
    api = FakeImmich(
        {
            "p1": [
                _face(100, 500, 200, 600, "alice"),
                _face(300, 520, 400, 620, "bob"),
                _face(800, 50, 900, 150, "julien"),
            ]
        }
    )
    engine = _engine(api)
    position = await engine.async_object_position("p1")
    expected = framing.object_position(
        framing.expand_box(framing.faces_box(api.faces["p1"], ["alice", "bob"]))
    )
    assert position == expected
    assert await engine.async_object_position("p1") == expected
    assert api.calls == ["p1"]


async def test_engine_default_without_asset_or_faces() -> None:
    engine = _engine(FakeImmich({}))
    assert await engine.async_object_position(None) == framing.DEFAULT_OBJECT_POSITION
    assert await engine.async_object_position("x") == framing.DEFAULT_OBJECT_POSITION


async def test_engine_stops_asking_without_face_permission() -> None:
    api = FakeImmich({}, error=ImmichAuthError("Authentification refusée (403)"))
    engine = _engine(api)
    for asset in ("a", "b", "c"):
        assert await engine.async_object_position(asset) == (
            framing.DEFAULT_OBJECT_POSITION
        )
    assert api.calls == ["a"]


async def test_engine_retries_after_transient_error() -> None:
    api = FakeImmich({}, error=ImmichApiError("Erreur API 500"))
    engine = _engine(api)
    await engine.async_object_position("a")
    await engine.async_object_position("a")
    assert api.calls == ["a", "a"]


def test_positions_grid_sanity() -> None:
    """Toutes les positions restent dans 0..100 %."""
    for lo, hi in itertools.combinations([i / 10 for i in range(11)], 2):
        x, y = _parse(framing.object_position((lo, lo, hi, hi)))
        assert 0 <= x <= 1 and 0 <= y <= 1

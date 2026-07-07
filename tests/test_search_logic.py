"""Tests de la logique de construction des recherches."""
from custom_components.immich_guest_slideshow.slideshow import build_search_combos

PERMS = ["Propriétaire 1", "Propriétaire 2"]


def test_no_guest_returns_no_search() -> None:
    """Aucun invité -> aucune recherche (pas de photo)."""
    assert build_search_combos([], PERMS) == []


def test_one_guest_builds_three_searches() -> None:
    """Un invité -> 3 combinaisons dans l'ordre spécifié."""
    combos = build_search_combos(["Alice"], PERMS)
    assert [c.label for c in combos] == [
        "Alice + Propriétaire 1",
        "Alice + Propriétaire 2",
        "Alice + Propriétaire 1 + Propriétaire 2",
    ]


def test_two_guests_build_nine_searches() -> None:
    """Deux invités -> 9 combinaisons dans l'ordre spécifié."""
    combos = build_search_combos(["Alice", "Bob"], PERMS)
    labels = [c.label for c in combos]
    assert len(labels) == 9
    assert labels[:3] == [
        "Alice + Propriétaire 1",
        "Alice + Propriétaire 2",
        "Alice + Propriétaire 1 + Propriétaire 2",
    ]
    assert labels[3:6] == [
        "Bob + Propriétaire 1",
        "Bob + Propriétaire 2",
        "Bob + Propriétaire 1 + Propriétaire 2",
    ]
    assert labels[6:] == [
        "Alice + Bob + Propriétaire 1",
        "Alice + Bob + Propriétaire 2",
        "Alice + Bob + Propriétaire 1 + Propriétaire 2",
    ]

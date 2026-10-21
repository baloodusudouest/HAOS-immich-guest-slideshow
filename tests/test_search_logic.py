"""Ordre de priorité des recherches et répartition entre les cadres."""
from custom_components.immich_guest_slideshow.slideshow import (
    frames_guests,
    priority_levels,
)

OWNERS = ["Julien", "Hôte2"]


def test_priority_one_guest() -> None:
    """Invité + Julien + Hôte2, + Julien, + Hôte2, puis seul."""
    assert priority_levels("Alice", ["Alice"], OWNERS) == [
        ("Alice", "Julien", "Hôte2"),
        ("Alice", "Julien"),
        ("Alice", "Hôte2"),
        ("Alice",),
    ]


def test_priority_two_guests_adds_together_before_alone() -> None:
    assert priority_levels("Bob", ["Alice", "Bob"], OWNERS) == [
        ("Bob", "Julien", "Hôte2"),
        ("Bob", "Julien"),
        ("Bob", "Hôte2"),
        ("Alice", "Bob"),
        ("Bob",),
    ]


def test_priority_single_owner() -> None:
    assert priority_levels("Alice", ["Alice"], ["Julien"]) == [
        ("Alice", "Julien"),
        ("Alice",),
    ]


def test_frames_guests() -> None:
    assert frames_guests(["A", "B"], 2) == [["A"], ["B"]]
    assert frames_guests(["A"], 2) == [["A"], ["A"]]
    assert frames_guests(["A", "B"], 1) == [["A", "B"]]
    assert frames_guests([], 2) == [[], []]
    assert frames_guests(["A"], 0) == []

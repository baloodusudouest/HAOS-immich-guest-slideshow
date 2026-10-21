"""Ordre de priorité des recherches et répartition entre les cadres."""
from custom_components.immich_guest_slideshow.slideshow import (
    frames_guests,
    priority_levels,
)

OWNERS = ["Julien", "Yves"]


def test_priority_one_guest() -> None:
    """Invité + Julien + Yves, + Julien, + Yves, puis seul."""
    assert priority_levels("Alice", ["Alice"], OWNERS) == [
        ("Alice", "Julien", "Yves"),
        ("Alice", "Julien"),
        ("Alice", "Yves"),
        ("Alice",),
    ]


def test_priority_two_guests_adds_together_before_alone() -> None:
    assert priority_levels("Bob", ["Alice", "Bob"], OWNERS) == [
        ("Bob", "Julien", "Yves"),
        ("Bob", "Julien"),
        ("Bob", "Yves"),
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

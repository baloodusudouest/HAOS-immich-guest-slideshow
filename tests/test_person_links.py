"""Tests : mémoire des liaisons, seuil de photos et signalement des problèmes."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from custom_components.immich_guest_slideshow.person_links import (
    KIND_FEW_PHOTOS,
    KIND_NOT_FOUND,
    PersonLinks,
    PersonProblem,
    link_key,
)
from custom_components.immich_guest_slideshow.problems import ProblemReporter
from custom_components.immich_guest_slideshow.slideshow import SlideshowEngine

ALEXIA = "1238c4da-3b98-41b9-b795-81d51e95a6d5"
OWNER1 = "aaaaaaaa-0000-4000-8000-000000000001"
OWNER2 = "aaaaaaaa-0000-4000-8000-000000000002"


# --------------------------------------------------------------------------- #
# Faux éléments
# --------------------------------------------------------------------------- #
class FakeStore:
    """Stockage en mémoire, imitant homeassistant.helpers.storage.Store."""

    def __init__(self, data: Any = None) -> None:
        self.data = data
        self.saves = 0

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        self.data = data
        self.saves += 1


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


class FakeApi:
    """Immich factice.

    ``photos`` : combinaison d'ids -> nombre de photos.
    ``by_name`` : nom -> id (recherche par nom).
    """

    def __init__(
        self,
        photos: dict[frozenset[str], int],
        by_name: dict[str, str] | None = None,
    ) -> None:
        self.people = {
            ALEXIA: "Alexia",
            OWNER1: "Propriétaire 1",
            OWNER2: "Propriétaire 2",
        }
        self.photos = photos
        self.by_name = by_name or {}
        self.get_calls: list[str] = []
        self.find_calls: list[str] = []

    async def async_get_person(self, person_id: str) -> dict[str, Any] | None:
        self.get_calls.append(person_id)
        name = self.people.get(person_id)
        return {"id": person_id, "name": name} if name else None

    async def async_find_person(self, name: str) -> dict[str, Any] | None:
        self.find_calls.append(name)
        pid = self.by_name.get(name)
        return {"id": pid, "name": name} if pid else None

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        count = self.photos.get(frozenset(person_ids), 0)
        key = "-".join(sorted(person_ids))
        return [{"id": f"{key}#{i}"} for i in range(count)]


class FakeReporter:
    """Collecte les mises à jour reçues du moteur."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, str, list[PersonProblem]]] = []

    def async_update(
        self, room_id: str, room_name: str, problems: list[PersonProblem]
    ) -> None:
        self.updates.append((room_id, room_name, list(problems)))

    @property
    def last(self) -> list[PersonProblem]:
        return self.updates[-1][2]


def _engine(
    api: FakeApi,
    guest: str,
    *,
    links: PersonLinks | None = None,
    reporter: FakeReporter | None = None,
    min_photos: int = 2,
    permanents: list[str] | None = None,
) -> SlideshowEngine:
    return SlideshowEngine(
        FakeHass({"input_text.h1": guest}),  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_test",
        room_name="Chambre test",
        helpers=["input_text.h1"],
        permanents=permanents if permanents is not None else [OWNER1, OWNER2],
        cache_size=100,
        min_photos=min_photos,
        links=links,
        reporter=reporter,
    )


def _links(data: dict[str, str] | None = None) -> PersonLinks:
    links = PersonLinks(FakeStore({"links": data} if data else None))
    asyncio.run(links.async_load())
    return links


# --------------------------------------------------------------------------- #
# PersonLinks (mémoire persistante)
# --------------------------------------------------------------------------- #
def test_link_key_is_case_and_unicode_insensitive() -> None:
    assert link_key("  Chloé ") == link_key("CHLOÉ")
    assert link_key("Chloé") == link_key("Chloé")  # NFD == NFC


def test_links_persist_through_store() -> None:
    store = FakeStore()
    links = PersonLinks(store)
    asyncio.run(links.async_set("Alexia", ALEXIA))
    assert store.data == {"links": {"alexia": ALEXIA}}

    reloaded = PersonLinks(store)
    asyncio.run(reloaded.async_load())
    assert reloaded.get("ALEXIA") == ALEXIA


def test_unlink_one_all_and_unknown() -> None:
    store = FakeStore({"links": {"alexia": ALEXIA, "bob": OWNER1}})
    links = PersonLinks(store)
    asyncio.run(links.async_load())

    assert asyncio.run(links.async_remove("Alexia")) == 1
    assert links.get("Alexia") is None and links.get("Bob") == OWNER1
    assert asyncio.run(links.async_remove("Inconnu")) == 0
    assert asyncio.run(links.async_remove()) == 1
    assert links.items == {}
    assert store.data == {"links": {}}


def test_unlink_nothing_does_not_write() -> None:
    store = FakeStore()
    links = PersonLinks(store)
    assert asyncio.run(links.async_remove("x")) == 0
    assert store.saves == 0


# --------------------------------------------------------------------------- #
# Résolution : la liaison prime
# --------------------------------------------------------------------------- #
def test_link_takes_priority_over_name_search() -> None:
    api = FakeApi(
        {frozenset([ALEXIA, OWNER1]): 5},
        by_name={"Alexia": "bbbbbbbb-0000-4000-8000-00000000000f"},  # homonyme
    )
    engine = _engine(api, "Alexia", links=_links({"alexia": ALEXIA}))

    asyncio.run(engine.async_rebuild())

    assert api.find_calls == []  # aucune recherche par nom pour l'invité
    assert engine.photo_count == 5
    assert [c.label for c in engine.combos][0] == "Alexia + Propriétaire 1"


def test_stale_link_falls_back_to_name_search() -> None:
    gone = "cccccccc-0000-4000-8000-000000000003"
    api = FakeApi({frozenset([ALEXIA, OWNER1]): 4}, by_name={"Alexia": ALEXIA})
    engine = _engine(api, "Alexia", links=_links({"alexia": gone}))

    asyncio.run(engine.async_rebuild())

    assert gone in api.get_calls and api.find_calls == ["Alexia"]
    assert engine.photo_count == 4


# --------------------------------------------------------------------------- #
# Seuil minimum : on affiche TOUT ce qui existe
# --------------------------------------------------------------------------- #
def test_one_photo_with_owner_adds_individual_photos_and_keeps_all() -> None:
    """1 photo avec un propriétaire (< 2) -> + photos individuelles, tout affiché."""
    api = FakeApi({frozenset([ALEXIA, OWNER1]): 1, frozenset([ALEXIA]): 6})
    reporter = FakeReporter()
    engine = _engine(api, ALEXIA, reporter=reporter)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 7
    assert reporter.last == []  # 7 photos au total >= 2 : rien à signaler


def test_enough_photos_with_owners_no_fallback() -> None:
    api = FakeApi({frozenset([ALEXIA, OWNER1]): 2, frozenset([ALEXIA]): 9})
    engine = _engine(api, ALEXIA)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 2  # seuil atteint : pas de repli


def test_below_minimum_still_shows_everything_and_reports() -> None:
    """1 seule photo au total (< 2) : elle est affichée ET signalée."""
    api = FakeApi({frozenset([ALEXIA]): 1})
    reporter = FakeReporter()
    engine = _engine(api, ALEXIA, reporter=reporter)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 1
    assert reporter.last == [PersonProblem(ALEXIA, KIND_FEW_PHOTOS, 1, 2)]


def test_min_photos_is_configurable() -> None:
    api = FakeApi({frozenset([ALEXIA, OWNER1]): 3, frozenset([ALEXIA]): 4})
    engine = _engine(api, ALEXIA, min_photos=5)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 7  # 3 < 5 -> + 4 individuelles


# --------------------------------------------------------------------------- #
# Personne introuvable -> signalement avec le nom saisi
# --------------------------------------------------------------------------- #
def test_unknown_name_is_reported_not_found() -> None:
    api = FakeApi({}, by_name={})
    reporter = FakeReporter()
    engine = _engine(api, "Zoé Inconnue", reporter=reporter)

    asyncio.run(engine.async_rebuild())

    assert engine.photo_count == 0
    kinds = {(p.name, p.kind) for p in reporter.last}
    assert ("Zoé Inconnue", KIND_NOT_FOUND) in kinds


def test_linking_clears_the_problem() -> None:
    api = FakeApi({frozenset([ALEXIA, OWNER1]): 3})
    reporter = FakeReporter()
    links = _links()
    engine = _engine(api, "Alex", links=links, reporter=reporter)

    asyncio.run(engine.async_rebuild())
    assert any(p.name == "Alex" and p.kind == KIND_NOT_FOUND for p in reporter.last)

    asyncio.run(links.async_set("Alex", ALEXIA))
    asyncio.run(engine.async_rebuild())

    assert reporter.last == []
    assert engine.photo_count == 3


def test_typed_uuid_not_reported_as_name_problem() -> None:
    """Un UUID inexistant n'est pas « liable » : pas d'élément de Réparations."""
    api = FakeApi({})
    reporter = FakeReporter()
    engine = _engine(
        api, "dddddddd-0000-4000-8000-000000000004", reporter=reporter
    )

    asyncio.run(engine.async_rebuild())

    assert reporter.last == []


def test_no_guest_clears_problems() -> None:
    api = FakeApi({})
    reporter = FakeReporter()
    engine = _engine(api, "unknown", reporter=reporter)  # helper vide

    asyncio.run(engine.async_rebuild())

    assert reporter.last == []


# --------------------------------------------------------------------------- #
# ProblemReporter : Réparations + notification, dédoublonnés et nettoyés
# --------------------------------------------------------------------------- #
def test_reporter_creates_one_issue_per_name_across_rooms() -> None:
    ir = MagicMock()
    notif = MagicMock()
    with patch("custom_components.immich_guest_slideshow.problems.ir", ir), patch(
        "custom_components.immich_guest_slideshow.problems.persistent_notification",
        notif,
    ):
        reporter = ProblemReporter(MagicMock(), "entry1")
        problem = PersonProblem("Alexia", KIND_NOT_FOUND, 0, 2)
        reporter.async_update("r1", "Chambre 1", [problem])
        reporter.async_update("r2", "Chambre 2", [problem])

    assert ir.async_create_issue.call_count == 2  # 1re création + mise à jour des chambres
    issue_ids = {c.args[2] for c in ir.async_create_issue.call_args_list}
    assert len(issue_ids) == 1  # un seul élément pour « Alexia »
    last = ir.async_create_issue.call_args_list[-1]
    assert last.kwargs["is_fixable"] is True
    assert "Chambre 1" in last.kwargs["translation_placeholders"]["rooms"]
    assert "Chambre 2" in last.kwargs["translation_placeholders"]["rooms"]
    assert last.kwargs["translation_key"] == "person_not_found"
    assert notif.async_create.called


def test_reporter_removes_issue_and_notification_when_fixed() -> None:
    ir = MagicMock()
    notif = MagicMock()
    with patch("custom_components.immich_guest_slideshow.problems.ir", ir), patch(
        "custom_components.immich_guest_slideshow.problems.persistent_notification",
        notif,
    ):
        reporter = ProblemReporter(MagicMock(), "entry1")
        reporter.async_update(
            "r1", "Chambre 1", [PersonProblem("Alexia", KIND_FEW_PHOTOS, 1, 2)]
        )
        issue_id = ir.async_create_issue.call_args.args[2]
        reporter.async_update("r1", "Chambre 1", [])

    ir.async_delete_issue.assert_called_once()
    assert ir.async_delete_issue.call_args.args[2] == issue_id
    notif.async_dismiss.assert_called_once()
    assert ir.async_create_issue.call_args.kwargs["translation_key"] == "person_few_photos"


def test_reporter_does_not_recreate_unchanged_issue() -> None:
    ir = MagicMock()
    with patch("custom_components.immich_guest_slideshow.problems.ir", ir), patch(
        "custom_components.immich_guest_slideshow.problems.persistent_notification",
        MagicMock(),
    ):
        reporter = ProblemReporter(MagicMock(), "entry1")
        problem = PersonProblem("Alexia", KIND_NOT_FOUND, 0, 2)
        reporter.async_update("r1", "Chambre 1", [problem])
        reporter.async_update("r1", "Chambre 1", [problem])

    assert ir.async_create_issue.call_count == 1


def test_reporter_clear_all() -> None:
    ir = MagicMock()
    with patch("custom_components.immich_guest_slideshow.problems.ir", ir), patch(
        "custom_components.immich_guest_slideshow.problems.persistent_notification",
        MagicMock(),
    ):
        reporter = ProblemReporter(MagicMock(), "entry1")
        reporter.async_update(
            "r1", "Chambre 1", [PersonProblem("Alexia", KIND_NOT_FOUND, 0, 2)]
        )
        reporter.async_clear_all()

    assert ir.async_delete_issue.call_count == 1

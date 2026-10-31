"""Tests de la 2.3.1 : robustesse (Immich injoignable, calendrier illisible,
homonymes, re-sélection forcée des cadres, longueur d'URL des cadres)."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.immich_guest_slideshow.api import (
    ImmichApiClient,
    ImmichApiError,
)
from custom_components.immich_guest_slideshow.const import (
    CONF_PICPAK_BASE_URL,
    CONF_PICPAK_INTERVAL,
    CONF_PICPAK_LOGO,
    CONF_PICPAK_LOGO_2,
    CONF_PICPAK_PHOTOS,
    CONF_PICPAK_QUIET,
    CONF_PICPAK_RESET_BUTTON,
    CONF_PICPAK_SUBTITLE,
    CONF_PICPAK_TITLE,
)
from custom_components.immich_guest_slideshow.slideshow import SlideshowEngine
from custom_components.immich_guest_slideshow.stay import StayTracker

from .common import (
    HELPER_1,
    PEOPLE,
    FakeImmich,
    make_entry,
    room_subentry,
    setup_entry,
)
from .test_api import FakeResponse, FakeSession


# --------------------------------------------------------------------------- #
# (d) Homonymes / correspondance approximative
# --------------------------------------------------------------------------- #
def _client(people: list[dict[str, Any]]) -> ImmichApiClient:
    session = FakeSession([FakeResponse(json_data=people)] * 3)
    return ImmichApiClient("http://immich", "key", session)  # type: ignore[arg-type]


def test_find_person_exact_match() -> None:
    client = _client([{"id": "1", "name": "Paul Martin"}, {"id": "2", "name": "Paul"}])
    assert asyncio.run(client.async_find_person("Paul"))["id"] == "2"


def test_find_person_without_accents() -> None:
    client = _client([{"id": "1", "name": "Chloé"}])
    assert asyncio.run(client.async_find_person("Chloe"))["id"] == "1"


def test_find_person_no_more_approximate_fallback() -> None:
    """« Paul » ne doit plus retenir « Paul Martin » sans prévenir."""
    client = _client([{"id": "1", "name": "Paul Martin"}, {"id": "2", "name": "Paul Durand"}])
    assert asyncio.run(client.async_find_person("Paul")) is None


def test_find_person_homonyms_are_ambiguous() -> None:
    client = _client([{"id": "1", "name": "Paul Martin"}, {"id": "2", "name": "paul martin"}])
    assert asyncio.run(client.async_find_person("Paul Martin")) is None


# --------------------------------------------------------------------------- #
# (b) Une panne d'Immich n'est pas mémorisée comme « aucune photo »
# --------------------------------------------------------------------------- #
class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class _FakeHass:
    def __init__(self, values: dict[str, str]) -> None:
        self.states = self
        self._values = values

    def get(self, entity_id: str) -> _FakeState | None:
        value = self._values.get(entity_id)
        return _FakeState(value) if value is not None else None


class _FlakyApi:
    """Immich factice qui tombe en panne puis revient."""

    def __init__(self) -> None:
        self.down = True

    async def async_get_person(self, person_id: str) -> dict[str, Any] | None:
        return None

    async def async_find_person(self, name: str) -> dict[str, Any] | None:
        if self.down:
            raise ImmichApiError("injoignable")
        return {"id": PEOPLE[name], "name": name}

    async def async_search_assets(self, person_ids: list[str], **kwargs: Any):
        if self.down:
            raise ImmichApiError("injoignable")
        return [{"id": f"{'-'.join(sorted(person_ids))}#{i}"} for i in range(3)]


def _engine(api: _FlakyApi) -> SlideshowEngine:
    return SlideshowEngine(
        _FakeHass({"input_text.h1": "Alexia"}),  # type: ignore[arg-type]
        api,  # type: ignore[arg-type]
        room_id="chambre_test",
        room_name="Chambre test",
        helpers=["input_text.h1"],
        permanents=["Propriétaire 1"],
        cache_size=5,
        min_photos=1,
    )


def test_outage_is_not_cached_and_frames_retry() -> None:
    api = _FlakyApi()
    engine = _engine(api)

    async def run() -> None:
        try:
            await engine.async_frame_playlists([["Alexia"]], 5)
        except ImmichApiError:
            pass
        else:  # pragma: no cover - doit lever
            raise AssertionError("une sélection incomplète doit lever")
        assert engine.incomplete
        api.down = False
        playlists = await engine.async_frame_playlists([["Alexia"]], 5)
        assert playlists[0], "Immich revenu : les photos doivent arriver"
        assert not engine.incomplete

    asyncio.run(run())


def test_outage_raises_no_false_not_found_problem() -> None:
    api = _FlakyApi()
    engine = _engine(api)
    asyncio.run(engine.async_rebuild())
    assert engine.incomplete
    assert engine.problems == []
    api.down = False
    asyncio.run(engine.async_rebuild())
    assert not engine.incomplete
    assert engine.photo_count > 0


# --------------------------------------------------------------------------- #
# (c) Calendrier illisible : jamais un faux séjour
# --------------------------------------------------------------------------- #
async def test_unreadable_calendar_means_free_room(hass: HomeAssistant) -> None:
    from homeassistant.core import SupportsResponse

    hass.states.async_set("calendar.chambre_test", "off")

    async def broken(call):
        raise RuntimeError("calendrier en panne")

    hass.services.async_register(
        "calendar", "get_events", broken, supports_response=SupportsResponse.ONLY
    )
    tracker = StayTracker(hass, "calendar.chambre_test", 9, lambda: None, lambda: False)
    status = await tracker.async_status()
    assert status.active is False


# --------------------------------------------------------------------------- #
# (a) Re-sélection forcée des cadres après refresh / liaison
# --------------------------------------------------------------------------- #
async def test_refresh_forces_frames_reselection(hass: HomeAssistant) -> None:
    from custom_components.immich_guest_slideshow.const import DOMAIN, SERVICE_REFRESH

    alexia = frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]])
    FakeImmich.reset({alexia: 0})
    entry = await setup_entry(
        hass,
        {HELPER_1: "Alexia"},
        make_entry([room_subentry()], **{CONF_PICPAK_INTERVAL: 60, CONF_PICPAK_PHOTOS: 5}),
    )
    picpak = entry.runtime_data.picpak
    await picpak.async_ensure_room("chambre_test")
    before = [len(f.assets) for k, f in picpak.frames.items() if k.endswith("1")]

    # Les photos apparaissent dans Immich (ex. après une liaison) : l'empreinte
    # du cadre ne change pas, seul un refresh forcé peut les prendre en compte.
    FakeImmich.photos = {alexia: 4}
    await hass.services.async_call(DOMAIN, SERVICE_REFRESH, {}, blocking=True)
    await hass.async_block_till_done()
    after = [len(f.assets) for k, f in picpak.frames.items() if k.endswith("1")]
    assert after != before and after[0] > 0


# --------------------------------------------------------------------------- #
# (f) Longueur de l'URL des cadres contrôlée dans les réglages
# --------------------------------------------------------------------------- #
def _picpak_input(base_url: str) -> dict[str, Any]:
    return {
        CONF_PICPAK_INTERVAL: 60,
        CONF_PICPAK_PHOTOS: 50,
        CONF_PICPAK_QUIET: "",
        CONF_PICPAK_LOGO: "/config/www/picpak_logo.png",
        CONF_PICPAK_LOGO_2: "",
        CONF_PICPAK_TITLE: "Bienvenue",
        CONF_PICPAK_SUBTITLE: "",
        CONF_PICPAK_BASE_URL: base_url,
        CONF_PICPAK_RESET_BUTTON: "input_button.reinitialiser_chambre_hote",
    }


async def test_options_reject_too_long_base_url(hass: HomeAssistant) -> None:
    entry = make_entry([room_subentry()])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "picpak"}
    )
    long_base = "http://" + "x" * 80 + ".local:8123"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _picpak_input(long_base)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PICPAK_BASE_URL: "base_url_too_long"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _picpak_input("http://192.168.1.29:8123")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

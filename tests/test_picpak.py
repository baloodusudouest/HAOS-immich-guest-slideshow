"""Cadres PicPak de bout en bout : vue HTTP, séjours, porte, réinitialisation."""
from __future__ import annotations

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util

from custom_components.immich_guest_slideshow import picpak_render as pr
from custom_components.immich_guest_slideshow.const import (
    CONF_PICPAK_INTERVAL,
    CONF_PICPAK_PHOTOS,
    DOMAIN,
    SERVICE_PICPAK_NEXT,
    SERVICE_PICPAK_URLS,
)

from .common import (
    HELPER_1,
    HELPER_2,
    PEOPLE,
    FakeImmich,
    make_entry,
    room_subentry,
    setup_entry,
)

ALEXIA_OWNERS = frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]])
BRUNO_OWNERS = frozenset([PEOPLE["Bruno"], PEOPLE["Propriétaire 1"]])
CALENDAR = "calendar.chambre_test"


def _entry(**room):
    return make_entry(
        [room_subentry(**room)], **{CONF_PICPAK_INTERVAL: 60, CONF_PICPAK_PHOTOS: 5}
    )


async def _urls(hass: HomeAssistant) -> dict[str, str]:
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_PICPAK_URLS, {}, blocking=True, return_response=True
    )
    return {f["slot"]: f["url"] for f in resp["frames"]}


def _path(url: str) -> str:
    return url.split("8123", 1)[1]


def _logo(caption: str) -> bytes:
    return pr.render_logo("/config/www/picpak_logo.png", "Bienvenue", "", caption)


def _calendar(hass: HomeAssistant, events: list[dict]) -> None:
    hass.states.async_set(CALENDAR, "off")

    async def get_events(call):
        return {CALENDAR: {"events": events}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )


async def test_single_guest_feeds_both_frames(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    FakeImmich.reset({ALEXIA_OWNERS: 10})
    entry = await setup_entry(hass, {HELPER_1: "Alexia", HELPER_2: ""}, _entry())
    urls = await _urls(hass)
    assert set(urls) == {"1", "2"}  # pas de cadre de porte
    assert all(len(u) <= 128 for u in urls.values())
    assert urls["1"].endswith(f"/picpak/chambre_test/1?k={entry.runtime_data.picpak.token}")
    client = await hass_client_no_auth()

    assert (await client.get(_path(urls["1"]).replace("?k=", "?k=x"))).status == 403

    r1 = await client.get(_path(urls["1"]))
    b1 = await r1.read()
    r2 = await client.get(_path(urls["2"]))
    b2 = await r2.read()
    assert r1.status == 200 and len(b1) == pr.FRAME_BYTES
    assert r1.headers["X-Next-Wake-Seconds"] == "3600"
    manager = entry.runtime_data.picpak
    f1, f2 = manager.frames["chambre_test/1"], manager.frames["chambre_test/2"]
    assert f1.guest == f2.guest == "Alexia"
    assert not set(f1.assets) & set(f2.assets)  # photos différentes
    assert len(f1.assets) == len(f2.assets) == 5
    assert b1 != b2

    # Nouveau réveil dans l'heure : même image (pas de rafraîchissement).
    assert await (await client.get(_path(urls["1"]))).read() == b1
    # Une heure plus tard : photo suivante.
    freezer.tick(3600)
    assert await (await client.get(_path(urls["1"]))).read() != b1
    # Appui sur le bouton du cadre : photo suivante tout de suite.
    shown = f1.shown
    await client.get(_path(urls["1"]), headers={"X-Picpak-Wake": "button"})
    assert f1.shown != shown


async def test_two_guests_one_frame_each(hass: HomeAssistant, hass_client_no_auth) -> None:
    FakeImmich.reset({ALEXIA_OWNERS: 4, BRUNO_OWNERS: 4})
    entry = await setup_entry(hass, {HELPER_1: "Alexia", HELPER_2: "Bruno"}, _entry())
    manager = entry.runtime_data.picpak
    await manager.async_ensure_room("chambre_test")
    f1, f2 = manager.frames["chambre_test/1"], manager.frames["chambre_test/2"]
    assert (f1.guest, f2.guest) == ("Alexia", "Bruno")
    assert all(PEOPLE["Alexia"] in a for a in f1.assets)
    assert all(PEOPLE["Bruno"] in a for a in f2.assets)


async def test_one_frame_mixes_both_guests(hass: HomeAssistant) -> None:
    FakeImmich.reset({ALEXIA_OWNERS: 3, BRUNO_OWNERS: 3})
    entry = await setup_entry(
        hass, {HELPER_1: "Alexia", HELPER_2: "Bruno"}, _entry(frames=1)
    )
    manager = entry.runtime_data.picpak
    await manager.async_ensure_room("chambre_test")
    assert list(manager.frames) == ["chambre_test/1"]
    frame = manager.frames["chambre_test/1"]
    assert frame.guest == "Alexia & Bruno"
    assert any(PEOPLE["Alexia"] in a for a in frame.assets)
    assert any(PEOPLE["Bruno"] in a for a in frame.assets)


async def test_unreachable_photo_is_skipped(hass: HomeAssistant, hass_client_no_auth) -> None:
    FakeImmich.reset({ALEXIA_OWNERS: 4})
    entry = await setup_entry(hass, {HELPER_1: "Alexia"}, _entry(frames=1))
    manager = entry.runtime_data.picpak
    await manager.async_ensure_room("chambre_test")
    frame = manager.frames["chambre_test/1"]
    # Rendus préparés à l'avance : on les laisse finir, puis on les oublie.
    await hass.async_block_till_done(wait_background_tasks=True)
    manager._rendered.clear()
    FakeImmich.fail = {frame.assets[0]}
    urls = await _urls(hass)
    client = await hass_client_no_auth()
    body = await (await client.get(_path(urls["1"]))).read()
    assert len(body) == pr.FRAME_BYTES
    assert frame.shown == frame.assets[1]


async def test_planned_then_ongoing_then_free(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    """Séjour samedi → dimanche programmé lundi : carte « programmé », photos
    de samedi 9 h à lundi 00:01, puis logo de la chambre libre."""
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 19, 19, 0, tzinfo=tz))
    FakeImmich.reset({ALEXIA_OWNERS: 60})
    _calendar(hass, [{"start": "2026-10-24", "end": "2026-10-26", "summary": "Alexia"}])
    entry = await setup_entry(
        hass, {HELPER_1: "Alexia", HELPER_2: ""}, _entry(calendar=CALENDAR)
    )
    urls = await _urls(hass)
    client = await hass_client_no_auth()
    manager = entry.runtime_data.picpak
    frame = manager.frames["chambre_test/1"]

    resp = await client.get(_path(urls["1"]))
    planned = await resp.read()
    assert planned == _logo("Chambre test\nAlexia\nsam. 24 → dim. 25 oct.")
    assert frame.state == "programme" and frame.index == -1
    # Une photo par heure de séjour : samedi 9 h → lundi 00:01 = 40 réveils,
    # répartis sans doublon sur les deux cadres.
    assert len(frame.assets) == 30  # 60 photos disponibles, 30 par cadre

    freezer.move_to(datetime(2026, 10, 24, 8, 30, tzinfo=tz))
    resp = await client.get(_path(urls["1"]))
    assert await resp.read() == planned
    assert int(resp.headers["X-Next-Wake-Seconds"]) == 30 * 60 + 60

    freezer.move_to(datetime(2026, 10, 24, 9, 1, tzinfo=tz))
    assert await (await client.get(_path(urls["1"]))).read() != planned
    assert frame.state == "en_cours" and frame.shown in frame.assets

    freezer.move_to(datetime(2026, 10, 25, 23, 30, tzinfo=tz))
    resp = await client.get(_path(urls["1"]))
    assert frame.state == "en_cours"
    # Réveil calé sur la fin du séjour (lundi 00:01) plutôt qu'une heure pleine.
    assert int(resp.headers["X-Next-Wake-Seconds"]) == 31 * 60 + 60

    freezer.move_to(datetime(2026, 10, 26, 0, 2, tzinfo=tz))
    assert await (await client.get(_path(urls["1"]))).read() == _logo("Chambre test")
    assert frame.state == "libre"


async def test_planned_stay_without_helper_uses_event_titles(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 19, 19, 0, tzinfo=tz))
    FakeImmich.reset()
    _calendar(
        hass,
        [
            {"start": "2026-10-23", "end": "2026-10-25", "summary": "Corinne Jacquouton"},
            {"start": "2026-10-23", "end": "2026-10-25", "summary": "Régis Jacquouton"},
        ],
    )
    await setup_entry(hass, {HELPER_1: "", HELPER_2: ""}, _entry(calendar=CALENDAR))
    urls = await _urls(hass)
    client = await hass_client_no_auth()
    body = await (await client.get(_path(urls["2"]))).read()
    assert body == _logo(
        "Chambre test\nCorinne Jacquouton et Régis Jacquouton\nven. 23 → sam. 24 oct."
    )


async def test_door_frame_free_then_welcome(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 23, 8, 0, tzinfo=tz))
    FakeImmich.reset({BRUNO_OWNERS: 3})
    _calendar(hass, [{"start": "2026-10-23", "end": "2026-10-25", "summary": "Séjour"}])
    entry = await setup_entry(
        hass,
        {HELPER_1: "Carla Dupont", HELPER_2: "Bruno"},
        _entry(calendar=CALENDAR, door=True, frames=0),
    )
    urls = await _urls(hass)
    assert set(urls) == {"porte"}
    assert urls["porte"].split("?")[0].endswith("/picpak/chambre_test/porte")
    client = await hass_client_no_auth()

    # 8 h : séjour pas commencé -> logo et nom de la chambre.
    resp = await client.get(_path(urls["porte"]))
    assert await resp.read() == _logo("Chambre test")
    assert int(resp.headers["X-Next-Wake-Seconds"]) == 3600

    # 9 h 05 : visages et « Carla et Bruno, bienvenue dans la chambre test »
    # (prénoms seulement).
    freezer.move_to(datetime(2026, 10, 23, 9, 5, tzinfo=tz))
    body = await (await client.get(_path(urls["porte"]))).read()
    face = await FakeImmich().async_get_person_thumbnail("x")
    assert body == pr.render_door(
        "Chambre test", [("Carla", face), ("Bruno", face)], "ven. 23 → sam. 24 oct."
    )
    frame = entry.runtime_data.picpak.frames["chambre_test/porte"]
    assert frame.state == "en_cours" and frame.shown == "__porte__"
    assert {PEOPLE["Carla Dupont"], PEOPLE["Bruno"]} <= set(FakeImmich.person_thumbs)
    # L'heure suivante : carte identique (la dalle ne se redessine pas).
    freezer.tick(3600)
    assert await (await client.get(_path(urls["porte"]))).read() == body


async def test_reset_button_ends_the_stay(hass: HomeAssistant, hass_client_no_auth) -> None:
    """Sans calendrier : chambre libre après « réinitialiser », jusqu'au
    prochain invité."""
    FakeImmich.reset({ALEXIA_OWNERS: 4, BRUNO_OWNERS: 4})
    hass.states.async_set("input_button.reinitialiser_chambre_hote", "2026-10-19T10:00:00")
    entry = await setup_entry(hass, {HELPER_1: "Alexia", HELPER_2: ""}, _entry(door=True))
    urls = await _urls(hass)
    client = await hass_client_no_auth()
    free = _logo("Chambre test")
    assert await (await client.get(_path(urls["1"]))).read() != free
    assert await (await client.get(_path(urls["porte"]))).read() != free

    hass.states.async_set("input_button.reinitialiser_chambre_hote", "2026-10-19T19:20:00")
    await hass.async_block_till_done()
    for slot in ("1", "2", "porte"):
        assert await (await client.get(_path(urls[slot]))).read() == free, slot

    from .common import patch_immich

    with patch_immich():
        hass.states.async_set(HELPER_1, "Bruno")
        await hass.async_block_till_done()
    await entry.runtime_data.picpak.async_ensure_room("chambre_test")
    assert await (await client.get(_path(urls["1"]))).read() != free


async def test_next_service_and_preview(hass: HomeAssistant, hass_client) -> None:
    FakeImmich.reset({ALEXIA_OWNERS: 6})
    entry = await setup_entry(hass, {HELPER_1: "Alexia"}, _entry(door=True))
    await hass.async_block_till_done()
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_PICPAK_NEXT, {"room": "chambre_test"}, blocking=True,
        return_response=True,
    )
    assert resp == {"frames": 2}  # la porte n'a pas de photos
    manager = entry.runtime_data.picpak
    assert manager.frames["chambre_test/1"].force_next

    # Aperçu d'un cadre jamais connecté : l'image qu'il affichera.
    client = await hass_client()
    state = hass.states.get("image.picpak_chambre_test_porte")
    assert state is not None
    preview = await client.get(f"/api/image_proxy/{state.entity_id}")
    assert preview.status == 200
    assert (await preview.read())[:8] == b"\x89PNG\r\n\x1a\n"
    assert hass.states.get("sensor.picpak_chambre_test_porte_batterie") is not None


async def test_dashboard_image_shows_photos_and_planned_dates(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Dashboards : photos dès que l'invité est renseigné, dates prévues en
    attributs, une photo toutes les 15 s."""
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 19, 19, 0, tzinfo=tz))
    FakeImmich.reset({ALEXIA_OWNERS: 8})
    _calendar(hass, [{"start": "2026-10-24", "end": "2026-10-26", "summary": "Alexia"}])
    entry = await setup_entry(
        hass, {HELPER_1: "Alexia"}, _entry(calendar=CALENDAR, frames=0)
    )
    state = hass.states.get("image.immich_chambre_test")
    assert state is not None and state.state != "unavailable"
    attrs = state.attributes
    assert attrs["sejour"] == "programme"
    assert attrs["sejour_dates"] == "sam. 24 → dim. 25 oct."
    assert (attrs["sejour_debut"], attrs["sejour_fin"]) == ("2026-10-24", "2026-10-25")
    assert attrs["invites"] == ["Alexia"] and attrs["photo_count"] == 8
    assert attrs["object_position"] == "50% 30%"
    coordinator = entry.runtime_data.rooms["chambre_test"].coordinator
    assert coordinator.update_interval.total_seconds() == 15

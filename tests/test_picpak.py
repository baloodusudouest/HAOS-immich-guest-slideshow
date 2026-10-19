"""Tests des cadres e-ink PicPak : rendu, rythme et vue HTTP."""
from __future__ import annotations

import io
from datetime import datetime, time, timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.immich_guest_slideshow import picpak_render as pr
from custom_components.immich_guest_slideshow.const import (
    CONF_API_KEY,
    CONF_PERMANENT_PERSONS,
    CONF_PICPAK_INTERVAL,
    CONF_PICPAK_PHOTOS,
    CONF_ROOMS,
    CONF_URL,
    DOMAIN,
    KEY_HELPERS,
    KEY_NAME,
    SERVICE_PICPAK_NEXT,
    SERVICE_PICPAK_URLS,
)
from custom_components.immich_guest_slideshow.picpak import (
    parse_quiet,
    quiet_remaining,
)

HELPER_1 = "input_text.guest_chambre_test"
HELPER_2 = "input_text.guest_chambre_test2"
PEOPLE = {
    "Propriétaire 1": "aaaaaaaa-0000-4000-8000-000000000001",
    "Propriétaire 2": "aaaaaaaa-0000-4000-8000-000000000002",
    "Alexia": "1238c4da-3b98-41b9-b795-81d51e95a6d5",
    "Bruno": "bbbbbbbb-0000-4000-8000-000000000003",
}


# --------------------------------------------------------------------------- #
# Rendu (pur)
# --------------------------------------------------------------------------- #
def _jpeg(color: tuple[int, int, int], size: tuple[int, int] = (640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_pack_unpack_round_trip() -> None:
    codes = bytes((i * 7 + i // 400) % 4 for i in range(400 * 300))
    frame = pr.pack(codes)
    assert len(frame) == pr.FRAME_BYTES
    assert pr.unpack(frame) == codes


def test_pack_flips_rows_and_packs_msb_first() -> None:
    codes = bytearray(400 * 300)  # tout noir
    codes[0:4] = bytes([pr.WHITE, pr.YELLOW, pr.RED, pr.BLACK])  # haut-gauche
    frame = pr.pack(bytes(codes))
    # La première ligne affichée est stockée en DERNIER (retournement).
    assert frame[-100] == (1 << 6) | (2 << 4) | (3 << 2) | 0
    assert frame[0] == 0


def test_solid_colors_map_to_their_code() -> None:
    for color, code in (
        ((255, 0, 0), pr.RED),
        ((255, 255, 0), pr.YELLOW),
        ((255, 255, 255), pr.WHITE),
        ((0, 0, 0), pr.BLACK),
        ((0, 0, 255), pr.BLACK),  # pas de bleu sur la dalle
    ):
        img = Image.new("RGB", (400, 300), color)
        assert set(pr.quantize(img)) == {code}, color


def test_render_photo_and_logo_sizes() -> None:
    assert len(pr.render_photo(_jpeg((200, 120, 60), (1440, 1080)))) == pr.FRAME_BYTES
    assert len(pr.render_photo(_jpeg((10, 200, 30), (480, 1440)))) == pr.FRAME_BYTES
    assert len(pr.render_logo(None, "Petit Lecussan", "Moirax")) == pr.FRAME_BYTES
    assert len(pr.preview_png(pr.render_logo())) > 100


def _face(x1, y1, x2, y2, w, h, pid=None) -> dict:
    return {
        "boundingBoxX1": x1, "boundingBoxY1": y1,
        "boundingBoxX2": x2, "boundingBoxY2": y2,
        "imageWidth": w, "imageHeight": h,
        "person": {"id": pid} if pid else None,
    }


def test_face_box_prefers_the_guest() -> None:
    faces = [
        _face(0, 0, 100, 100, 1000, 500, "autre"),
        _face(800, 300, 900, 400, 1000, 500, "invite"),
    ]
    assert pr.face_box(faces, "invite") == (0.8, 0.6, 0.9, 0.8)
    # Invité absent de la photo : tous les visages
    assert pr.face_box(faces, "inconnu") == (0.0, 0.0, 0.9, 0.8)
    assert pr.face_box([], "invite") is None
    assert pr.face_box([{"imageWidth": 0}], "invite") is None


def test_cover_crop_fills_the_frame_and_keeps_the_face() -> None:
    """Paysage 16:9, visage tout à droite : pas de bande, visage conservé."""
    img = Image.new("RGB", (1600, 900), (0, 0, 0))
    img.paste((255, 0, 0), (1450, 300, 1550, 400))  # « visage » rouge
    box = (1450 / 1600, 300 / 900, 1550 / 1600, 400 / 900)
    out = pr.cover_crop(img, box)
    assert out.size == (400, 300)
    reds = [
        (x, y) for x in range(0, 400, 4) for y in range(0, 300, 4)
        if out.getpixel((x, y))[0] > 200 and out.getpixel((x, y))[1] < 50
    ]
    assert reds, "le visage doit rester visible"
    xs = [x for x, _ in reds]
    assert min(xs) > 0 and max(xs) < 399  # entièrement dans le cadre
    # Sans visage : recadrage centré, toujours plein cadre
    assert pr.cover_crop(img, None).size == (400, 300)


def test_cover_crop_portrait_centres_the_face_vertically() -> None:
    img = Image.new("RGB", (600, 1200), (0, 0, 0))
    img.paste((255, 255, 255), (250, 900, 350, 1000))  # visage en bas
    out = pr.cover_crop(img, (250 / 600, 900 / 1200, 350 / 600, 1000 / 1200))
    # Le visage (blanc) est au centre vertical de la fenêtre
    assert out.getpixel((200, 150)) == (255, 255, 255)


def test_render_photo_has_no_white_bands() -> None:
    frame = pr.render_photo(_jpeg((200, 30, 30), (1600, 900)))
    codes = pr.unpack(frame)
    # Colonne de gauche et rangée du haut : rouge, pas de bande blanche
    assert codes[0] != pr.WHITE and codes[150 * 400] != pr.WHITE


def test_full_page_logo_is_used_as_is(tmp_path) -> None:
    logo = Image.new("RGB", (400, 300), (0, 0, 0))
    logo.paste((255, 255, 0), (150, 100, 250, 200))
    path = tmp_path / "logo.png"
    logo.save(path)
    codes = pr.unpack(pr.render_logo(str(path)))
    assert codes[0] == pr.BLACK  # pas de marge blanche
    assert codes[150 * 400 + 200] == pr.YELLOW


def test_unreadable_logo_falls_back_to_text(tmp_path) -> None:
    path = tmp_path / "logo.svg"
    path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert pr.render_logo(str(path), "Petit Lecussan") == pr.render_logo(
        None, "Petit Lecussan"
    )


# --------------------------------------------------------------------------- #
# Pause nocturne
# --------------------------------------------------------------------------- #
def test_parse_quiet() -> None:
    assert parse_quiet("23:00-07:00") == (time(23, 0), time(7, 0))
    assert parse_quiet(" 1:30 - 6:00 ") == (time(1, 30), time(6, 0))
    assert parse_quiet("") is None
    assert parse_quiet("25:00-07:00") is None
    assert parse_quiet("07:00-07:00") is None


def test_quiet_remaining_across_midnight() -> None:
    quiet = parse_quiet("23:00-07:00")
    tz = dt_util.get_default_time_zone()
    night = datetime(2026, 10, 18, 23, 30, tzinfo=tz)
    assert quiet_remaining(night, quiet) == 7.5 * 3600
    early = datetime(2026, 10, 19, 6, 0, tzinfo=tz)
    assert quiet_remaining(early, quiet) == 3600
    day = datetime(2026, 10, 19, 12, 0, tzinfo=tz)
    assert quiet_remaining(day, quiet) is None
    assert quiet_remaining(day, None) is None


# --------------------------------------------------------------------------- #
# Intégration complète : sélection, rythme, vue HTTP
# --------------------------------------------------------------------------- #
class FakeImmich:
    """Client Immich factice : une photo unie par asset."""

    photos: dict[frozenset[str], int] = {}
    fail: set[str] = set()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def async_validate_connection(self) -> dict[str, Any]:
        return {"version": "3.0.1"}

    async def async_get_person(self, person_id: str) -> dict[str, Any] | None:
        for name, pid in PEOPLE.items():
            if pid == person_id:
                return {"id": pid, "name": name}
        return None

    async def async_find_person(self, name: str) -> dict[str, Any] | None:
        pid = PEOPLE.get(name)
        return {"id": pid, "name": name} if pid else None

    async def async_search_assets(
        self, person_ids: list[str], **kwargs: Any
    ) -> list[dict[str, Any]]:
        count = self.photos.get(frozenset(person_ids), 0)
        key = "-".join(sorted(person_ids))
        return [{"id": f"{key}#{i}"} for i in range(count)]

    async def async_get_faces(self, asset_id: str) -> list[dict[str, Any]]:
        return []

    async def async_get_thumbnail(self, asset_id: str, **kwargs: Any) -> bytes:
        from custom_components.immich_guest_slideshow.api import ImmichApiError

        if asset_id in self.fail:
            raise ImmichApiError("boom")
        n = int(asset_id.rsplit("#", 1)[1])
        return _jpeg(((n * 40) % 256, (n * 90) % 256, (n * 15) % 256))



def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "http://immich.test:2283", CONF_API_KEY: "k"},
        options={
            CONF_PERMANENT_PERSONS: ["Propriétaire 1", "Propriétaire 2"],
            CONF_ROOMS: {
                "chambre_test": {
                    KEY_NAME: "Chambre test",
                    KEY_HELPERS: [HELPER_1, HELPER_2],
                },
            },
            CONF_PICPAK_INTERVAL: 60,
            CONF_PICPAK_PHOTOS: 5,
        },
        unique_id="http://immich.test:2283",
    )


async def _setup(hass: HomeAssistant, guest_1: str, guest_2: str = ""):
    hass.config.internal_url = "http://192.168.1.29:8123"
    hass.states.async_set(HELPER_1, guest_1)
    hass.states.async_set(HELPER_2, guest_2)
    entry = _entry()
    entry.add_to_hass(hass)
    patcher = patch(
        "custom_components.immich_guest_slideshow.ImmichApiClient", FakeImmich
    )
    patcher.start()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, patcher


async def _urls(hass: HomeAssistant) -> dict[int, str]:
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_PICPAK_URLS, {}, blocking=True, return_response=True
    )
    return {f["bed"]: f["url"] for f in resp["frames"]}


def _path(url: str) -> str:
    return url.split("8123", 1)[1]


async def test_single_guest_feeds_both_frames(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    FakeImmich.photos = {frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 10}
    FakeImmich.fail = set()
    entry, patcher = await _setup(hass, "Alexia")
    try:
        urls = await _urls(hass)
        assert set(urls) == {1, 2}
        assert all(len(u) <= 128 for u in urls.values())
        client = await hass_client_no_auth()

        # Jeton invalide -> refusé
        bad = await client.get(_path(urls[1]).replace("?k=", "?k=x"))
        assert bad.status == 403

        r1 = await client.get(
            _path(urls[1]), headers={"X-Picpak-Battery": "4100;87", "X-Picpak-Wake": "timer"}
        )
        assert r1.status == 200
        assert r1.headers["X-Next-Wake-Seconds"] == "3600"
        body1 = await r1.read()
        assert len(body1) == pr.FRAME_BYTES

        manager = entry.runtime_data.picpak
        f1 = manager.frames["chambre_test/1"]
        f2 = manager.frames["chambre_test/2"]
        assert f1.guest_name == f2.guest_name == "Alexia"
        assert len(f1.assets) == len(f2.assets) == 5
        assert not set(f1.assets) & set(f2.assets)  # photos différentes

        await hass.async_block_till_done()
        state = hass.states.get("sensor.picpak_chambre_test_1_batterie")
        assert state.state == "87"
        assert state.attributes["tension_mv"] == 4100
        assert hass.states.get("image.picpak_chambre_test_1") is not None

        # Nouvel essai 5 min plus tard : même image (pas de rafraîchissement)
        freezer.tick(timedelta(minutes=5))
        again = await (await client.get(_path(urls[1]))).read()
        assert again == body1

        # Une heure plus tard : photo suivante
        freezer.tick(timedelta(minutes=56))
        nxt = await (await client.get(_path(urls[1]))).read()
        assert nxt != body1
        assert f1.index == 1

        # Bouton : photo suivante immédiatement
        btn = await (
            await client.get(_path(urls[1]), headers={"X-Picpak-Wake": "button"})
        ).read()
        assert btn != nxt and f1.index == 2

        # Service : photo suivante au prochain réveil
        await hass.services.async_call(
            DOMAIN, SERVICE_PICPAK_NEXT, {"room": "chambre_test", "bed": 1}, blocking=True
        )
        await client.get(_path(urls[1]))
        assert f1.index == 3
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        patcher.stop()


async def test_two_guests_and_checkout_shows_logo(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    FakeImmich.photos = {
        frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 4,
        frozenset([PEOPLE["Bruno"]]): 3,
    }
    FakeImmich.fail = set()
    entry, patcher = await _setup(hass, "Alexia", "Bruno")
    try:
        urls = await _urls(hass)
        client = await hass_client_no_auth()
        await client.get(_path(urls[1]))
        await client.get(_path(urls[2]))
        manager = entry.runtime_data.picpak
        assert manager.frames["chambre_test/1"].guest_name == "Alexia"
        assert manager.frames["chambre_test/2"].guest_name == "Bruno"
        assert len(manager.frames["chambre_test/2"].assets) == 3  # repli individuel

        # Départ : les deux helpers vidés -> logo
        hass.states.async_set(HELPER_1, "")
        hass.states.async_set(HELPER_2, "")
        await hass.async_block_till_done()
        logo = await (await client.get(_path(urls[1]))).read()
        assert logo == pr.render_logo(None, "Bienvenue", "")
        assert manager.frames["chambre_test/1"].assets == []
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        patcher.stop()


async def test_unreachable_photo_is_skipped(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    pid = "-".join(sorted([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]))
    FakeImmich.photos = {frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 6}
    entry, patcher = await _setup(hass, "")
    try:
        manager = entry.runtime_data.picpak
        hass.states.async_set(HELPER_1, "Alexia")
        await hass.async_block_till_done()
        frame = manager.frames["chambre_test/1"]
        FakeImmich.fail = {frame.assets[0]}
        # La photo avait déjà été préparée en arrière-plan : on oublie ce rendu
        # pour que le réveil doive la télécharger (et échoue).
        for task in list(manager._render_tasks.values()):
            await task  # tâches d'arrière-plan : non attendues par HA
        manager._rendered.pop(frame.assets[0], None)
        urls = await _urls(hass)
        client = await hass_client_no_auth()
        resp = await client.get(_path(urls[1]))
        assert resp.status == 200
        assert frame.shown == frame.assets[1]
        assert frame.shown.startswith(pid)
    finally:
        FakeImmich.fail = set()
        await hass.config_entries.async_unload(entry.entry_id)
        patcher.stop()


async def test_screen_preview_before_first_wake(
    hass: HomeAssistant, hass_client
) -> None:
    """Avant tout réveil du cadre, l'entité image montre l'image à venir."""
    FakeImmich.photos = {frozenset([PEOPLE["Alexia"], PEOPLE["Propriétaire 1"]]): 4}
    FakeImmich.fail = set()
    entry, patcher = await _setup(hass, "Alexia")
    try:
        manager = entry.runtime_data.picpak
        # Attend la sélection lancée au démarrage (tâche d'arrière-plan).
        await manager.async_ensure_room("chambre_test")
        await hass.async_block_till_done()
        client = await hass_client()
        state = hass.states.get("image.picpak_chambre_test_1")
        resp = await client.get(state.attributes["entity_picture"])
        assert resp.status == 200
        assert resp.content_type == "image/png"
        manager = entry.runtime_data.picpak
        frame = manager.frames["chambre_test/1"]
        assert frame.last_seen is None  # jamais réveillé
        assert frame.assets  # sélection faite dès le démarrage
        rendered = await manager._async_render_asset(frame.assets[0], None)
        expected = pr.preview_png(rendered)
        assert await resp.read() == expected

        # Chambre libre : le logo
        hass.states.async_set(HELPER_1, "")
        await hass.async_block_till_done()
        await manager.async_ensure_room("chambre_test")
        await hass.async_block_till_done()
        state = hass.states.get("image.picpak_chambre_test_1")
        resp = await client.get(state.attributes["entity_picture"])
        assert resp.status == 200
        assert await resp.read() == pr.preview_png(pr.render_logo(None, "Bienvenue", ""))
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        patcher.stop()

"""Portraits HD de Carnet (2.4.0) : client, cadrage, cadre de porte, dashboards."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from PIL import Image
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.immich_guest_slideshow import picpak_render as pr
from custom_components.immich_guest_slideshow.carnet import (
    CarnetAuthError,
    CarnetClient,
    CarnetError,
    clean_token,
    parse_face_box,
)
from custom_components.immich_guest_slideshow.const import (
    CONF_CARNET_TOKEN,
    CONF_CARNET_URL,
    CONF_PICPAK_INTERVAL,
    CONF_PICPAK_PHOTOS,
    DOMAIN,
    SERVICE_PICPAK_URLS,
)

from .common import (
    HELPER_1,
    HELPER_2,
    PEOPLE,
    FakeImmich,
    make_entry,
    patch_immich,
    room_subentry,
)

CALENDAR = "calendar.chambre_test"
CARNET = "http://192.168.1.90:8096"
# Portrait Carnet « centre » : visage entier au milieu (côté = 1,3 × (w + h)).
CARNET_BOX = (0.31, 0.35, 0.69, 0.73)


def _portrait_jpeg(box=CARNET_BOX, size: int = 800) -> bytes:
    """Fond gris, « visage » rouge dans ``box`` : on peut vérifier le cadrage."""
    img = Image.new("RGB", (size, size), (120, 120, 120))
    x1, y1, x2, y2 = (int(v * size) for v in box)
    img.paste((220, 30, 30), (x1, y1, x2, y2))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def test_clean_token_and_face_header() -> None:
    assert clean_token("Bearer abc ") == "abc"
    assert clean_token(" abc") == "abc"
    assert parse_face_box("0.3100,0.3500,0.6900,0.7300") == CARNET_BOX
    assert parse_face_box("-1,0,2,1") == (0.0, 0.0, 1.0, 1.0)  # bornée
    for bad in (None, "", "1,2,3", "a,b,c,d", "0.5,0.5,0.5,0.9"):
        assert parse_face_box(bad) is None


async def test_client_portrait(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    jpeg = _portrait_jpeg()
    aioclient_mock.get(
        f"{CARNET}/ha/portrait?immich=p1&cadre=centre",
        content=jpeg,
        headers={"Content-Type": "image/jpeg", "X-Visage": "0.31,0.35,0.69,0.73"},
    )
    aioclient_mock.get(f"{CARNET}/ha/portrait?immich=p2&cadre=centre", status=404)
    aioclient_mock.get(f"{CARNET}/ha/portrait?immich=p3&cadre=centre", status=401)
    aioclient_mock.get(f"{CARNET}/ha/portrait?immich=p4&cadre=centre", status=500)
    aioclient_mock.get(f"{CARNET}/ha/personnes", json={"personnes": []})
    client = CarnetClient(async_get_clientsession(hass), CARNET + "/", "Bearer jeton")
    assert await client.async_get_portrait("p1") == (jpeg, CARNET_BOX)
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer jeton"
    assert await client.async_get_portrait("p2") is None
    for pid, exc in (("p3", CarnetAuthError), ("p4", CarnetError)):
        try:
            await client.async_get_portrait(pid)
        except exc:
            pass
        else:  # pragma: no cover
            raise AssertionError(pid)
    await client.async_validate()


# --------------------------------------------------------------------------- #
# Cadrage dans le losange
# --------------------------------------------------------------------------- #
def test_face_crop_keeps_the_whole_face_centred() -> None:
    # Portrait Carnet « centre » : gardé (presque) entier.
    left, top, right, bottom = pr.face_crop(600, 600, CARNET_BOX)
    assert right - left == bottom - top >= 580
    # Grande photo : carré centré, assez grand pour le visage entier.
    box = (0.40, 0.30, 0.60, 0.56)  # 1200 × 900
    left, top, right, bottom = pr.face_crop(1200, 900, box)
    side = right - left
    assert side == bottom - top
    fw, fh = 0.2 * 1200, 0.26 * 900
    assert fw + fh < side  # un rectangle w × h tient dans le losange si w + h <= côté
    assert abs((left + right) / 2 - 600) <= 1
    assert (top + bottom) / 2 < (0.30 + 0.56) / 2 * 900  # remonté (cheveux)
    # Visage près du bord : carré réduit et décalé, jamais hors de l'image.
    left, top, right, bottom = pr.face_crop(800, 800, (0.05, 0.02, 0.6, 0.7))
    assert left >= 0 and top >= 0 and right <= 800 and bottom <= 800


def test_door_card_with_carnet_portrait_shows_the_whole_face() -> None:
    portrait = _portrait_jpeg()
    with_box = pr.render_door("Chambre d'ami", [("Paul", portrait, CARNET_BOX)], "sam. 31 oct.")
    without = pr.render_door("Chambre d'ami", [("Paul", portrait)], "sam. 31 oct.")
    assert with_box != without
    img = Image.open(io.BytesIO(pr.preview_png(with_box))).convert("RGB")
    # Losange d'un invité : centre (200, 132), demi-diagonale intérieure ≈ 75.
    red = [
        (x, y)
        for x in range(120, 281)
        for y in range(52, 213)
        if img.getpixel((x, y))[0] > 150 and img.getpixel((x, y))[1] < 90
    ]
    assert red
    xs = [p[0] for p in red]
    # Le visage entier est dans le losange (aucun coin coupé) et centré.
    assert all(abs(x - 200) + abs(y - 132) <= 76 for x, y in red)
    assert abs((min(xs) + max(xs)) / 2 - 200) <= 3


# --------------------------------------------------------------------------- #
# Intégration : cadre de porte, vue des portraits, attribut des dashboards
# --------------------------------------------------------------------------- #
class FakeCarnet:
    """Carnet factice : portrait HD pour Carla seulement."""

    calls: list[str] = []
    down = False

    def __init__(self, session: Any, url: str, token: str) -> None:
        self.url = url

    async def async_get_portrait(self, person_id: str):
        FakeCarnet.calls.append(person_id)
        if FakeCarnet.down:
            raise CarnetError("injoignable")
        if person_id == PEOPLE["Carla Dupont"]:
            return _portrait_jpeg(), CARNET_BOX
        return None


def _calendar(hass: HomeAssistant, events: list[dict]) -> None:
    hass.states.async_set(CALENDAR, "off")

    async def get_events(call):
        return {CALENDAR: {"events": events}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )


async def _setup(hass: HomeAssistant, carnet: bool = True):
    hass.config.internal_url = "http://192.168.1.29:8123"
    hass.states.async_set(HELPER_1, "Carla Dupont")
    hass.states.async_set(HELPER_2, "Bruno")
    options = {CONF_PICPAK_INTERVAL: 60, CONF_PICPAK_PHOTOS: 5}
    if carnet:
        options.update({CONF_CARNET_URL: CARNET, CONF_CARNET_TOKEN: "jeton"})
    entry = make_entry([room_subentry(calendar=CALENDAR, door=True, frames=0)], **options)
    entry.add_to_hass(hass)
    with patch_immich(), patch(
        "custom_components.immich_guest_slideshow.CarnetClient", FakeCarnet
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_door_frame_uses_carnet_then_immich(
    hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory
) -> None:
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 23, 10, 0, tzinfo=tz))
    FakeImmich.reset({frozenset([PEOPLE["Carla Dupont"], PEOPLE["Propriétaire 1"]]): 3})
    FakeCarnet.calls, FakeCarnet.down = [], False
    _calendar(hass, [{"start": "2026-10-23", "end": "2026-10-25", "summary": "Séjour"}])
    entry = await _setup(hass)
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_PICPAK_URLS, {}, blocking=True, return_response=True
    )
    url = next(f["url"] for f in resp["frames"] if f["slot"] == "porte")
    client = await hass_client_no_auth()
    body = await (await client.get(url.split("8123", 1)[1])).read()

    thumb = await FakeImmich().async_get_person_thumbnail("x")
    expected = pr.render_door(
        "Chambre test",
        [("Carla", _portrait_jpeg(), CARNET_BOX), ("Bruno", thumb, None)],
        "ven. 23 → sam. 24 oct.",
    )
    assert body == expected
    # Carla : portrait Carnet ; Bruno : pas de portrait HD -> vignette Immich.
    assert set(FakeCarnet.calls) == {PEOPLE["Carla Dupont"], PEOPLE["Bruno"]}
    assert [p for p in FakeImmich.person_thumbs if p != "x"] == [PEOPLE["Bruno"]]

    # Portraits gardés en mémoire : pas de nouvel appel l'heure suivante.
    freezer.tick(3600)
    assert await (await client.get(url.split("8123", 1)[1])).read() == body
    assert len(FakeCarnet.calls) == 2

    # Dashboards : attribut « portraits » et vue protégée par le jeton.
    state = hass.states.get("image.immich_chambre_test")
    portraits = state.attributes["portraits"]
    assert [p["nom"] for p in portraits] == ["Carla Dupont", "Bruno"]
    token = entry.runtime_data.picpak.token
    assert portraits[0]["url"].startswith(
        f"/api/immich_guest_slideshow/portrait/chambre_test/1?k={token}&v="
    )
    got = await client.get(portraits[0]["url"])
    assert got.status == 200 and got.headers["X-Portrait-Source"] == "carnet"
    assert got.content_type == "image/jpeg" and await got.read() == _portrait_jpeg()
    got = await client.get(portraits[1]["url"])
    assert got.headers["X-Portrait-Source"] == "immich"
    bad = portraits[0]["url"].replace(f"k={token}", "k=faux")
    assert (await client.get(bad)).status == 403
    missing = f"/api/immich_guest_slideshow/portrait/chambre_test/3?k={token}"
    assert (await client.get(missing)).status == 404


async def test_carnet_down_falls_back_without_caching(hass: HomeAssistant) -> None:
    FakeImmich.reset()
    FakeCarnet.calls, FakeCarnet.down = [], True
    entry = await _setup(hass)
    manager = entry.runtime_data.picpak
    first = await manager.async_portrait(PEOPLE["Carla Dupont"])
    assert first is not None and first.source == "immich"
    # Carnet revient : le portrait HD est repris tout de suite (panne non mémorisée).
    FakeCarnet.down = False
    again = await manager.async_portrait(PEOPLE["Carla Dupont"])
    assert again is not None and again.source == "carnet" and again.face == CARNET_BOX


async def test_without_carnet_only_immich(hass: HomeAssistant) -> None:
    FakeImmich.reset()
    FakeCarnet.calls = []
    entry = await _setup(hass, carnet=False)
    portrait = await entry.runtime_data.picpak.async_portrait(PEOPLE["Carla Dupont"])
    assert portrait is not None and portrait.source == "immich" and portrait.face is None
    assert FakeCarnet.calls == []

"""Rendu des écrans PicPak (module pur, sans Home Assistant)."""
from __future__ import annotations

import io

from PIL import Image

from custom_components.immich_guest_slideshow import picpak_render as pr
from custom_components.immich_guest_slideshow.picpak import PicPakSettings


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


def test_logo_caption_is_drawn_at_the_bottom(tmp_path) -> None:
    """Logo pleine page sur fond noir : légende jaune dans la bande du bas."""
    logo = Image.new("RGB", (400, 300), (0, 0, 0))
    logo.paste((255, 255, 0), (100, 0, 300, 300))  # bande jaune sur toute la hauteur
    path = tmp_path / "logo.png"
    logo.save(path)
    plain = pr.unpack(pr.render_logo(str(path)))
    captioned = pr.unpack(pr.render_logo(str(path), caption="Chambre d'ami invité N°1"))
    assert plain != captioned
    band = captioned[(300 - pr.CAPTION_BAND) * 400:]
    # La bande du bas contient du texte jaune sur le fond noir…
    assert pr.YELLOW in band and pr.BLACK in band
    # … et le logo réduit ne déborde pas dedans (les coins restent noirs).
    assert band[0] == pr.BLACK and band[-1] == pr.BLACK


def test_bundled_font_has_french_accents() -> None:
    """La police livrée doit couvrir les accents, même sans police système."""
    import os

    from PIL import ImageFont

    path = os.path.join(os.path.dirname(pr.__file__), "fonts", "DejaVuSerif-Bold.ttf")
    font = ImageFont.truetype(path, 20)
    font_r = ImageFont.truetype(path.replace("-Bold", ""), 20)
    for f in (font, font_r):
        for ch in "éèêàçôûîœ→N°":
            # Une lettre absente est dessinée comme le glyphe « .notdef ».
            assert f.getmask(ch).getbbox() != f.getmask("￿").getbbox(), ch
    assert pr._font(20, bold=True).path == path  # noqa: SLF001




def test_caption_three_lines_planned_stay(tmp_path) -> None:
    """Séjour programmé : chambre, invités et dates sous le logo."""
    one = pr.unpack(pr.render_logo(None, "Petit Lecussan", "", "Chambre d'ami"))
    three = pr.unpack(
        pr.render_logo(
            None, "Petit Lecussan", "", "Chambre d'ami\nAlexia et Bruno\nven. 23 → sam. 24 oct."
        )
    )
    assert one != three
    band = three[(300 - pr.CAPTION_BAND - 40) * 400:]
    assert pr.YELLOW in band and pr.BLACK in band


def test_text_logo_is_gold_on_black() -> None:
    codes = pr.unpack(pr.render_logo(None, "Petit Lecussan"))
    assert codes[0] == pr.BLACK
    assert pr.YELLOW in codes and pr.WHITE not in codes[:400]


def test_second_logo_only_for_frame_two() -> None:
    s = PicPakSettings(60, 50, None, "/a.png", "", "", "", logo_path_2="/b.png")
    assert [s.logo_for(x) for x in ("1", "2", "porte")] == ["/a.png", "/b.png", "/a.png"]
    s.logo_path_2 = ""
    assert s.logo_for("2") == "/a.png"


def test_welcome_line() -> None:
    assert pr.welcome_line("Chambre d'ami") == "bienvenue dans la chambre d'ami"
    assert pr.welcome_line("Stitch") == "bienvenue dans la chambre Stitch"


def test_door_card_one_and_two_guests() -> None:
    face = _jpeg((210, 160, 130), (250, 250))
    one = pr.render_door("Chambre d'ami", [("Corinne", face)], "ven. 23 → sam. 24 oct.")
    two = pr.render_door(
        "Chambre d'ami", [("Corinne", face), ("Régis", None)], "ven. 23 → sam. 24 oct."
    )
    assert len(one) == len(two) == pr.FRAME_BYTES
    img1 = Image.open(io.BytesIO(pr.preview_png(one))).convert("RGB")
    img2 = Image.open(io.BytesIO(pr.preview_png(two))).convert("RGB")
    bg = img1.getpixel((2, 2))
    # Un invité : visage centré ; deux : côte à côte, espace au milieu.
    assert img1.getpixel((200, 105)) != bg and img1.getpixel((60, 105)) == bg
    assert img2.getpixel((120, 105)) != bg  # visage
    assert {img2.getpixel((280, y)) for y in range(30, 180)} - {bg}  # initiales + cercle
    assert img2.getpixel((200, 105)) == bg
    # Texte sous les visages (prénoms, bienvenue, dates).
    below = {img2.getpixel((x, y)) for x in range(40, 360, 2) for y in range(190, 270, 2)}
    assert below - {bg}

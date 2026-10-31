"""Rendu des images pour les cadres PicPak (e-ink 4,2″, 400×300, 4 couleurs).

Module pur (sans Home Assistant) : il transforme une photo JPEG en tampon
d'affichage de 30 000 octets, au format exact attendu par la dalle.

Pipeline (référence : documentation/image-pipeline.md du projet open-PicPak) :

1. recadrage plein cadre 400×300 (aucune bande), centré sur le visage de
   l'invité quand Immich l'a détecté, sinon légèrement au-dessus du centre ;
2. quantification au plus proche des 4 couleurs (distance RVB pondérée
   BT.601 : 0,299 / 0,587 / 0,114) ;
3. tramage d'Atkinson (erreur répartie 1/8 sur 6 voisins, NON bornée) ;
4. retournement vertical ;
5. empaquetage 2 bits/pixel, 4 pixels/octet, bit de poids fort en premier.

Toutes les fonctions sont synchrones et coûteuses en CPU : Home Assistant les
appelle dans un exécuteur, jamais dans la boucle d'événements.
"""
from __future__ import annotations

import io
import os
from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from . import framing

FRAME_W = 400
FRAME_H = 300
FRAME_BYTES = FRAME_W * FRAME_H // 4  # 30 000

# Codes couleur de la dalle (ordre imposé par le format 2 bpp).
BLACK, WHITE, YELLOW, RED = 0, 1, 2, 3

# Ancres logiques utilisées pour le calcul (identiques à l'appli d'origine).
PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
    (255, 255, 0),
    (255, 0, 0),
)
# Couleurs réellement affichées par la dalle : uniquement pour l'aperçu.
PANEL_PREVIEW: tuple[tuple[int, int, int], ...] = (
    (18, 20, 18),
    (205, 206, 198),
    (202, 174, 62),
    (164, 63, 55),
)

_LW_R, _LW_G, _LW_B = 0.299, 0.587, 0.114


# --------------------------------------------------------------------------- #
# Recadrage plein cadre, centré sur les visages
# --------------------------------------------------------------------------- #
# Boîte normalisée (x1, y1, x2, y2), coordonnées 0..1 dans l'image source.
Box = framing.Box

# Visée sans visage connu : un peu au-dessus du centre, là où se trouvent les
# têtes sur la plupart des photos.
DEFAULT_FOCUS = (0.5, 0.42)


def face_box(faces: Sequence[dict], person_id: str | None) -> Box | None:
    """Boîte englobant les visages à garder dans le cadre.

    Priorité au(x) visage(s) de l'invité ``person_id`` ; à défaut (invité non
    reconnu sur cette photo), tous les visages détectés. ``None`` si Immich
    n'a détecté aucun visage exploitable. Voir :func:`framing.faces_box`.
    """
    return framing.faces_box(faces, [person_id])


def _window(start_hint: float, size: float, total: float, lo: float, hi: float) -> float:
    """Position (en pixels) d'une fenêtre de ``size`` dans ``total``.

    Centrée sur ``start_hint`` (centre souhaité) ; si la zone [lo, hi] à
    garder tient dans la fenêtre, on la décale juste assez pour l'inclure
    entièrement. Toujours bornée à l'image.
    """
    pos = start_hint - size / 2
    if hi - lo <= size:
        pos = min(max(pos, hi - size), lo)
    return min(max(0.0, pos), total - size)


def cover_crop(
    img: Image.Image,
    box: Box | None = None,
    size: tuple[int, int] = (FRAME_W, FRAME_H),
) -> Image.Image:
    """Recadre ``img`` pour REMPLIR ``size`` (aucune bande), sans déformation.

    La photo est mise à l'échelle pour couvrir tout le cadre, puis la fenêtre
    est placée de façon à centrer la boîte des visages ``box`` ; si aucun
    visage n'est connu, visée légèrement au-dessus du centre.
    """
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(tw, round(sw * scale)), max(th, round(sh * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    if box is None:
        cx, cy = DEFAULT_FOCUS[0] * nw, DEFAULT_FOCUS[1] * nh
        x1 = x2 = cx
        y1 = y2 = cy
    else:
        x1, y1, x2, y2 = box[0] * nw, box[1] * nh, box[2] * nw, box[3] * nh
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left = round(_window(cx, tw, nw, x1, x2))
    top = round(_window(cy, th, nh, y1, y2))
    return img.crop((left, top, left + tw, top + th))


# --------------------------------------------------------------------------- #
# Quantification, tramage, empaquetage
# --------------------------------------------------------------------------- #
def quantize(img: Image.Image, dither: bool = True) -> bytes:
    """Convertit une image RVB 400×300 en codes couleur (1 octet par pixel).

    Erreur d'Atkinson non bornée, calculée en valeurs sRGB (0..255), comme
    l'appli d'origine : c'est ce qui donne le rendu observé sur la dalle.
    """
    if img.size != (FRAME_W, FRAME_H):
        raise ValueError(f"image {img.size}, attendu {(FRAME_W, FRAME_H)}")
    raw = img.convert("RGB").tobytes()
    w, h = FRAME_W, FRAME_H
    n = w * h
    r = [float(v) for v in raw[0::3]]
    g = [float(v) for v in raw[1::3]]
    b = [float(v) for v in raw[2::3]]
    codes = bytearray(n)

    # Distances au carré pondérées vers les 4 ancres, développées à la main :
    # c'est la boucle chaude (120 000 pixels).
    for y in range(h):
        row = y * w
        down1 = y + 1 < h
        down2 = y + 2 < h
        for x in range(w):
            i = row + x
            R = r[i]
            G = g[i]
            B = b[i]
            Rm = R - 255.0
            Gm = G - 255.0
            Bm = B - 255.0
            dk = _LW_R * R * R + _LW_G * G * G + _LW_B * B * B
            dw = _LW_R * Rm * Rm + _LW_G * Gm * Gm + _LW_B * Bm * Bm
            dy = _LW_R * Rm * Rm + _LW_G * Gm * Gm + _LW_B * B * B
            dr = _LW_R * Rm * Rm + _LW_G * G * G + _LW_B * B * B
            code, best = BLACK, dk
            if dw < best:
                code, best = WHITE, dw
            if dy < best:
                code, best = YELLOW, dy
            if dr < best:
                code = RED
            codes[i] = code
            if not dither:
                continue
            pr, pg, pb = PALETTE[code]
            eR = (R - pr) * 0.125
            eG = (G - pg) * 0.125
            eB = (B - pb) * 0.125
            # (x+1, y) (x+2, y)
            if x + 1 < w:
                j = i + 1
                r[j] += eR
                g[j] += eG
                b[j] += eB
                if x + 2 < w:
                    j = i + 2
                    r[j] += eR
                    g[j] += eG
                    b[j] += eB
            if down1:
                # (x-1, y+1) (x, y+1) (x+1, y+1)
                j = i + w
                r[j] += eR
                g[j] += eG
                b[j] += eB
                if x > 0:
                    r[j - 1] += eR
                    g[j - 1] += eG
                    b[j - 1] += eB
                if x + 1 < w:
                    r[j + 1] += eR
                    g[j + 1] += eG
                    b[j + 1] += eB
                if down2:
                    # (x, y+2)
                    j += w
                    r[j] += eR
                    g[j] += eG
                    b[j] += eB
    return bytes(codes)


def pack(codes: bytes) -> bytes:
    """Retournement vertical + empaquetage 2 bpp (MSB en premier) -> 30 000 o."""
    w, h = FRAME_W, FRAME_H
    out = bytearray(FRAME_BYTES)
    o = 0
    for y in range(h - 1, -1, -1):
        base = y * w
        for x in range(0, w, 4):
            i = base + x
            out[o] = (
                (codes[i] << 6)
                | (codes[i + 1] << 4)
                | (codes[i + 2] << 2)
                | codes[i + 3]
            )
            o += 1
    return bytes(out)


def unpack(frame: bytes) -> bytes:
    """Inverse de :func:`pack` : codes couleur dans l'ordre d'affichage."""
    if len(frame) != FRAME_BYTES:
        raise ValueError(f"tampon de {len(frame)} octets, attendu {FRAME_BYTES}")
    w, h = FRAME_W, FRAME_H
    codes = bytearray(w * h)
    o = 0
    for y in range(h - 1, -1, -1):
        base = y * w
        for x in range(0, w, 4):
            v = frame[o]
            o += 1
            i = base + x
            codes[i] = (v >> 6) & 3
            codes[i + 1] = (v >> 4) & 3
            codes[i + 2] = (v >> 2) & 3
            codes[i + 3] = v & 3
    return bytes(codes)


def preview_png(frame: bytes) -> bytes:
    """Aperçu PNG du tampon, avec les couleurs réelles de la dalle."""
    lut = PANEL_PREVIEW
    rgb = bytearray()
    for code in unpack(frame):
        rgb.extend(lut[code])
    img = Image.frombytes("RGB", (FRAME_W, FRAME_H), bytes(rgb))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Images complètes
# --------------------------------------------------------------------------- #
def _prepare_photo(img: Image.Image) -> Image.Image:
    """Léger renfort de contraste et de saturation avant la réduction à
    4 couleurs : sans lui, les photos un peu ternes tombent dans un gris
    tramé noir/blanc peu lisible sur la dalle."""
    img = ImageOps.autocontrast(img, cutoff=0.5)
    img = ImageEnhance.Color(img).enhance(1.2)
    return img


def render_photo(jpeg: bytes, box: Box | None = None) -> bytes:
    """Photo (JPEG/PNG/WebP) -> tampon d'affichage de 30 000 octets.

    ``box`` : visages à garder au centre (voir :func:`face_box`).
    """
    with Image.open(io.BytesIO(jpeg)) as src:
        img = ImageOps.exif_transpose(src).convert("RGB")
    img = _prepare_photo(cover_crop(img, box))
    return pack(quantize(img, dither=True))


# Polices livrées avec l'intégration : Home Assistant OS n'en installe aucune,
# et la police par défaut de Pillow n'a pas les lettres accentuées (« invité »
# s'affichait « invit□ »).
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Police avec accents : celle de l'intégration, sinon celle du système."""
    name = "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"
    candidates = [os.path.join(_FONT_DIR, name), name]
    candidates.append("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Découpe ``text`` en lignes tenant dans ``max_w`` pixels."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), trial, font=font)
        if current and box[2] - box[0] > max_w:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _wordmark(title: str, subtitle: str = "") -> Image.Image:
    """Logo typographique, quand aucun fichier de logo n'est fourni.

    Titre or (jaune de la dalle), filet or, sous-titre blanc, sur fond noir :
    aplats nets sans tramage, aux couleurs de la maison.
    """
    img = Image.new("RGB", (FRAME_W, FRAME_H), PALETTE[BLACK])
    draw = ImageDraw.Draw(img)
    title = title.strip() or "Bienvenue"
    max_w = FRAME_W - 40

    # Plus grande taille de titre qui tient sur au plus 3 lignes.
    for size in (60, 54, 48, 42, 36, 30, 26):
        font = _font(size, bold=True)
        lines = _wrap(draw, title, font, max_w)
        widths = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
        if len(lines) <= 3 and all(b[2] - b[0] <= max_w for b in widths):
            break
    sub_font = _font(20)
    sub_lines = _wrap(draw, subtitle.strip(), sub_font, max_w) if subtitle.strip() else []

    gap = 10
    title_h = [b[3] - b[1] for b in (draw.textbbox((0, 0), ln, font=font) for ln in lines)]
    sub_h = [b[3] - b[1] for b in (draw.textbbox((0, 0), ln, font=sub_font) for ln in sub_lines)]
    total = sum(title_h) + gap * (len(lines) - 1) + 40
    if sub_lines:
        total += sum(sub_h) + 6 * (len(sub_lines) - 1)
    y = (FRAME_H - total) / 2

    def centered(text: str, fnt, top: float, fill) -> float:
        box = draw.textbbox((0, 0), text, font=fnt)
        tw = box[2] - box[0]
        draw.text(((FRAME_W - tw) / 2 - box[0], top - box[1]), text, font=fnt, fill=fill)
        return top + (box[3] - box[1])

    for i, ln in enumerate(lines):
        y = centered(ln, font, y, PALETTE[YELLOW]) + (gap if i < len(lines) - 1 else 0)
    draw.rectangle((110, y + 16, 290, y + 21), fill=PALETTE[YELLOW])
    y += 40
    for ln in sub_lines:
        y = centered(ln, sub_font, y, PALETTE[WHITE]) + 6
    return img


CAPTION_BAND = 30  # hauteur de la légende en bas du logo


def _add_caption(img: Image.Image, caption: str) -> Image.Image:
    """Réduit le logo pour libérer une bande en bas et y écrit ``caption``.

    Une à trois lignes (séparées par « \n ») : la 1re en gras, les suivantes
    plus petites. Le logo garde ses proportions et la couleur de son fond (prise
    dans le coin) ; la légende est en jaune sur fond sombre, en noir sur fond
    clair.
    """
    lines = [ln.strip() for ln in caption.split("\n") if ln.strip()][:3]
    if not lines:
        return img
    bg = img.getpixel((0, 0))
    luma = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    ink = PALETTE[YELLOW] if luma < 128 else PALETTE[BLACK]
    band = CAPTION_BAND + 20 * (len(lines) - 1)
    area = (FRAME_W, FRAME_H - band)
    scale = min(area[0] / img.width, area[1] / img.height)
    small = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.Resampling.LANCZOS,
    )
    out = Image.new("RGB", (FRAME_W, FRAME_H), bg)
    out.paste(small, ((FRAME_W - small.width) // 2, (area[1] - small.height) // 2))
    draw = ImageDraw.Draw(out)

    def fit(text: str, sizes: tuple[int, ...], bold: bool):
        for size in sizes:
            font = _font(size, bold=bold)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= FRAME_W - 16:
                break
        return font, box

    fonts = [fit(lines[0], (20, 18, 16, 14, 12), True)]
    fonts.extend(fit(ln, (16, 14, 12), False) for ln in lines[1:])
    gap = 4
    heights = [b[3] - b[1] for _, b in fonts]
    y = area[1] + (band - sum(heights) - gap * (len(lines) - 1)) / 2
    for text, (font, box), h in zip(lines, fonts, heights, strict=True):
        tw = box[2] - box[0]
        draw.text(((FRAME_W - tw) / 2 - box[0], y - box[1]), text, font=font, fill=ink)
        y += h + gap
    return out


def render_logo(
    path: str | None = None,
    title: str = "Bienvenue",
    subtitle: str = "",
    caption: str | None = None,
) -> bytes:
    """Écran « chambre libre » : le logo, sans tramage (aplats nets).

    ``path`` : image du logo (PNG ; le SVG n'est pas lisible). Une image de
    exactement 400×300 est affichée pleine page, telle quelle ; sinon elle est
    centrée sur fond noir sans être déformée ni rognée. Sans fichier lisible,
    un logo typographique est dessiné avec ``title`` et ``subtitle``.

    ``caption`` : légende écrite tout en bas, une à trois lignes (nom de la
    chambre ; noms des invités ; dates) ; le logo est réduit pour lui laisser
    la place. Or sur fond sombre, noir sur fond clair.
    """
    img: Image.Image | None = None
    if path and os.path.isfile(path):
        try:
            with Image.open(path) as src:
                logo = ImageOps.exif_transpose(src).convert("RGBA")
            img = Image.new("RGB", (FRAME_W, FRAME_H), PALETTE[BLACK])
            if logo.size == (FRAME_W, FRAME_H):
                # Image déjà composée pour la dalle : pleine page, telle quelle.
                img.paste(logo, (0, 0), logo)
            else:
                logo.thumbnail((FRAME_W - 40, FRAME_H - 40), Image.Resampling.LANCZOS)
                img.paste(
                    logo,
                    ((FRAME_W - logo.width) // 2, (FRAME_H - logo.height) // 2),
                    logo,
                )
        except OSError:
            # Format illisible par Pillow (ex. SVG) : logo texte de secours.
            img = None
    if img is None:
        img = _wordmark(title, subtitle)
    if caption:
        img = _add_caption(img, caption.strip())
    return pack(quantize(img, dither=False))


# --------------------------------------------------------------------------- #
# Cadre de la porte : visages des invités pendant le séjour
# --------------------------------------------------------------------------- #
# Cadrage « visage entier centré » dans un losange : un rectangle w × h tient
# dans un losange de diagonale S si w + h <= S. Côté du carré découpé autour du
# visage = (w + h) × FACE_MARGIN, pour garder front, cheveux et menton ; le
# centre est remonté d'une fraction de la hauteur du visage (les cheveux).
FACE_MARGIN = 1.3
FACE_LIFT = 0.1


def face_crop(width: int, height: int, box: Sequence[float]) -> tuple[int, int, int, int]:
    """Carré (gauche, haut, droite, bas) en pixels, centré sur le visage
    ``box`` (relatif, 0 à 1), gardé dans l'image : réduit puis décalé s'il le
    faut (jamais de bande vide). Carnet (``cadre=centre``) envoie déjà un carré
    à ces proportions : il est alors gardé entier."""
    x1, y1, x2, y2 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
    fw, fh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    side = int(min(round((fw + fh) * FACE_MARGIN), width, height))
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2 - FACE_LIFT * fh
    left = int(min(max(round(cx - side / 2), 0), width - side))
    top = int(min(max(round(cy - side / 2), 0), height - side))
    return left, top, left + side, top + side


def _face_disc(
    face: bytes | None, name: str, size: int, box: Sequence[float] | None = None
) -> Image.Image:
    """Visage carré ou, à défaut, initiales sur fond noir.

    ``box`` (portrait Carnet) : visage entier centré, voir :func:`face_crop` ;
    sans ``box`` (vignette Immich, déjà serrée sur le visage) : image entière.
    """
    if face:
        try:
            with Image.open(io.BytesIO(face)) as src:
                img = ImageOps.exif_transpose(src).convert("RGB")
            if box is not None:
                img = img.crop(face_crop(img.width, img.height, box))
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            else:
                img = ImageOps.fit(
                    img, (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.4)
                )
            img = ImageOps.autocontrast(img, cutoff=1)
            return ImageEnhance.Contrast(img).enhance(1.15)
        except OSError:
            pass
    img = Image.new("RGB", (size, size), PALETTE[BLACK])
    draw = ImageDraw.Draw(img)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    font = _font(size // 3, bold=True)
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text(
        ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
        initials,
        font=font,
        fill=PALETTE[YELLOW],
    )
    return img


def _centered(draw: ImageDraw.ImageDraw, cx: float, top: float, text: str, font, fill) -> float:
    """Écrit ``text`` centré sur ``cx`` ; renvoie le bas du texte."""
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2 - box[0], top - box[1]), text, font=font, fill=fill)
    return top + (box[3] - box[1])


def _fit(draw: ImageDraw.ImageDraw, text: str, sizes: tuple[int, ...], bold: bool, max_w: int):
    """Plus grande police de ``sizes`` dans laquelle ``text`` tient en ``max_w``."""
    font = _font(sizes[-1], bold=bold)
    for size in sizes:
        font = _font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_w:
            break
    return font


def room_line(room: str, prefix: str = "Bienvenue") -> str:
    """« Bienvenue dans la chambre d'ami », « Bientôt dans la chambre Stitch »."""
    room = room.strip()
    if room.lower().startswith("chambre"):
        return f"{prefix} dans la {room[0].lower()}{room[1:]}"
    return f"{prefix} dans la chambre {room}"


def welcome_line(room: str) -> str:
    """« bienvenue dans la chambre d'ami » (forme en minuscule, v2.0)."""
    line = room_line(room)
    return line[0].lower() + line[1:]


# Géométrie du losange du logo Petit Lecussan (SVG 512×512, centre 256) :
# losange extérieur demi-diagonale 150, trait 9, ouvert sur le côté haut-droit
# où passe la Garonne (courbe bleue, trait 11, doublée d'un filet 3,8) ;
# losange intérieur demi-diagonale 121,5, trait 2,9 à 50 %.
_OUTER = 150.0
_INNER = 121.5
_RIVER = ((0.0, -150.0), (44.0, -126.0), (75.0, -75.0), (106.0, -24.0), (150.0, 0.0))
_RIVER_2 = ((8.4, -158.4), (56.0, -138.0), (83.4, -83.4), (118.0, -36.0), (158.4, -8.4))


def _quad(p0, p1, p2, steps: int = 24) -> list[tuple[float, float]]:
    """Points d'une courbe de Bézier quadratique."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append(
            (
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            )
        )
    return pts


def _thick_line(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: float, fill) -> None:
    """Polyligne épaisse aux jonctions arrondies."""
    w = max(1, round(width))
    draw.line(pts, fill=fill, width=w)
    if w > 2:
        r = w / 2
        for x, y in pts:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def _diamond_face(
    img: Image.Image,
    cx: float,
    cy: float,
    half: float,
    face: bytes | None,
    name: str,
    box: Sequence[float] | None = None,
) -> None:
    """Visage dans le losange du logo, à la place du « PL », bordures du logo."""
    draw = ImageDraw.Draw(img)
    k = half / _OUTER

    def at(x: float, y: float) -> tuple[float, float]:
        return (cx + x * k, cy + y * k)

    inner = _INNER * k
    # Visage découpé dans le losange intérieur.
    size = int(2 * inner)
    portrait = _face_disc(face, name, size, box)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(
        [(size / 2, 0), (size - 1, size / 2), (size / 2, size - 1), (0, size / 2)], fill=255
    )
    img.paste(portrait, (int(cx - size / 2), int(cy - size / 2)), mask)
    # Losange intérieur : filet fin.
    _thick_line(
        draw,
        [at(0, -_INNER), at(_INNER, 0), at(0, _INNER), at(-_INNER, 0), at(0, -_INNER)],
        max(1.0, 2.9 * k),
        PALETTE[YELLOW],
    )
    # Losange extérieur, ouvert en haut à droite.
    _thick_line(
        draw,
        [at(_OUTER, 0), at(0, _OUTER), at(-_OUTER, 0), at(0, -_OUTER)],
        9 * k,
        PALETTE[YELLOW],
    )
    # La Garonne (bleue sur le logo : blanche sur la dalle, qui n'a pas de bleu).
    for curve, width in ((_RIVER, 11 * k), (_RIVER_2, max(1.0, 3.8 * k))):
        pts = _quad(curve[0], curve[1], curve[2]) + _quad(curve[2], curve[3], curve[4])[1:]
        _thick_line(draw, [at(x, y) for x, y in pts], width, PALETTE[WHITE])


def render_door(
    room: str,
    guests: Sequence[tuple],
    dates: str = "",
    upcoming: bool = False,
) -> bytes:
    """Cadre de la porte quand un séjour est programmé ou en cours.

    Fond noir ; en haut « Bientôt dans la chambre … » (séjour programmé) ou
    « Bienvenue dans la chambre … » (séjour en cours), en or ; au centre le
    visage de chaque invité dans le losange du logo Petit Lecussan, à la place
    du « PL » (un invité : un losange ; deux : deux losanges côte à côte),
    son prénom dessous ; en bas la date prévue.
    ``guests`` : (prénom, image ou ``None`` -> initiales[, cadre du visage]).
    Avec le cadre du visage (portrait Carnet), le visage entier est centré
    dans le losange ; sans (vignette Immich), l'image remplit le losange.
    """
    img = Image.new("RGB", (FRAME_W, FRAME_H), PALETTE[BLACK])
    draw = ImageDraw.Draw(img)
    guests = list(guests)[:2]
    two = len(guests) == 2
    max_w = FRAME_W - 20

    title = room_line(room, "Bientôt" if upcoming else "Bienvenue")
    _centered(draw, FRAME_W / 2, 10, title, _fit(draw, title, (22, 20, 18, 16, 14), True, max_w), PALETTE[YELLOW])

    half = 74 if two else 92
    cy = 40 + half
    centers = [FRAME_W / 2 - 96, FRAME_W / 2 + 96] if two else [FRAME_W / 2]
    name_top = cy + half + 8
    for guest, cx in zip(guests, centers, strict=True):
        name, face = guest[0], guest[1]
        box = guest[2] if len(guest) > 2 else None
        _diamond_face(img, cx, cy, half, face, name, box)
        if name:
            _centered(draw, cx, name_top, name, _fit(draw, name, (20, 18, 16, 14), True, 180), PALETTE[WHITE])
    if dates:
        _centered(draw, FRAME_W / 2, FRAME_H - 30, dates, _fit(draw, dates, (18, 16, 14, 12), False, max_w), PALETTE[YELLOW])
    return pack(quantize(img, dither=True))

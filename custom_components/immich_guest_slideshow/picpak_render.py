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
Box = tuple[float, float, float, float]

# Visée sans visage connu : un peu au-dessus du centre, là où se trouvent les
# têtes sur la plupart des photos.
DEFAULT_FOCUS = (0.5, 0.42)


def face_box(faces: Sequence[dict], person_id: str | None) -> Box | None:
    """Boîte englobant les visages à garder dans le cadre.

    Priorité au(x) visage(s) de l'invité ``person_id`` ; à défaut (invité non
    reconnu sur cette photo), tous les visages détectés. ``None`` si Immich
    n'a détecté aucun visage exploitable.

    Les boîtes d'Immich sont exprimées dans les dimensions de l'image
    analysée (``imageWidth`` × ``imageHeight``, orientation EXIF appliquée,
    comme l'aperçu téléchargé) : on les normalise donc par ces dimensions.
    """
    def _box(face: dict) -> Box | None:
        try:
            w = float(face["imageWidth"])
            h = float(face["imageHeight"])
            if w <= 0 or h <= 0:
                return None
            x1 = float(face["boundingBoxX1"]) / w
            y1 = float(face["boundingBoxY1"]) / h
            x2 = float(face["boundingBoxX2"]) / w
            y2 = float(face["boundingBoxY2"]) / h
        except (KeyError, TypeError, ValueError):
            return None
        x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
        y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None
        return (x1, y1, x2, y2)

    def _person(face: dict) -> str | None:
        person = face.get("person")
        return person.get("id") if isinstance(person, dict) else None

    guest = [f for f in faces if person_id and _person(f) == person_id]
    for group in (guest, list(faces)):
        boxes = [b for b in (_box(f) for f in group) if b]
        if boxes:
            return (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
    return None


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

    Titre en noir, filet jaune, sous-titre en noir, sur fond blanc : les
    seules couleurs qui restent nettes sans tramage sur la dalle.
    """
    img = Image.new("RGB", (FRAME_W, FRAME_H), PALETTE[WHITE])
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

    def centered(text: str, fnt, top: float) -> float:
        box = draw.textbbox((0, 0), text, font=fnt)
        tw = box[2] - box[0]
        draw.text(((FRAME_W - tw) / 2 - box[0], top - box[1]), text, font=fnt, fill=PALETTE[BLACK])
        return top + (box[3] - box[1])

    for i, ln in enumerate(lines):
        y = centered(ln, font, y) + (gap if i < len(lines) - 1 else 0)
    draw.rectangle((110, y + 16, 290, y + 21), fill=PALETTE[YELLOW])
    y += 40
    for ln in sub_lines:
        y = centered(ln, sub_font, y) + 6
    return img


CAPTION_BAND = 30  # hauteur de la légende en bas du logo


def _add_caption(img: Image.Image, caption: str) -> Image.Image:
    """Réduit le logo pour libérer une bande en bas et y écrit ``caption``.

    Une ou deux lignes (séparées par « \n ») : la 1re en gras, la 2e plus
    petite. Le logo garde ses proportions et la couleur de son fond (prise
    dans le coin) ; la légende est en jaune sur fond sombre, en noir sur fond
    clair.
    """
    lines = [ln.strip() for ln in caption.split("\n") if ln.strip()][:2]
    if not lines:
        return img
    bg = img.getpixel((0, 0))
    luma = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    ink = PALETTE[YELLOW] if luma < 128 else PALETTE[BLACK]
    band = CAPTION_BAND if len(lines) == 1 else CAPTION_BAND + 20
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
    if len(lines) > 1:
        fonts.append(fit(lines[1], (16, 14, 12), False))
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
    centrée sur fond blanc sans être déformée ni rognée. Sans fichier lisible,
    un logo typographique est dessiné avec ``title`` et ``subtitle``.

    ``caption`` : légende écrite tout en bas (ex. « Chambre d'ami invité
    N°1 ») ; le logo est alors réduit pour lui laisser la place.
    """
    img: Image.Image | None = None
    if path and os.path.isfile(path):
        try:
            with Image.open(path) as src:
                logo = ImageOps.exif_transpose(src).convert("RGBA")
            img = Image.new("RGB", (FRAME_W, FRAME_H), PALETTE[WHITE])
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

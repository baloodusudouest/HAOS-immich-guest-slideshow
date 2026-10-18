"""Rendu des images pour les cadres PicPak (e-ink 4,2″, 400×300, 4 couleurs).

Module pur (sans Home Assistant) : il transforme une photo JPEG en tampon
d'affichage de 30 000 octets, au format exact attendu par la dalle.

Pipeline (référence : documentation/image-pipeline.md du projet open-PicPak) :

1. photo intégrale réduite dans 400×300 (bandes blanches si le format diffère) ;
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
# Mise à l'échelle
# --------------------------------------------------------------------------- #
def contain(
    img: Image.Image,
    size: tuple[int, int] = (FRAME_W, FRAME_H),
    background: tuple[int, int, int] = PALETTE[WHITE],
) -> Image.Image:
    """Photo INTÉGRALE : réduite pour tenir dans ``size``, sans rognage ni
    déformation, centrée sur un fond uni (bandes blanches, comme le papier
    de la dalle, en haut/bas ou à gauche/droite selon le format)."""
    tw, th = size
    sw, sh = img.size
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background)
    canvas.paste(img, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


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


def render_photo(jpeg: bytes) -> bytes:
    """Photo (JPEG/PNG/WebP) -> tampon d'affichage de 30 000 octets.

    La photo est affichée en entier : le renfort de contraste est appliqué
    avant l'ajout des bandes, pour ne pas être faussé par leur blanc.
    """
    with Image.open(io.BytesIO(jpeg)) as src:
        img = ImageOps.exif_transpose(src).convert("RGB")
    img = contain(_prepare_photo(img))
    return pack(quantize(img, dither=True))


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Police vectorielle si disponible, sinon la police bitmap de Pillow."""
    candidates = (
        ["DejaVuSerif-Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["DejaVuSerif.ttf", "DejaVuSans.ttf"]
    )
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


def render_logo(
    path: str | None = None, title: str = "Bienvenue", subtitle: str = ""
) -> bytes:
    """Écran « chambre libre » : le logo, sans tramage (aplats nets).

    ``path`` : image du logo (PNG ; le SVG n'est pas lisible). Une image de
    exactement 400×300 est affichée pleine page, telle quelle ; sinon elle est
    centrée sur fond blanc sans être déformée ni rognée. Sans fichier lisible,
    un logo typographique est dessiné avec ``title`` et ``subtitle``.
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
    return pack(quantize(img, dither=False))

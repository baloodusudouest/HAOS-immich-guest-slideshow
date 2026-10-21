"""Cadrage des photos sur les visages des invités.

Module pur (sans Home Assistant), partagé par le diaporama des chambres et
les cadres PicPak.

Le diaporama est affiché par le navigateur, qui recadre lui-même la photo
(``object-fit: cover``) à une taille qu'on ne connaît pas côté serveur : elle
dépend de la largeur de l'écran. On ne peut donc pas viser « le visage au
centre » ; on calcule à la place une ``object-position`` CSS qui garantit que
toute la zone à garder (visages + marge pour cheveux et menton) reste visible
**quelle que soit la taille d'affichage**, dès lors que la zone visible est au
moins aussi grande qu'elle.

Principe, sur un axe : la zone à garder occupe ``[a, b]`` (fractions 0..1 de
la photo). Avec ``object-position: p%``, la fenêtre visible de taille ``v``
commence à ``p·(1 − v)``. Choisir ``p = a / (a + 1 − b)`` — la part de marge
libre située avant la zone — donne :

* début de fenêtre ``≤ a``  ⇔  ``v ≥ b − a`` ;
* fin de fenêtre   ``≥ b``  ⇔  ``v ≥ b − a``.

La zone est donc entièrement visible pour toute fenêtre assez grande pour la
contenir, sans zoom : on voit toujours la photo, pas seulement le visage.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

# Boîte normalisée (x1, y1, x2, y2), coordonnées 0..1 dans l'image source.
Box = tuple[float, float, float, float]

# Marges ajoutées autour des boîtes d'Immich, en proportion de la taille du
# visage. La boîte de détection va des sourcils au menton : on ajoute le
# front et les cheveux au-dessus, le menton en dessous, les oreilles sur les
# côtés.
MARGIN_TOP = 0.6
MARGIN_BOTTOM = 0.25
MARGIN_SIDE = 0.3

# Position par défaut sans visage connu : un peu au-dessus du centre, là où
# se trouvent les têtes sur la plupart des photos.
DEFAULT_OBJECT_POSITION = "50% 30%"


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _box(face: dict) -> Box | None:
    """Boîte normalisée d'un visage Immich, ``None`` si inexploitable.

    Les boîtes d'Immich sont exprimées dans les dimensions de l'image
    analysée (``imageWidth`` × ``imageHeight``, orientation EXIF appliquée,
    comme l'aperçu téléchargé) : on les normalise donc par ces dimensions.
    """
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
    x1, x2 = sorted((_clamp(x1), _clamp(x2)))
    y1, y2 = sorted((_clamp(y1), _clamp(y2)))
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    return (x1, y1, x2, y2)


def _person(face: dict) -> str | None:
    person = face.get("person")
    return person.get("id") if isinstance(person, dict) else None


def _union(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def faces_box(faces: Sequence[dict], person_ids: Iterable[str | None]) -> Box | None:
    """Boîte englobant les visages à garder.

    Priorité aux visages des personnes ``person_ids`` (les invités) ; à
    défaut (aucun invité reconnu sur cette photo), tous les visages détectés.
    ``None`` si Immich n'a détecté aucun visage exploitable.
    """
    wanted = {pid for pid in person_ids if pid}
    guests = [f for f in faces if wanted and _person(f) in wanted]
    for group in (guests, list(faces)):
        boxes = [b for b in (_box(f) for f in group) if b]
        if boxes:
            return _union(boxes)
    return None


def expand_box(box: Box) -> Box:
    """Ajoute à chaque visage la marge cheveux / menton / oreilles.

    La marge est calculée sur la taille de la boîte d'origine : sur une
    boîte englobant deux visages, elle est un peu plus généreuse, ce qui
    reste du bon côté (on ne coupe jamais un visage).
    """
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return (
        _clamp(x1 - MARGIN_SIDE * w),
        _clamp(y1 - MARGIN_TOP * h),
        _clamp(x2 + MARGIN_SIDE * w),
        _clamp(y2 + MARGIN_BOTTOM * h),
    )


def _axis_position(lo: float, hi: float) -> float:
    """``object-position`` (0..1) gardant ``[lo, hi]`` visible sur un axe."""
    spare = lo + (1.0 - hi)
    if spare <= 1e-9:
        return 0.5
    return _clamp(lo / spare)


def object_position(box: Box | None) -> str:
    """Valeur CSS ``object-position`` (ex. ``"47.5% 12.0%"``) pour ``box``.

    ``box`` est la zone à garder, marges comprises (voir :func:`expand_box`).
    Sans boîte : :data:`DEFAULT_OBJECT_POSITION`.
    """
    if box is None:
        return DEFAULT_OBJECT_POSITION
    x = _axis_position(box[0], box[2]) * 100
    y = _axis_position(box[1], box[3]) * 100
    return f"{x:.1f}% {y:.1f}%"

"""Constantes pour l'intégration Immich Guest Slideshow."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "immich_guest_slideshow"

# Configuration (Config Flow)
CONF_URL: Final = "url"
CONF_API_KEY: Final = "api_key"

# Options
CONF_INTERVAL: Final = "interval"                    # secondes entre deux images
CONF_CACHE_SIZE: Final = "cache_size"                # nb max de photos en cache / chambre
CONF_PERMANENT_PERSONS: Final = "permanent_persons"  # liste de noms complets
CONF_ROOMS: Final = "rooms"
CONF_REBUILD_HOURS: Final = "rebuild_hours"          # rafraîchissement du cache

# Clés internes d'une chambre
KEY_NAME: Final = "name"
KEY_HELPERS: Final = "helpers"

DEFAULT_INTERVAL: Final = 30
DEFAULT_CACHE_SIZE: Final = 50
DEFAULT_PERMANENT_PERSONS: Final = ["Propriétaire 1", "Propriétaire 2"]
DEFAULT_REBUILD_HOURS: Final = 6

# Nombre maximum de photos récupérées par recherche (pagination)
MAX_ASSETS_PER_SEARCH: Final = 400

# Chambres par défaut : id -> (nom affiché, helpers input_text)
DEFAULT_ROOMS: Final[dict[str, dict]] = {
    "chambre_d_ami": {
        "name": "Chambre d'ami",
        "helpers": [
            "input_text.guest_chambre_d_ami",
            "input_text.guest_chambre_d_ami2",
        ],
    },
    "chambre_stitch": {
        "name": "Chambre Stitch",
        "helpers": [
            "input_text.guest_chambre_stitch",
            "input_text.guest_chambre_stitch2",
        ],
    },
    "chambre_ado": {
        "name": "Chambre Ado",
        "helpers": [
            "input_text.guest_chambre_ado",
            "input_text.guest_chambre_ado2",
        ],
    },
}

# Services
SERVICE_REFRESH: Final = "refresh"
SERVICE_NEXT: Final = "next"
SERVICE_SYNC_ALBUMS: Final = "sync_albums"
ATTR_ROOM: Final = "room"
ATTR_SIZE: Final = "size"

# --- Albums Immich par lit ------------------------------------------------ #
# Un album par helper input_text, donc par lit — et par cadre photo. C'est la
# seule granularité qui permette au cadre du lit 1 de montrer l'invité 1 et
# celui du lit 2 l'invité 2.
#
# Le nom est « {prefixe} {chambre} {n} », suffixé du nom de l'invité. Le
# préfixe commun regroupe les albums dans la liste d'Immich, triée
# alphabétiquement.
ALBUM_PREFIX: Final = "PicPak"
DEFAULT_ALBUM_SIZE: Final = 50
MAX_ALBUM_SIZE: Final = 500

MANUFACTURER: Final = "Immich"

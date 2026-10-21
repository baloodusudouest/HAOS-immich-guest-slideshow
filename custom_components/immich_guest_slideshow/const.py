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
CONF_MIN_PHOTOS: Final = "min_photos"                # minimum de photos par invité

# Clés internes d'une chambre
KEY_NAME: Final = "name"
KEY_HELPERS: Final = "helpers"

DEFAULT_INTERVAL: Final = 30
DEFAULT_CACHE_SIZE: Final = 50
DEFAULT_PERMANENT_PERSONS: Final = ["Propriétaire 1", "Propriétaire 2"]
DEFAULT_REBUILD_HOURS: Final = 6
# En dessous de ce nombre de photos (avec les propriétaires), on affiche aussi
# les photos individuelles de l'invité ; sous ce nombre au total, on prévient.
DEFAULT_MIN_PHOTOS: Final = 2

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
SERVICE_LINK_PERSON: Final = "link_person"
SERVICE_UNLINK_PERSON: Final = "unlink_person"
ATTR_ROOM: Final = "room"
ATTR_NAME: Final = "name"
ATTR_PERSON_ID: Final = "person_id"
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

# --- Cadres e-ink PicPak (firmware open-PicPak, tirage Wi-Fi) ------------- #
# Un cadre par lit : le cadre n° N d'une chambre suit le helper n° N. Chaque
# cadre télécharge à chaque réveil une image déjà rendue (30 000 octets) sur
# l'URL de la vue HTTP ci-dessous ; la réponse fixe aussi son prochain réveil.
PICPAK_URL_PATH: Final = "/api/immich_guest_slideshow/picpak/{room_id}/{bed}"
PICPAK_URL_MAX: Final = 128  # taille max d'une URL dans la NVS du cadre

CONF_PICPAK_INTERVAL: Final = "picpak_interval"   # minutes entre deux photos
CONF_PICPAK_PHOTOS: Final = "picpak_photos"       # photos par cadre et par séjour
CONF_PICPAK_QUIET: Final = "picpak_quiet"         # pause nocturne « 23:00-07:00 »
CONF_PICPAK_LOGO: Final = "picpak_logo"           # chemin du logo (chambre libre)
CONF_PICPAK_LOGO_2: Final = "picpak_logo_2"       # logo des cadres n° 2, 4… (facultatif)
CONF_PICPAK_TITLE: Final = "picpak_title"         # logo texte de secours
CONF_PICPAK_SUBTITLE: Final = "picpak_subtitle"
CONF_PICPAK_BASE_URL: Final = "picpak_base_url"   # URL locale de HA vue du cadre
CONF_PICPAK_ARRIVAL_HOUR: Final = "picpak_arrival_hour"  # début des photos le jour J
CONF_PICPAK_RESET_BUTTON: Final = "picpak_reset_button"  # fin de séjour manuelle
CONF_PICPAK_MODE: Final = "picpak_mode"  # pendant le séjour : photos ou visages
PICPAK_MODE_PHOTOS: Final = "photos"
PICPAK_MODE_FACES: Final = "visages"

DEFAULT_PICPAK_INTERVAL: Final = 60
DEFAULT_PICPAK_PHOTOS: Final = 50
DEFAULT_PICPAK_QUIET: Final = ""
DEFAULT_PICPAK_LOGO: Final = "/config/www/picpak_logo.png"
DEFAULT_PICPAK_LOGO_2: Final = ""
DEFAULT_PICPAK_TITLE: Final = "Bienvenue"
DEFAULT_PICPAK_SUBTITLE: Final = ""
DEFAULT_PICPAK_BASE_URL: Final = ""
DEFAULT_PICPAK_ARRIVAL_HOUR: Final = 9
DEFAULT_PICPAK_RESET_BUTTON: Final = "input_button.reinitialiser_chambre_hote"
DEFAULT_PICPAK_MODE: Final = "photos"
# Calendrier des séjours d'une chambre : calendar.<identifiant de la chambre>.
# Sans ce calendrier, les cadres montrent l'invité dès qu'il est renseigné.
PICPAK_CALENDAR: Final = "calendar.{room_id}"

SERVICE_PICPAK_URLS: Final = "picpak_urls"
SERVICE_PICPAK_NEXT: Final = "picpak_next"
ATTR_BED: Final = "bed"
ATTR_BASE_URL: Final = "base_url"

# Signal de mise à jour des entités d'un cadre : format(entry_id, frame_key)
SIGNAL_PICPAK_UPDATE: Final = "immich_guest_slideshow_picpak_{}_{}"
PICPAK_MANUFACTURER: Final = "PicPak (firmware open-PicPak)"
PICPAK_MODEL: Final = "PicPak 4,2″ BWRY"

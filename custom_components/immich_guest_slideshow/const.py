"""Constantes de l'intégration Immich Guest Slideshow (v2)."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "immich_guest_slideshow"
MANUFACTURER: Final = "Immich"

# --------------------------------------------------------------------------- #
# Entrée principale : serveur Immich
# --------------------------------------------------------------------------- #
CONF_URL: Final = "url"
CONF_API_KEY: Final = "api_key"

# --------------------------------------------------------------------------- #
# Options générales
# --------------------------------------------------------------------------- #
CONF_PERMANENT_PERSONS: Final = "permanent_persons"  # propriétaires (noms ou ID)
CONF_INTERVAL: Final = "interval"            # dashboard : secondes par photo
CONF_CACHE_SIZE: Final = "cache_size"        # dashboard : photos par chambre
CONF_REBUILD_HOURS: Final = "rebuild_hours"  # relecture périodique d'Immich
CONF_MIN_PHOTOS: Final = "min_photos"        # sous ce nombre : signalement
CONF_ARRIVAL_HOUR: Final = "arrival_hour"    # début du séjour le jour J

DEFAULT_PERMANENT_PERSONS: Final = ["Propriétaire 1", "Propriétaire 2"]
DEFAULT_INTERVAL: Final = 15
DEFAULT_CACHE_SIZE: Final = 100
DEFAULT_REBUILD_HOURS: Final = 6
DEFAULT_MIN_PHOTOS: Final = 2
DEFAULT_ARRIVAL_HOUR: Final = 9

# Nombre maximum de photos récupérées par recherche Immich (pagination).
MAX_ASSETS_PER_SEARCH: Final = 400

# --------------------------------------------------------------------------- #
# Carnet (contacts, NAS) : portraits HD des invités (2.4.0, facultatif)
# --------------------------------------------------------------------------- #
CONF_CARNET_URL: Final = "carnet_url"      # ex. http://192.168.1.90:8096 (vide = sans Carnet)
CONF_CARNET_TOKEN: Final = "carnet_token"  # CARNET_HA_TOKEN de la stack Carnet

DEFAULT_CARNET_URL: Final = ""

# --------------------------------------------------------------------------- #
# Options des cadres PicPak
# --------------------------------------------------------------------------- #
CONF_PICPAK_INTERVAL: Final = "picpak_interval"    # minutes entre deux photos
CONF_PICPAK_PHOTOS: Final = "picpak_photos"        # repli sans calendrier
CONF_PICPAK_QUIET: Final = "picpak_quiet"          # pause nocturne « 01:00-07:00 »
CONF_PICPAK_LOGO: Final = "picpak_logo"            # logo (chemin d'un PNG)
CONF_PICPAK_LOGO_2: Final = "picpak_logo_2"        # logo des cadres n° 2 (facultatif)
CONF_PICPAK_TITLE: Final = "picpak_title"          # logo texte de secours
CONF_PICPAK_SUBTITLE: Final = "picpak_subtitle"
CONF_PICPAK_BASE_URL: Final = "picpak_base_url"    # URL locale de HA vue du cadre
CONF_PICPAK_RESET_BUTTON: Final = "picpak_reset_button"  # fin de séjour manuelle

DEFAULT_PICPAK_INTERVAL: Final = 60
DEFAULT_PICPAK_PHOTOS: Final = 50
DEFAULT_PICPAK_QUIET: Final = ""
DEFAULT_PICPAK_LOGO: Final = "/config/www/picpak_logo.png"
DEFAULT_PICPAK_LOGO_2: Final = ""
DEFAULT_PICPAK_TITLE: Final = "Bienvenue"
DEFAULT_PICPAK_SUBTITLE: Final = ""
DEFAULT_PICPAK_BASE_URL: Final = ""
DEFAULT_PICPAK_RESET_BUTTON: Final = "input_button.reinitialiser_chambre_hote"

# Plafond du nombre de photos par cadre et par séjour (une par heure).
MAX_FRAME_PHOTOS: Final = 500

# --------------------------------------------------------------------------- #
# Chambres : une sous-entrée (« une ligne ») par chambre
# --------------------------------------------------------------------------- #
SUBENTRY_ROOM: Final = "room"
ROOM_ID: Final = "room_id"            # identifiant stable (entités, URL des cadres)
ROOM_NAME: Final = "name"
ROOM_CALENDAR: Final = "calendar"     # calendar.xxx (séjours)
ROOM_HELPERS: Final = "helpers"       # input_text des invités (1 ou 2)
ROOM_DOOR_FRAME: Final = "door_frame"  # cadre PicPak sur la porte
ROOM_FRAMES: Final = "room_frames"    # cadres PicPak dans la chambre (0 à 2)

MAX_GUESTS: Final = 2
MAX_ROOM_FRAMES: Final = 2

# Options v1 (avant les sous-entrées), lues seulement par la migration.
LEGACY_CONF_ROOMS: Final = "rooms"
LEGACY_KEY_NAME: Final = "name"
LEGACY_KEY_HELPERS: Final = "helpers"
LEGACY_PICPAK_MODE: Final = "picpak_mode"
LEGACY_PICPAK_ARRIVAL_HOUR: Final = "picpak_arrival_hour"

# --------------------------------------------------------------------------- #
# Cadres PicPak (firmware open-PicPak, tirage Wi-Fi)
# --------------------------------------------------------------------------- #
# Chaque cadre télécharge à chaque réveil une image déjà rendue (30 000 octets)
# sur cette URL ; la réponse fixe aussi son prochain réveil. ``{slot}`` vaut
# « 1 », « 2 » (cadres de la chambre, URL inchangées depuis la v1) ou
# « porte ».
PICPAK_URL_PATH: Final = "/api/immich_guest_slideshow/picpak/{room_id}/{slot}"
PICPAK_URL_MAX: Final = 128  # taille max d'une URL dans la NVS du cadre
DOOR_SLOT: Final = "porte"

# Portraits des invités pour les dashboards (même jeton que les cadres) ;
# ``{index}`` = 1 ou 2, rang de l'invité renseigné.
PORTRAIT_URL_PATH: Final = "/api/immich_guest_slideshow/portrait/{room_id}/{index}"
# Simulation du cadre de porte avec une photo candidate (carte de réservation).
SIMULATION_URL_PATH: Final = "/api/immich_guest_slideshow/simulation/{room_id}"

PICPAK_MANUFACTURER: Final = "PicPak (firmware open-PicPak)"
PICPAK_MODEL: Final = "PicPak 4,2″ BWRY"

# Signal de mise à jour des entités d'un cadre : format(entry_id, frame_key)
SIGNAL_PICPAK_UPDATE: Final = "immich_guest_slideshow_picpak_{}_{}"

# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #
SERVICE_REFRESH: Final = "refresh"
SERVICE_NEXT: Final = "next"
SERVICE_PREVIOUS: Final = "previous"
SERVICE_LINK_PERSON: Final = "link_person"
SERVICE_UNLINK_PERSON: Final = "unlink_person"
SERVICE_PICPAK_URLS: Final = "picpak_urls"
SERVICE_PICPAK_NEXT: Final = "picpak_next"
SERVICE_PORTRAIT_CHOICES: Final = "portrait_choices"
SERVICE_PORTRAIT_CHOOSE: Final = "portrait_choose"

ATTR_ROOM: Final = "room"
ATTR_NAME: Final = "name"
ATTR_PERSON_ID: Final = "person_id"
ATTR_BED: Final = "bed"
ATTR_BASE_URL: Final = "base_url"
ATTR_ASSET: Final = "asset"
ATTR_ARRIVAL: Final = "arrivee"
ATTR_DEPARTURE: Final = "depart"

# Changelog
## 1.3.0 — 2026-08-02

### Synchronisation d'albums Immich

- Nouveau service `immich_guest_slideshow.sync_albums` : crée ou met à jour un
  album Immich **par lit occupé**, contenant un échantillon aléatoire des
  photos de son occupant. Paramètres `room` (optionnel) et `size` (50 par
  défaut). Le service renvoie une réponse détaillant les albums touchés, pour
  qu'une automatisation puisse la notifier.
- Destiné aux cadres photo qui ne savent pas recevoir d'image
  automatiquement : il suffit de télécharger l'album depuis l'app Immich puis
  de l'envoyer au cadre.
- La granularité est le **lit**, pas la chambre : le cadre du lit 1 reçoit les
  photos de l'invité 1, celui du lit 2 celles de l'invité 2. Les albums sont
  nommés `PicPak <chambre> <n> — <invité>`.
- Chambre occupée par une seule personne : les deux albums puisent dans le
  même ensemble, mais sur des tranches **disjointes** après mélange, pour que
  deux cadres n'affichent jamais la même photo au même moment.
- Lit vide : l'album correspondant est laissé intact.
- Les albums sont retrouvés par leur préfixe et non par leur nom complet,
  qui porte le nom de l'invité et change à chaque réservation — sans quoi un
  album serait créé à chaque séjour.
- Le contenu obsolète est retiré **avant** l'ajout du nouveau : l'ordre
  inverse ferait transiter l'album par un état contenant les deux, et un
  téléchargement lancé à cet instant récupérerait le double.

### API

- Client Immich étendu : liste des albums, lecture, création, ajout et retrait
  d'assets, renommage.
- Lecture des assets d'un album en deux stratégies : `GET /api/albums/{id}`
  puis repli sur `search/metadata` filtré par `albumIds`. Sur certaines
  instances le premier renvoie un tableau `assets` vide alors que
  `assetCount` est non nul.

### Interne

- `SlideshowEngine.get_guests_by_bed()` : liste des invités par lit, avec
  `None` pour un lit vide. `get_guests()` compacte la liste et perd l'indice,
  ce dont les albums ont besoin.
- La collecte d'assets par invité est indépendante du cache du diaporama, qui
  fusionne volontairement tous les invités d'une chambre.

Aucun changement de configuration : `cache.py`, `coordinator.py`, `image.py`,
`sensor.py`, `config_flow.py` et `diagnostics.py` sont inchangés.

## 1.2.1 — 2026-07-17

- Correction : extraction du nom d'invité depuis le helper (suffixe de dates ignoré) et normalisation Unicode NFC/NFD des noms accentués

## 1.2.0 — 2026-07-17

-Changement pour pouvoir avoir utiliser des prénoms et noms avec des accents

## 1.1.0 — 2026-07-08

- Repli automatique par invité : si aucune photo ne combine un invité avec
  les propriétaires, ses photos individuelles sont utilisées à la place.
- Tests dédiés au mécanisme de repli.

## 1.0.0 — 2026-07-07

### V0.3 → V1.0
- Chambres configurables via l'interface (options) : ajout/suppression avec
  sélecteur d'entités `input_text` ; les trois chambres historiques restent
  les valeurs par défaut.
- Nouveau service `immich_guest_slideshow.next` (image suivante immédiate).
- Nettoyage automatique des appareils des chambres supprimées.
- CI GitHub Actions : hassfest, validation HACS, pytest + couverture.
- Tests supplémentaires : pagination API, flux d'options.

### V0.2
- Pagination des recherches Immich (`assets.nextPage`), limite de 400 photos
  par recherche.
- Rafraîchissement périodique du cache (option `rebuild_hours`, défaut 6 h)
  pour intégrer les nouvelles photos sans redémarrage.

### V0.1
- Config Flow (URL + clé API) avec validation de connexion.
- Client API aiohttp typé (personnes, recherches multi-personnes, thumbnails).
- Moteur de diaporama par chambre : combinaisons invités/permanents
  (3 ou 9 recherches), cache local, rotation aléatoire sans doublon immédiat.
- Entités `image.immich_<chambre>` et `sensor.immich_<chambre>_current_search`.
- Rebuild automatique à chaque changement d'un helper `input_text`.
- Service `refresh`, diagnostics, traductions fr/en, compatibilité HACS.

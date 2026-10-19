# Changelog

## 1.7.0 — 2026-10-19

### Sélection des photos par niveaux de priorité

Pour chaque invité, les photos sont choisies dans cet ordre, jusqu'au nombre
voulu (50 par cadre ou par album, taille du cache pour le diaporama) :

1. l'invité avec l'un des propriétaires, l'autre, ou les deux ;
2. s'il manque des photos : les invités de la chambre **ensemble** ;
3. s'il en manque encore : toutes les autres photos de l'invité.

- S'applique partout : diaporama des chambres, cadres PicPak et albums
  Immich (`sync_albums`).
- Deux invités : chaque cadre montre son occupant ; les photos où ils sont
  ensemble sont **réparties** entre les deux cadres, sans doublon.
- Un invité seul dans une chambre à deux cadres : les deux le montrent, avec
  des photos différentes, ses photos avec les propriétaires en premier.
- Les recherches ne sont faites qu'au besoin : si le niveau 1 suffit, Immich
  n'est pas interrogé pour les suivants.
- « Photos minimum par invité » ne sert plus qu'aux signalements
  (Réparations) : les photos seules complètent désormais toujours la
  sélection quand elle est incomplète.

## 1.6.2 — 2026-10-19

- Cadres PicPak : les photos **remplissent tout l'écran** (plus de bandes
  blanches). Elles sont recadrées au format 4:3 en gardant au centre le
  **visage de l'invité**, d'après les visages détectés par Immich ; si l'invité
  n'est pas reconnu sur la photo, tous les visages ; sans visage, recadrage
  légèrement au-dessus du centre.
- La clé API Immich doit avoir la permission de lecture des visages
  (« face.read ») ; sans elle, le recadrage se fait sans visage.

## 1.6.1 — 2026-10-19

- Correction : l'entité image d'un cadre PicPak restait vide (image cassée) tant que
  le cadre ne s'était pas encore connecté, ou juste après un redémarrage de
  Home Assistant. Elle montre désormais l'image que le cadre affichera à son
  prochain réveil (photo prévue, ou logo si la chambre est libre).
- Les sélections de photos des cadres sont construites dès le démarrage, et
  non plus au premier réveil du cadre.

## 1.6.0 — 2026-10-18

### Cadres e-ink PicPak

- Nouveau : l'intégration alimente des cadres **PicPak** (e-ink 4,2″,
  4 couleurs) flashés avec le firmware open-PicPak « tirage Wi-Fi ». Un cadre
  par lit ; un invité seul dans une chambre à deux cadres est montré sur les
  deux, avec des photos différentes ; chambre libre → logo.
- **50 photos par cadre et par séjour** (réglable), une nouvelle **toutes les
  heures** (réglable), ou immédiatement sur un appui du bouton du cadre.
  **Pause nocturne** facultative.
- Photos affichées **en entier** (jamais rognées, bandes blanches si besoin) ;
  conversion 4 couleurs identique à l'appli d'origine. Un logo PNG de
  400×300 est affiché pleine page.
- Vue HTTP protégée par un jeton : `/api/immich_guest_slideshow/picpak/<chambre>/<lit>`.
  L'image suivante est préparée à l'avance ; un réveil sans changement reçoit
  la même image, que le cadre n'affiche pas (économie de batterie).
- Nouvelles entités par cadre : aperçu de l'écran, batterie, dernière
  connexion.
- Nouveaux services : `picpak_urls` (URL de chaque cadre) et `picpak_next`.
- Nouvelle page d'options **Cadres PicPak**.
- Interne : la répartition des photos par lit est mise en commun entre les
  albums Immich et les cadres (`async_bed_playlists`), sans changement pour
  `sync_albums`.

## 1.5.0 — 2026-10-17

### Personnes introuvables ou avec peu de photos : réparation guidée

- Quand un nom saisi (invité ou personne permanente) ne correspond à personne
  dans Immich, ou quand un invité a **moins de 2 photos**, un élément apparaît
  dans **Paramètres → Système → Réparations**, avec une notification
  persistante (cloche) qui y renvoie. Le formulaire demande l'ID de la
  personne Immich (UUID ou URL de sa fiche) et **mémorise la liaison**.
- La liaison nom → ID est **globale** (toutes les chambres, invités et
  propriétaires), conservée après redémarrage, et **prioritaire** sur la
  recherche par nom. Si la personne liée n'existe plus dans Immich, on
  retombe sur la recherche par nom.
- Un même nom présent dans plusieurs chambres ne produit qu'un seul élément ;
  il disparaît dès que le problème est résolu.
- Nouveau réglage **Photos minimum par invité** (2 par défaut). En dessous,
  les photos individuelles de l'invité s'ajoutent à celles avec les
  propriétaires (avant : uniquement s'il n'y en avait aucune). **Toutes les
  photos trouvées sont toujours affichées**, même sous le minimum : il sert à
  compléter et à prévenir, jamais à masquer.
- Nouveaux services : `immich_guest_slideshow.link_person` (lier un nom à un
  ID sans passer par les Réparations) et `immich_guest_slideshow.unlink_person`
  (effacer la liaison d'un nom, ou toutes si aucun nom n'est donné).

## 1.4.0 — 2026-10-17

- Nouveau : une personne Immich peut désormais être désignée par son
  **identifiant (UUID)** au lieu de son nom, partout où un nom est accepté :
  personnes permanentes (options) et helpers `input_text` des invités.
  Il suffit de coller l'UUID, ou directement l'URL de la fiche de la personne
  (`.../people/<uuid>`). Les noms continuent de fonctionner comme avant.
- L'UUID évite les ambiguïtés d'homonymes, les personnes sans nom et les
  personnes renommées dans Immich. Les sensors et les noms d'albums affichent
  le nom Immich de la personne, pas l'UUID.

## 1.3.1 — 2026-08-02

- Correction : dans une chambre à plusieurs lits occupée par une seule
  personne, un seul album était créé au lieu d'un par lit. Le découpage
  comptait les lits occupés au lieu des lits existants.
  
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

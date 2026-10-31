# Changelog

## 2.4.0 — 2026-10-31

Portraits HD de Carnet — facultatif, à activer dans Configurer → Carnet.

- **Cadre de porte** : le visage de chaque invité vient du **portrait HD de
  Carnet** (meilleure photo du contact : grande, nette, de face), découpé
  dans la photo d'origine avec le **visage entier au milieu du losange**.
  Avant : vignette Immich, petite (~250 px) et serrée sur le visage, coupée au
  front par les angles du losange.
- Invité sans portrait HD, ou Carnet injoignable : vignette Immich, comme
  avant (nouvel essai au réveil suivant). Portraits gardés 6 h.
- **Dashboards** : nouvel attribut `portraits` de `image.immich_<chambre>`
  (`[{nom, url}]`) et adresse
  `/api/immich_guest_slideshow/portrait/<chambre>/<1|2>?k=<jeton>` (jeton des
  cadres), pour afficher le visage des invités dans une carte.
- Nécessite Carnet 1.1.0 (`GET /ha/portrait`). Le jeton Carnet est masqué
  dans les diagnostics.

## 2.3.1 — 2026-10-31

Robustesse — aucun changement de réglage nécessaire.

- **Cadres à jour après une liaison** : lier une personne (Réparations ou
  `link_person`), retirer une liaison ou lancer `refresh` refait aussi la
  sélection des cadres PicPak. Avant, seul le diaporama changeait : les cadres
  gardaient leur sélection tant que les invités ne changeaient pas.
- **Immich injoignable** : une recherche en échec n'est plus mémorisée comme
  « aucune photo ». Les cadres réessaient à leur réveil suivant, le diaporama
  au bout de 5 minutes, et aucun faux signalement « introuvable / peu de
  photos » n'est créé pendant la panne.
- **Calendrier illisible** (au démarrage notamment) : la chambre est
  considérée libre au lieu d'un faux séjour en cours.
- **Homonymes** : plus de repli sur le « premier résultat approchant ».
  Un nom sans correspondance exacte (avec ou sans accents), ou porté par
  plusieurs personnes, est signalé dans **Réparations** pour être lié à la
  bonne fiche (la liaison est mémorisée).
- **URL des cadres** : les réglages PicPak refusent une adresse de base qui
  donnerait une URL de plus de 128 caractères (limite du firmware).

## 2.3.0 — 2026-10-31

- **Suppression de l'action `sync_albums`** et des albums Immich par lit
  (« PicPak <chambre> <lit> — <invité> »). Ils servaient au circuit manuel :
  télécharger l'album depuis l'app Immich puis l'envoyer au cadre avec l'app
  PicPak. Les cadres flashés open-PicPak reçoivent leurs images directement,
  les albums n'avaient plus d'usage.
- La clé API n'a plus besoin des permissions sur les albums.
- Les albums déjà créés ne sont pas touchés : les supprimer à la main dans
  Immich si besoin (les photos restent intactes). Retirer aussi toute
  automatisation ou tout script qui appelait `immich_guest_slideshow.sync_albums`.

## 2.2.0 — 2026-10-21

- Diaporama des dashboards : nouvelle action **`previous`** (photo
  précédente) à côté de `next` ; les deux font changer la photo
  immédiatement et relancent le minuteur de 15 s.
- `next`, `previous` et `refresh` acceptent l'**image du diaporama**
  (`entity_id: image.immich_chambre_ami`) au lieu de l'identifiant de la
  chambre : plus besoin de connaître cet identifiant dans les cartes.
- Rotation du diaporama dans un ordre tiré au hasard à chaque sélection :
  toutes les photos passent avant qu'une revienne, et « précédente » ramène
  exactement la photo d'avant.
- Nouvel attribut `chambre` (identifiant de la chambre) sur l'image ; une
  chambre inconnue dans une action liste les chambres existantes.

## 2.1.0 — 2026-10-21

- Cadre de porte : dès qu'un séjour est **programmé**, la porte affiche
  « **Bientôt dans la chambre …** » ; pendant le séjour, « **Bienvenue dans
  la chambre …** ». Chambre libre : logo et nom de la chambre (inchangé).
- Le visage de chaque invité s'affiche dans le **losange du logo Petit
  Lecussan**, à la place du « PL », avec les mêmes bordures (la Garonne en
  blanc, la dalle n'ayant pas de bleu) ; deux invités : deux losanges côte à
  côte. Prénoms sous les losanges, date prévue en bas.

## 2.0.0 — 2026-10-21

Refonte complète. **Migration automatique** depuis la v1 : identifiants des
chambres, entités et URL des cadres déjà flashés sont conservés.

- **Une ligne par chambre** : chaque chambre est une sous-entrée de
  l'intégration, avec son propre bouton « Configurer » : nom, calendrier des
  séjours, invités (1 ou 2 helpers), cadre PicPak sur la porte (oui/non),
  cadres PicPak dans la chambre (0 à 2). Les options ne gardent que les
  réglages communs (propriétaires, dashboards, cadres).
- **Nouvel ordre des photos** : invité + tous les propriétaires, puis invité +
  chaque propriétaire, puis invités ensemble, puis invité seul.
- **Dashboards** : 100 photos, une toutes les 15 s, dès que l'invité est
  renseigné ; nouveaux attributs `invites`, `sejour` (`en_cours` /
  `programme` / `libre`), `sejour_dates`, `sejour_debut`, `sejour_fin`.
- **Séjour** : du jour d'arrivée à 9 h (réglable) jusqu'au **lendemain du
  dernier jour à 00:01** ; le prochain séjour est lu quelle que soit sa date.
- **Cadres dans la chambre** : logo + nom de la chambre si libre ; logo,
  chambre, noms des invités et dates si un séjour est programmé ; pendant le
  séjour, **une photo par heure, autant que d'heures de séjour** (pause
  nocturne exclue). Un invité par cadre ; un invité seul sur deux cadres sans
  doublon ; un cadre pour deux invités : photos mélangées.
- **Nouveau cadre de porte** (`…/picpak/<chambre>/porte`) : logo et nom de la
  chambre hors séjour ; pendant le séjour, visages des invités,
  « Prénom et Prénom, bienvenue dans la chambre … » et dates.
- Écrans texte en **or sur noir** (logo texte de secours compris).
- Le choix « photos / visages » (réglage global, puis entités
  `select.picpak_…_affichage` de la 1.11) est supprimé : les visages sont sur
  le cadre de porte, les photos dans la chambre. La migration retire ces
  entités.
- Home Assistant 2025.6 minimum (sous-entrées).

## 1.11.0 — 2026-10-21

- Cadres PicPak : le choix **photos / visages** se fait maintenant **cadre
  par cadre**, avec une nouvelle entité
  `select.picpak_<chambre>_<lit>_affichage` (« Affichage pendant le séjour »)
  sur l'appareil de chaque cadre. Le choix est mémorisé, s'applique au
  prochain réveil du cadre, et l'aperçu `image.picpak_…` le montre tout de
  suite. L'option globale « Pendant le séjour, afficher » disparaît des
  options : sa valeur actuelle devient le choix initial de chaque cadre.
- Options → Cadres PicPak : correction de l'erreur d'affichage
  « Translation error: UNCLOSED_TAG » sur l'heure de début des photos
  (libellé contenant `<chambre>`) ; même correction dans la réparation
  « personne introuvable ».

## 1.10.0 — 2026-10-21

- Cadres PicPak : nouveau **mode « visages »** (Options → Cadres PicPak →
  « Pendant le séjour, afficher »). Pendant le séjour, les cadres de la
  chambre affichent une carte « médaillon » au lieu des photos : nom de la
  chambre, visage du ou des invités (vignette Immich de la personne ; centré
  pour un invité seul, côte à côte avec les prénoms pour deux), nom et dates
  du séjour. Même carte sur les deux cadres, redessinée seulement quand les
  invités ou les dates changent (pas de rafraîchissement d'écran inutile).
  Sans vignette, les initiales de l'invité. Hors séjour : le logo, inchangé.
- Attribut `affichage` des entités PicPak : nouvelle valeur `visages`.

## 1.9.0 — 2026-10-21

- Diaporama des chambres : nouvel attribut **`object_position`** sur
  `image.immich_<chambre>`. Utilisé en CSS (`object-fit: cover`), il garde
  **le visage entier des invités** visible quelle que soit la taille de la
  carte : visages des invités de la chambre (ou, à défaut, tous les visages
  détectés), agrandis pour inclure cheveux et menton, sans jamais zoomer
  sur la photo. Sans visage connu : `50% 30%`. Un appel Immich par photo,
  mémorisé pour le séjour ; nécessite la permission « face.read ».
- Le calcul des visages est mis en commun avec les cadres PicPak
  (`framing.py`), sans changement de leur rendu.

## 1.8.3 — 2026-10-19

- Correction : les accents et la flèche des légendes PicPak s'affichaient en
  carré (« invit□ », « 23 □ sam. 24 »). Home Assistant OS n'a pas de police
  système et celle de Pillow ne couvre pas ces caractères : l'intégration
  embarque maintenant DejaVu Serif (réduite aux caractères latins, ~80 Ko,
  licence dans `fonts/LICENSE.txt`), plus proche de la police du logo.

## 1.8.2 — 2026-10-19

- Cadres PicPak : le logo porte en bas le nom de la chambre et le numéro du
  cadre (« Chambre d'ami invité N°1 »). Le logo est réduit pour laisser la
  place à la légende, écrite en jaune sur fond sombre, en noir sur fond clair.
- Nouvelle option « Logo du cadre n°2 » : un 2e logo pour les cadres n°2
  (et 4…) ; vide, ils reprennent le logo du cadre n°1.
- Si un séjour est programmé dans le calendrier de la chambre, la légende
  affiche à la place **le nom de l'invité du cadre et ses dates**, sur deux
  lignes (« Corinne Jacquouton / ven. 23 → sam. 24 oct. »). Le nom vient du
  helper du lit, à défaut du titre de l'événement correspondant.

## 1.8.1 — 2026-10-19

- Le jour de départ est le dernier jour couvert par l'événement dans le
  calendrier : un séjour saisi « journée entière » du samedi au dimanche
  affiche les photos jusqu'à dimanche minuit (Home Assistant stocke une fin
  exclusive au lundi) ; un événement avec heures, jusqu'à la fin de son jour.
- **Bouton de réinitialisation** (`input_button.reinitialiser_chambre_hote`
  par défaut, réglable) : un appui termine les séjours en cours, les cadres
  affichent le logo à leur prochain réveil. Les séjours à venir s'afficheront
  normalement ; une chambre sans calendrier garde le logo jusqu'au prochain
  changement d'invité.

## 1.8.0 — 2026-10-19

### Cadres PicPak : photos seulement pendant le séjour

- Les cadres n'affichent les photos de l'invité **que pendant son séjour**,
  d'après le calendrier de la chambre (`calendar.<chambre>`, ex.
  `calendar.chambre_ami`) : du jour d'arrivée à **9 h** (réglable : option
  « Heure de début des photos le jour d'arrivée ») jusqu'à la **fin du jour
  de départ**. Avant et après : le logo.
- Le jour de départ est la date de fin de l'événement.
- Le cadre se réveille juste après le début du séjour pour passer aux photos
  à l'heure.
- Sans calendrier pour la chambre, comportement inchangé : l'invité est montré
  dès qu'il est renseigné. Le diaporama des chambres n'est pas concerné.
- Nouvel attribut `sejour_en_cours` sur le capteur de batterie de chaque
  cadre.

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

# HAOS-immich-guest-slideshow

> Diaporama photo Immich par chambre pour Home Assistant, adapté automatiquement aux invités présents.

Intégration HACS basée sur [Immich](https://immich.app) : chaque chambre affiche
un diaporama de photos combinant ses invités (helpers `input_text`) et les
propriétaires, avec cache local et rotation aléatoire.

## Fonctionnement

Deux **personnes permanentes** (par défaut *Propriétaire 1* et *Propriétaire 2* —
remplacez-les dans les options par les noms **exacts** de vos personnes Immich) sont combinées avec les invités saisis dans des
helpers `input_text`, un par lit et par chambre :

Les chambres sont entièrement configurables depuis l'interface ; par défaut :

| Chambre | Helpers |
|---|---|
| Chambre d'ami | `input_text.guest_chambre_d_ami`, `input_text.guest_chambre_d_ami2` |
| Chambre Stitch | `input_text.guest_chambre_stitch`, `input_text.guest_chambre_stitch2` |
| Chambre Ado | `input_text.guest_chambre_ado`, `input_text.guest_chambre_ado2` |

### Logique des recherches Immich

Pour chaque invité, les photos sont choisies par **niveaux de priorité**,
jusqu'au nombre voulu (50 par cadre ou album, taille du cache pour le
diaporama) :

1. l'invité avec l'un des propriétaires (`I+P1`, `I+P2`), ce qui inclut les
   photos avec les deux ;
2. s'il manque des photos et que la chambre a deux invités : les invités
   **ensemble** (`I1+I2`) ;
3. s'il en manque encore : toutes les autres photos de l'invité (`I`).

- **Aucun invité** → aucune photo, les entités passent en `unavailable`.
- **Deux invités** : le diaporama de la chambre entrelace leurs photos ; sur
  les cadres et les albums, chaque lit montre son occupant et les photos où
  ils sont ensemble sont réparties entre les deux lits, sans doublon.
- **Un invité seul dans une chambre à deux lits** : les deux cadres le
  montrent, avec des photos différentes.

Les recherches ne sont faites qu'au besoin (si le niveau 1 suffit, les
suivants ne sont pas interrogés). La sélection est mise en **cache local** ;
la rotation est **aléatoire sans doublon immédiat** et n'interroge **pas**
Immich à chaque changement d'image. Quand un helper change, la sélection de
la chambre est refaite automatiquement.

## Entités créées

- `image.immich_chambre_d_ami`, `image.immich_chambre_stitch`, `image.immich_chambre_ado`
- `sensor.immich_chambre_d_ami_current_search`, `sensor.immich_chambre_stitch_current_search`, `sensor.immich_chambre_ado_current_search`
- par lit, les entités des cadres PicPak (voir plus bas)

Le sensor expose en attributs les invités détectés, le nombre de photos en
cache et la liste des recherches actives.

L'entité image expose `asset_id`, `search`, `photo_count` et
**`object_position`** : la position CSS à donner à la photo (`object-fit:
cover`) pour que **le visage entier des invités reste visible**, sans zoom
(voir l'exemple de carte plus bas).

### Visages toujours entiers dans le diaporama

Pour chaque photo, l'intégration lit les visages détectés par Immich (clé API
avec la permission « face.read ») et calcule `object_position` :

- zone gardée : les visages des invités de la chambre (les deux s'ils sont sur
  la photo), à défaut tous les visages détectés, **agrandie** pour inclure
  cheveux, menton et oreilles ;
- la position est choisie pour que cette zone soit entièrement visible **quelle
  que soit la taille d'affichage** (largeur d'écran, hauteur de la carte), dès
  que la zone visible est au moins aussi grande qu'elle. La photo n'est jamais
  agrandie : on voit toujours la scène, pas seulement le visage ;
- sans visage connu : `50% 30%` (un peu au-dessus du centre).

Un seul appel Immich par photo et par séjour (mémorisé).

## Services

- `immich_guest_slideshow.refresh` — vide et reconstruit le cache d'une
  chambre (paramètre optionnel `room`, ex. `chambre_d_ami`) ou de toutes.
- `immich_guest_slideshow.next` — passe immédiatement à l'image suivante
  (paramètre optionnel `room`).
- `immich_guest_slideshow.link_person` — lie un nom (`name`) à une personne
  Immich (`person_id` : UUID ou URL de sa fiche). La liaison est mémorisée.
- `immich_guest_slideshow.unlink_person` — efface la liaison d'un nom
  (`name`), ou **toutes** les liaisons si `name` est omis. Renvoie le nombre
  de liaisons supprimées.

Le cache est par ailleurs reconstruit automatiquement toutes les 6 heures
(réglable, `0` pour désactiver) afin d'intégrer les nouvelles photos Immich.

## Cadres e-ink PicPak

L'intégration alimente des cadres **PicPak** (e-ink 4,2″, 400×300, 4 couleurs :
noir, blanc, jaune, rouge) flashés avec le firmware communautaire
[open-PicPak](https://github.com/GottZ/open-PicPak) muni du correctif
« tirage Wi-Fi ». Le firmware d'origine n'est pas compatible : il ne sait
recevoir des images qu'en Bluetooth, depuis l'appli du fabricant.

**Un cadre par lit** : le cadre n° 1 d'une chambre suit son 1er helper, le n° 2
le 2e.

- **Deux invités** : chaque cadre montre son occupant.
- **Un invité seul** : les deux cadres le montrent, avec des photos
  **différentes** (tranches disjointes de la même sélection).
- **Chambre libre** : le logo (fichier PNG des options, ou logo texte).

Par séjour, chaque cadre reçoit **50 photos** (réglable), choisies avec la même
logique que le diaporama (invité avec les propriétaires d'abord, puis ses
photos individuelles). La photo change **toutes les heures** (réglable), ou
tout de suite sur un appui du bouton du cadre. Une **pause nocturne**
(ex. `23:00-07:00`) évite tout réveil inutile la nuit.

Chaque photo **remplit tout l'écran** : elle est recadrée au format 400×300 en
gardant au centre le **visage de l'invité** (visages détectés par Immich ; la
clé API doit avoir la permission « face.read », sinon recadrage légèrement
au-dessus du centre). La conversion en 4 couleurs reproduit exactement celle
de l'appli d'origine (distance BT.601, tramage d'Atkinson).

**Logo** : un PNG de exactement 400×300 est affiché pleine page tel quel (le
composer directement dans les 4 couleurs de la dalle donne le rendu le plus
net) ; une autre taille est centrée sur fond blanc. Le SVG n'est pas lu :
convertissez-le en PNG.

### Photos seulement pendant le séjour

Si la chambre a un calendrier `calendar.<chambre>` (ex. `calendar.chambre_ami`),
les cadres n'affichent les photos que **pendant le séjour** : du jour d'arrivée
à 9 h (réglable) jusqu'à la fin du jour de départ (dernier jour couvert par
l'événement). Avant (invité déjà renseigné, séjour à venir) et après : le
logo. Sans calendrier, l'invité est montré dès qu'il est renseigné.

Le logo porte en bas une légende : le nom de l'invité du cadre et les dates de
son séjour s'il y en a un de programmé, sinon « Chambre d'ami invité N°1 ».
Un 2e logo peut être choisi pour les cadres n°2 (options).

Le bouton `input_button.reinitialiser_chambre_hote` (réglable) termine les
séjours en cours : logo sur tous les cadres à leur prochain réveil.

### Fonctionnement

Le cadre dort, se réveille, télécharge
`/api/immich_guest_slideshow/picpak/<chambre>/<lit>?k=<jeton>`, affiche
l'image si elle a changé et se rendort pour la durée fixée par Home Assistant.
Un réveil qui ne doit rien changer reçoit la même image : le cadre ne
rafraîchit pas l'écran, ce qui économise la batterie. L'image suivante est
préparée à l'avance pour répondre en quelques millisecondes.

### Mise en place

1. Options → **Cadres PicPak** : intervalle, nombre de photos, pause nocturne,
   logo, URL locale de Home Assistant vue par les cadres (ex.
   `http://192.168.1.29:8123` — une IP, le firmware ne résout pas les noms
   `.local`).
2. Action `immich_guest_slideshow.picpak_urls` (avec « renvoyer la réponse ») :
   l'URL de chaque cadre, jeton compris. Ne la partagez pas.
3. Flasher et configurer chaque cadre avec cette URL (paquet firmware fourni
   séparément, script `picpak_installer.py`).

### Entités par cadre

- `image.picpak_<chambre>_<lit>` : aperçu de ce qu'affiche la dalle, avec ses
  vraies couleurs ;
- `sensor.picpak_<chambre>_<lit>_batterie` : batterie (%), avec en attributs la
  tension, l'invité, la photo en cours, la cause du dernier réveil ;
- `sensor.picpak_<chambre>_<lit>_derniere_connexion` : pour repérer un cadre
  muet (batterie vide, hors de portée Wi-Fi).

### Services

- `immich_guest_slideshow.picpak_urls` — URL à enregistrer dans chaque cadre
  (paramètre optionnel `base_url`).
- `immich_guest_slideshow.picpak_next` — photo suivante au prochain réveil
  (paramètres optionnels `room`, `bed`).

## Installation

### Via HACS (recommandé)
1. HACS → Intégrations → menu ⋮ → *Dépôts personnalisés*.
2. Ajouter l'URL de ce dépôt, catégorie **Integration**.
3. Installer *Immich Guest Slideshow* puis redémarrer Home Assistant.

### Manuelle
Copier `custom_components/immich_guest_slideshow/` dans le dossier
`custom_components/` de votre configuration, puis redémarrer.

## Configuration

1. *Paramètres → Appareils et services → Ajouter une intégration →
   Immich Guest Slideshow*.
2. Saisir l'URL (ex. `http://192.168.1.100:2283`) et la **clé API Immich**
   (créée dans Immich : *Account Settings → API Keys*). La clé est stockée
   dans le stockage chiffré de Home Assistant, jamais dans le code.
3. Options disponibles ensuite (bouton *Configurer*) :
   - **Réglages généraux** : durée entre deux images, taille du cache,
     rafraîchissement périodique du cache (heures), personnes permanentes ;
   - **Ajouter une chambre** : nom libre + sélection des helpers
     `input_text` (l'identifiant de la chambre est le slug du nom) ;
   - **Supprimer des chambres** : les appareils et entités des chambres
     retirées sont nettoyés automatiquement.

Les trois chambres du tableau ci-dessus sont créées par défaut ; pour
modifier une chambre, supprimez-la puis recréez-la.

### Désigner une personne par son ID Immich

Partout où un nom est attendu (personnes permanentes dans les options, valeur
d'un helper `input_text` d'invité), vous pouvez saisir à la place l'**ID de la
personne Immich** (UUID), ou coller l'URL de sa fiche :

```
1238c4da-3b98-41b9-b795-81d51e95a6d5
http://192.168.1.90:2283/people/1238c4da-3b98-41b9-b795-81d51e95a6d5
```

L'ID est repris de l'URL de la fiche de la personne dans Immich
(`/people/<id>`). Les noms continuent de fonctionner ; l'ID est utile en cas
d'homonymes, de personne sans nom ou renommée.

### Personne introuvable ou peu de photos : liaison mémorisée

Si un nom ne correspond à personne dans Immich, ou si un invité a moins de
**2 photos** (réglable : *Photos minimum par invité*), Home Assistant crée :

- un élément dans **Paramètres → Système → Réparations** ;
- une notification persistante (cloche) qui y renvoie.

Cliquez sur l'élément, collez l'**ID** de la bonne personne (ou l'URL de sa
fiche Immich) : le nom est alors **lié** à cet ID, pour toutes les chambres,
et la liaison est conservée après redémarrage. Elle prime sur la recherche par
nom. L'élément disparaît dès que le problème est résolu.

Les photos trouvées sont **toujours affichées**, même sous le minimum : il ne
sert qu'à prévenir.

Si un **helper configuré pour une chambre n'existe pas** (renommé ou
supprimé), une réparation « Helper introuvable » le signale : sans elle, la
chambre ne verrait simplement aucun invité. Créez le helper, ou supprimez puis
recréez la chambre dans *Configurer* avec les bons helpers.

Pour défaire une liaison : service `immich_guest_slideshow.unlink_person`
(avec `name`, ou sans paramètre pour tout effacer).

## Prérequis

- Home Assistant 2024.6+ (testé HAOS 2026.x)
- Immich ≥ 1.118 (testé 3.0.1) avec la reconnaissance faciale activée et les
  personnes **nommées** (les noms des helpers doivent correspondre aux noms
  Immich)
- Les helpers `input_text` listés ci-dessus doivent exister

## Exemple de carte Lovelace

```yaml
type: picture-entity
entity: image.immich_chambre_d_ami
show_state: false
show_name: false
camera_view: auto
```

Avec recadrage sur les visages (nécessite [card-mod](https://github.com/thomasloven/lovelace-card-mod)) :

```yaml
type: picture
image_entity: image.immich_chambre_d_ami
card_mod:
  style: |
    img {
      width: 100%;
      max-height: 300px;
      object-fit: cover !important;
      object-position: {{ state_attr('image.immich_chambre_d_ami', 'object_position') or '50% 30%' }} !important;
    }
```

## Développement

```bash
pip install -r requirements_test.txt
pytest
```

La CI GitHub Actions exécute hassfest, la validation HACS et les tests
pytest avec couverture à chaque push.

## Licence

MIT

# HAOS-immich-guest-slideshow

> Les photos de vos invités, tirées d'Immich, dans Home Assistant : diaporama
> des dashboards et cadres e-ink PicPak de chaque chambre.

Intégration HACS pour chambres d'hôtes, basée sur [Immich](https://immich.app).
Chaque chambre est **une ligne** de l'intégration (Paramètres → Appareils et
services → Immich Guest Slideshow) qui regroupe :

- le **diaporama** affiché dans les dashboards invité et gestionnaire ;
- les **cadres PicPak dans la chambre** (0, 1 ou 2) ;
- le **cadre PicPak de la porte** (facultatif).

## Installation

1. HACS → Intégrations → ⋮ → Dépôts personnalisés → ajouter ce dépôt
   (catégorie *Intégration*), installer **Immich Guest Slideshow**, redémarrer.
2. Paramètres → Appareils et services → **Ajouter une intégration** → Immich
   Guest Slideshow : URL d'Immich et **clé API** (permissions de lecture des
   personnes, photos et visages ; albums si vous utilisez `sync_albums`).
3. Sur l'intégration, **Ajouter une chambre** pour chaque chambre.

Prérequis : Home Assistant 2025.6 ou plus récent (testé 2026.10), Immich avec
la reconnaissance faciale et les personnes nommées (testé 3.0.1).

## Réglages

### Généraux (Configurer → Réglages généraux)

| Réglage | Par défaut |
|---|---|
| Propriétaires (noms exacts ou ID Immich, séparés par des virgules) | — |
| Dashboards : secondes entre deux photos | 15 |
| Dashboards : nombre de photos par chambre | 100 |
| Heure de début du séjour le jour d'arrivée | 9 h |
| Relecture d'Immich (nouvelles photos) | toutes les 6 h |
| Photos minimum par invité (sinon : réparation) | 2 |

### Une chambre (ligne de la chambre → Configurer)

| Réglage | Rôle |
|---|---|
| Nom | « Chambre d'ami » ; l'identifiant (`chambre_d_ami`) est fixé à la création et ne change plus (entités, URL des cadres) |
| Calendrier des séjours | `calendar.xxx`, un événement par séjour ou par invité |
| Invités | 1 ou 2 helpers `input_text` ; le cadre n°1 suit le 1er, le n°2 le 2e |
| Cadre PicPak sur la porte | oui / non |
| Cadres PicPak dans la chambre | 0, 1 ou 2 |

Les helpers peuvent contenir « Nom (2026-10-23 → 2026-10-24) » : seul le nom
est envoyé à Immich. Un nom peut être remplacé par l'**ID** de la personne
Immich ou l'URL de sa fiche (`http://…/people/…`).

### Cadres PicPak (Configurer → Cadres PicPak)

Minutes entre deux photos (60), pause nocturne (ex. `01:00-07:00`), logo (PNG,
idéalement 400×300, or sur noir), 2e logo facultatif pour le cadre n°2, URL
locale de Home Assistant vue par les cadres, bouton de réinitialisation
(`input_button.reinitialiser_chambre_hote`), et nombre de photos par cadre
pour une chambre **sans** calendrier.

## Le séjour

D'après le calendrier de la chambre :

- **en cours** : du jour d'arrivée à **9 h** jusqu'au **lendemain du dernier
  jour à 00:01**. Ex. : nuit du samedi au dimanche (événement « journée
  entière » samedi → dimanche) → de samedi 9 h à lundi 00:01 ;
- **programmé** : le prochain séjour du calendrier, quelle que soit sa date ;
- **libre** : rien de prévu.

Le **bouton de réinitialisation** met fin tout de suite aux séjours en cours
(un séjour futur s'affichera normalement). Sans calendrier, l'invité est
considéré présent dès qu'il est renseigné.

## Choix des photos

Pour chaque invité, par ordre de priorité, jusqu'au nombre voulu :

1. l'invité avec **tous les propriétaires** (invité + Julien + Yves) ;
2. l'invité avec **chaque propriétaire** (invité + Julien, puis invité + Yves) ;
3. les **deux invités ensemble** (si la chambre en a deux) ;
4. l'**invité seul**.

Avec deux invités, leurs photos sont entrelacées à chaque niveau. Les
recherches ne sont faites qu'au besoin.

## Dashboards

`image.immich_<chambre>` affiche une photo des invités **toutes les 15 s**,
parmi **100**, **dès que l'invité est renseigné** (avant même son arrivée).
Appui court sur la carte : photo suivante (`immich_guest_slideshow.next`) ;
appui long : nouvelle sélection (`immich_guest_slideshow.refresh`).

Attributs utiles aux cartes :

| Attribut | Exemple |
|---|---|
| `object_position` | `47.5% 12.0%` : position CSS qui garde le **visage entier** des invités visible en `object-fit: cover` |
| `invites` | `["Corinne Jacquouton", "Régis Jacquouton"]` |
| `sejour` | `en_cours`, `programme` ou `libre` |
| `sejour_dates` | `ven. 23 → sam. 24 oct.` |
| `sejour_debut`, `sejour_fin` | `2026-10-23`, `2026-10-24` (premier et dernier jour) |

Carte avec cadrage sur les visages ([card-mod](https://github.com/thomasloven/lovelace-card-mod)) :

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

## Cadres e-ink PicPak

Cadres **PicPak** 4,2″ (400×300, noir / blanc / jaune / rouge) flashés avec le
firmware [open-PicPak](https://github.com/GottZ/open-PicPak) et son correctif
« tirage Wi-Fi ». À chaque réveil, le cadre télécharge son image déjà prête
et l'heure de son prochain réveil ; une image inchangée n'est pas redessinée.

### Dans la chambre

| Chambre | Écran |
|---|---|
| libre | logo + nom de la chambre |
| séjour programmé | logo + nom de la chambre + noms des invités + dates (les deux noms sur chaque cadre ; sans invité renseigné, le titre des événements) |
| séjour en cours | les photos des invités, **une par heure**, autant que d'heures de séjour hors pause nocturne (rotation s'il y en a moins) |

Pendant le séjour : deux cadres et deux invités → **un invité par cadre** ;
un invité seul → ses photos sur les deux cadres, **sans doublon** ; un seul
cadre pour deux invités → leurs photos mélangées. Chaque photo remplit
l'écran, recadrée sur le visage de l'invité (permission « face.read »).

### Sur la porte

| Chambre | Écran |
|---|---|
| libre | logo + nom de la chambre |
| séjour programmé | « **Bientôt dans la chambre d'ami** », le visage de chaque invité dans le losange du logo Petit Lecussan (à la place du « PL », mêmes bordures), les prénoms dessous, la date prévue |
| séjour en cours | la même carte avec « **Bienvenue dans la chambre d'ami** » |

### Mise en place

1. Régler l'URL locale de Home Assistant vue par les cadres (ex.
   `http://192.168.1.29:8123` : une IP, le firmware ne résout pas `.local`).
2. Action `immich_guest_slideshow.picpak_urls` (« renvoyer la réponse ») :
   l'URL de chaque cadre, jeton compris (emplacements `1`, `2`, `porte`).
   Ne la partagez pas.
3. Flasher et configurer chaque cadre avec son URL.

### Entités par cadre

- `image.picpak_<chambre>_<1|2|porte>` : aperçu de l'écran, vraies couleurs ;
- `sensor.picpak_<chambre>_<…>_batterie` : batterie (%), avec l'invité,
  l'état (`libre` / `programme` / `en_cours`), la photo en cours, la cause du
  dernier réveil ;
- `sensor.picpak_<chambre>_<…>_derniere_connexion` : pour repérer un cadre muet.

## Actions (services)

| Action | Rôle |
|---|---|
| `refresh` | nouvelle sélection (chambre ou toutes) |
| `next` | photo suivante du diaporama |
| `picpak_urls` | URL à saisir dans chaque cadre |
| `picpak_next` | photo suivante sur les cadres de chambre (au prochain réveil) |
| `sync_albums` | un album Immich par lit avec ses photos |
| `link_person` / `unlink_person` | lier un nom à l'ID d'une personne Immich |

## Personne introuvable ou peu de photos

Si un nom ne correspond à personne dans Immich, ou si un invité a moins de
2 photos, un élément apparaît dans **Paramètres → Système → Réparations** (et
une notification). Collez-y l'ID de la bonne personne (ou l'URL de sa fiche) :
la liaison est mémorisée pour toutes les chambres. Un helper configuré mais
inexistant est aussi signalé.

## Mise à jour depuis la v1

La migration est automatique au premier démarrage de la v2 :

- chaque chambre des anciennes options devient une **ligne** (même
  identifiant, mêmes entités) ;
- une chambre reçoit autant de **cadres** qu'elle en avait de **déjà
  connectés** (les cadres flashés gardent leur URL et leur jeton) ; le cadre
  de porte est à activer dans les réglages de la chambre ;
- le calendrier de la chambre est deviné (`calendar.<chambre>`) : **vérifiez-le**
  dans les réglages de chaque chambre ;
- les dashboards passent à 100 photos, une toutes les 15 s ;
- l'ancien réglage « Pendant le séjour, afficher : photos / visages »
  disparaît : les visages sont sur le cadre de la porte, les photos dans la
  chambre.

## Développement

```bash
pip install -r requirements_test.txt
pytest
```

## Licence

MIT

# HAOS-immich-guest-slideshow

Diaporama intelligent basé sur [Immich](https://immich.app), intégré à Home Assistant,
avec une logique de sélection de photos différente pour chaque chambre en fonction
des invités présents.

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

- **Aucun invité** → aucune photo, les entités passent en `unavailable`.
- **Un invité (I1)** → 3 recherches : `I1+P1`, `I1+P2`, `I1+P1+P2`.
- **Deux invités (I1, I2)** → 9 recherches : les 3 combos de I1, les 3 combos
  de I2, puis `I1+I2+P1`, `I1+I2+P2`, `I1+I2+P1+P2`.

Les photos de toutes les recherches sont dédupliquées puis mises en **cache
local** ; la rotation est **aléatoire sans doublon immédiat** et n'interroge
**pas** Immich à chaque changement d'image. Quand un helper change, le cache
de la chambre concernée est vidé et reconstruit automatiquement.

## Entités créées

- `image.immich_chambre_d_ami`, `image.immich_chambre_stitch`, `image.immich_chambre_ado`
- `sensor.immich_chambre_d_ami_current_search`, `sensor.immich_chambre_stitch_current_search`, `sensor.immich_chambre_ado_current_search`

Le sensor expose en attributs les invités détectés, le nombre de photos en
cache et la liste des recherches actives.

## Services

- `immich_guest_slideshow.refresh` — vide et reconstruit le cache d'une
  chambre (paramètre optionnel `room`, ex. `chambre_d_ami`) ou de toutes.
- `immich_guest_slideshow.next` — passe immédiatement à l'image suivante
  (paramètre optionnel `room`).

Le cache est par ailleurs reconstruit automatiquement toutes les 6 heures
(réglable, `0` pour désactiver) afin d'intégrer les nouvelles photos Immich.

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

## Développement

```bash
pip install -r requirements_test.txt
pytest
```

La CI GitHub Actions exécute hassfest, la validation HACS et les tests
pytest avec couverture à chaque push.

## Licence

MIT

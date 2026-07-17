# Changelog

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

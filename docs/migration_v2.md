# Migration V1.2.x → V2.1.0

[Retour au README](../README.md)

La V2 est une refonte fonctionnelle : ne pas comparer directement les scores Z et les probabilités des deux versions comme s'ils avaient la même signification.

## Avant la migration

Sauvegarder le script, les blocs Tempo du `configuration.yaml`, la carte et les automatisations existantes. Noter les noms des entités et les dépendances dans vos automatisations. Conserver les identifiants API localement, ne jamais les publier.

## Étapes

1. Dans DATA RTE, ajouter l'API Signal V2 à l'application utilisée par le script (en plus de Consumption, Generation Forecast et Tempo Like Supply Contract).
2. Remplacer le Python par `tempo_prevision_simple.py` V2.1.0 et renseigner les identifiants locaux ou les variables d'environnement.
3. Télécharger les deux matrices officielles de la période et les placer à côté du script (`tableauseuils_pp_2627_j-1.csv`, `tableauseuils_pp_2627_j-2.csv`).
4. Fusionner les définitions de [configuration.yaml](../examples/configuration.yaml) : capteur principal, attributs `version` et `diagnostics`, délai 90 s, 18 probabilités, couleurs et scores/commentaires facultatifs pour conserver les noms d'entités utilisés dans d'autres automatisations. Remplacer les anciens templates plutôt que de créer des doublons.
5. Conserver les anciens capteurs Z uniquement si d'autres usages en dépendent. L'exemple ne les crée plus. Adapter les automatisations qui utilisaient leur ancienne échelle ou les sources ENR de V1. `red_deadline_pressure` ne pilote plus rien.
6. Choisir un mode d'actualisation : scan toutes les deux heures, ou automation ciblée avec scan quotidien. Ne pas cumuler plusieurs anciennes automatisations de rafraîchissement.
7. Vérifier la configuration HA, recharger Command Line et les modèles. Remplacer la carte par [Affichage_Lovelace](../examples/Affichage_Lovelace). L'exemple public retire le bouton de retour vers le tableau de bord personnel de l'auteur.
8. Contrôler les attributs JSON et les entités de probabilités, puis tester la carte sur une couleur publiée, une journée interdite PP et une journée à seuil fini.

## Contrôles et dépannage

| Symptôme | Contrôle |
|---|---|
| État `error`, stocks invalides | Les trois stocks doivent être des entiers disponibles. Vérifier les noms des entités et attendre l'initialisation de RTE Tempo. |
| HTTP 401/403 | Identifiants OAuth et souscriptions aux quatre API. |
| `week: TimeoutError` | API hebdomadaire indisponible ; réessayer raisonnablement plus tard. Aucun cache persistant. |
| Matrice manquante | Nom exact, saison, emplacement et CSV intact. En hiver, vérifier `matrices_loaded` et les warnings. |
| ENR météo / extrapolées | Ce n'est pas une erreur en soi ; vérifier `gen_source` et les compteurs par filière. |
| État `ok` mais prévision inconnue | Lire `diagnostics.warnings` et `data_quality`, contrôler les heures disponibles. |
| `Unavailable` dans la carte | Vérifier les 18 capteurs de probabilité et leurs identifiants, ainsi que les extensions HACS. Une valeur inconnue doit rester indisponible. |
| Mise à jour manuelle sans effet | Recharger `command_line` depuis Actions ; cela recharge tous les capteurs de cette intégration. |

Pour inspecter la sortie :

```jinja
{{ states.sensor.tempo_prevision_simple.attributes | to_json(pretty_print=True) }}
```

Le JSON doit afficher la version 2.1.0 et une heure récente. Les six blocs peuvent être partiellement renseignés. Un test en conditions réelles le 27 novembre a notamment révélé un timeout de l'API hebdomadaire résolu lors d'un nouvel appel ; ce cas reste possible.

## Retour arrière

Restaurer ensemble le Python V1, les anciens templates, la carte et les automatismes associés. Ne pas mélanger les deux contrats JSON. La V1 ne prend toutefois pas en charge la nouvelle méthode PP ; elle ne constitue donc plus une référence fiable pour les rouges.

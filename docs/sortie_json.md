# Format de sortie JSON — V2.1.0

[Retour au README](../README.md) · [Fonctionnement](fonctionnement.md)

Le programme écrit un objet JSON sur stdout. Home Assistant expose les blocs `J+1` à `J+6` comme attributs du capteur `sensor.tempo_prevision_simple`. Un exemple de sortie réelle du 27 novembre 2026, 13h04, est disponible dans [examples/sortie_json_v2.1.0.json](../examples/sortie_json_v2.1.0.json). Ce n'est pas une prévision actuelle.

## Métadonnées globales

| Champ | Type | Description |
|---|---|---|
| `generated_at` | string ISO 8601 | Heure de référence du calcul, avec fuseau Paris. |
| `version` | string | `2.1.0` pour cette version. |
| `J+1` … `J+6` | object ou null | Journées à venir ; null en cas d'erreur fatale. |
| `diagnostics` | object | Diagnostics globaux (peut être absent en cas d'erreur fatale). |
| `error` | string ou null | Erreur fatale ; null ne garantit pas la complétude de la prévision. |

`friendly_name` est ajouté par Home Assistant, pas par le Python.

## Couleur et probabilités

| Champ | Type | Description |
|---|---|---|
| `date` | string | Date cible `YYYY-MM-DD`. |
| `couleur` | string | `bleu`, `blanc`, `rouge` ou `inconnue`. Couleur favorite, sauf annonce officielle. |
| `modele` | string | `modele_interne`, `override_RTE`, `PP_OFFICIAL` ou `PP_MATRIX_CONSTRAINT`. |
| `p_bleu`, `p_blanc`, `p_rouge` | number 0–1 ou null | Probabilités indicatives. Somme 1 lorsque le triplet est complet. Si une partie est indisponible, le rouge peut néanmoins rester renseigné. |
| `confidence_score` | integer 1–5 ou null | Indice heuristique, pas une précision mesurée. |
| `confidence_label` | string | Très faible, Faible, Moyenne, Forte, Très forte, Indisponible. |
| `confidence_comment` | string | Commentaire du moteur : couleur publiée, données insuffisantes ou probabilités non calibrées. La carte produit ses propres commentaires complémentaires. |

`override_RTE` désigne une couleur officielle issue du calendrier ou de l'argument HA accepté par le moteur ; `PP_OFFICIAL` un signal PP/non-PP connu et non traité comme tardif ; `PP_MATRIX_CONSTRAINT` une contrainte déterministe rouge/non-rouge de la matrice. Seule la couleur officielle Tempo constitue la référence finale pour le tarif.

Les nombres sont des fractions : `0.481` correspond à 48,1 %. Les capteurs de l'exemple multiplient par 100. Ne jamais substituer 0 à null : 0 signifie une probabilité nulle selon le modèle, null signifie non calculée. En cas d'absence, ne pas en déduire qu'une consommation coûteuse est sans risque.

## Données énergétiques (MW)

| Champ | Type | Description |
|---|---|---|
| `c_mw` | number/null | Consommation moyenne sur la journée civile. |
| `gen_mw` | number/null | Production ENR moyenne sur la journée civile. |
| `gen_source` | string | Origine du profil de production. |
| `c_net_mw` | number/null | Moyenne horaire de consommation résiduelle de la journée civile. |
| `c_net_tempo_mw` | number/null | Moyenne sur la fenêtre 06h–06h ; peut être null même si la moyenne civile existe. |
| `pp_peak_mw` | number/null | Maximum horaire de consommation résiduelle sur la journée civile. Ce n'est pas la moyenne nette ni une pointe brute moins une production moyenne. |

`gen_source` :

- `RTE` : profil national intégralement fourni par RTE.
- `estime_meteo_ENR` : profil complété grâce à Open-Meteo, éventuellement mêlé à des heures ou composantes RTE.
- `estime_profil_horaire_ENR` : complété par des profils nationaux RTE antérieurs.
- `estime_mixte_ENR` : météo et extrapolation de profils, avec données RTE si disponibles.
- `missing` : profil incomplet ; la production moyenne ou la pointe peuvent être indisponibles.

## PP et matrices Bellman

| Champ | Type | Description |
|---|---|---|
| `pp_status` | string | `PP`, `NON_PP`, `PENDING` (pas de signal connu), `UNAVAILABLE` (signal non accessible). |
| `pp_source` | string | Source du stock initial PP : `PP_history`, `Tempo_proxy`, `outside_PP_season`. |
| `pp_probability` | number/null | Probabilité agrégée du signal PP ; peut différer du rouge Tempo, notamment si un PP tardif est possible. |
| `pp_thresholds` | array | Scénarios de seuils avant affectation de la journée. |
| `pp_early_probability_indicative` | number/null | Indicateur de franchissement J-2 à l'horizon J+2 si calculable. N'est pas à ajouter à `p_rouge`. |
| `pp_reason` | string | Motif dominant pondéré parmi les scénarios. |
| `pp_stock_expected_after` | number/null | Espérance du nombre cumulé de PP consommés après cette journée. |

Chaque entrée de `pp_thresholds` contient :

| Champ | Description |
|---|---|
| `pp_used` | Nombre de PP consommés avant le jour. |
| `weight` | Masse probabiliste du scénario, arrondie à six décimales. Plusieurs entrées peuvent avoir le même `pp_used` et le même seuil. |
| `threshold_j1_mw`, `threshold_j2_mw` | Nombre positif en MW, `0` (tirage forcé), chaîne `"Infinity"` (interdit) ou `null` (inconnu). |
| `reason` | Motif du scénario : par exemple `BELLMAN_CALCULATED_THRESHOLD`, `CALENDAR_BLOCK`, `MONTHLY_CAP`, `RESERVED_PP_CAP`, `MATRIX_MISSING`, `PP_OFFICIAL`, `TEMPO_RED_PP_PROXY`, `TEMPO_NON_RED_PP_PROXY`, ou motif de blocage/forçage du CSV. |
| `crosses_j1`, `crosses_j2` | Boolean ou null ; comparaison stricte de la pointe et du seuil, avant les éventuels overrides. |

La jauge affiche la pointe et les seuils finis distincts. Pour plusieurs seuils, l'approche est une plage `100 × pointe / seuil_max` à `100 × pointe / seuil_min`. Il s'agit d'un ratio de puissance, pas d'une probabilité. Les poids ne sont pas matérialisés par l'épaisseur des traits. La jauge est masquée pour une couleur officielle, un PP déjà connu, un tirage contraint ou une pointe absente, selon les conditions de la carte.

## Stocks

| Champ | Description |
|---|---|
| `red_remaining`, `white_remaining`, `blue_remaining` | Compteurs entiers de présentation après le jour, décrémentés selon les couleurs favorites. |
| `expected_remaining` | Objet `rouge`, `blanc`, `bleu` : espérances des stocks après le jour, non nécessairement entières. |
| `stock_uncertain` | Boolean : une prévision antérieure a nécessité une propagation avec données manquantes ; false ne signifie pas que le stock futur est certain. |

## Modèle blanc et météo thermique

| Champ | Description |
|---|---|
| `z`, `z_raw` | Indice normalisé interne, identique dans cette version ; number/null. |
| `z_source` | `annual_forecast`, `fallback_std` ou null. |
| `T_nat`, `T_norm` | Température nationale estimée et normale (°C) ; number/null. |
| `delta_T` | Écart `T_nat - T_norm` en °C ; number/null. |
| `meteo_bias` | Amplitude non signée du biais thermique (0, 0.03, 0.06, 0.09 ou null) ; ne mesure pas nécessairement le déplacement final après contraintes. |
| `white_bellman_bonus` | Masse de probabilité blanche supplémentaire due au bonus expérimental, agrégée sur les branches ; number/null. |
| `white_bellman_bonus_conditional` | Bonus conditionnel à la masse non rouge calculée ; number/null. |
| `red_deadline_pressure` | Champ hérité de V1, toujours null en V2.1.0. |
| `z_debug` | Facultatif (`ENABLE_DEBUG=True`) : `annual_days_count`, `states` (nombre d'états après propagation). |

Les anciens champs de debug détaillé et sources ENR de V1 ne sont plus présents. Le Z n'a plus de rôle direct dans le calcul du rouge.

## Qualité des données

`data_quality` est présent dans chaque bloc journalier :

| Champ | Description |
|---|---|
| `gen_profile_dates` | Dates des profils RTE utilisés en secours. |
| `gen_rte_hours` | Nombre d'heures disposant d'une production nationale complète issue de RTE. |
| `gen_weather_hours` | Heures complétées par le modèle météo. |
| `gen_persistence_hours` | Heures complétées par profil historique RTE. |
| `gen_component_sources` | Objet par filière (`WIND_ONSHORE`, `WIND_OFFSHORE`, `SOLAR`, ou format hérité `WIND`) avec compteurs `RTE` et `meteo`. |
| `hours` | Heures de consommation disponibles. |
| `matched_hours` | Heures résiduelles retenues pour le calcul civil ; 0 si le profil civil est incomplet. |
| `expected_hours` | Heures attendues sur la journée civile (23, 24 ou 25). |
| `tempo_hours`, `tempo_expected_hours` | Heures disponibles/attendues pour la fenêtre 06h–06h. |
| `white_net_source` | `06h-06h`, `calendar_day_fallback`, `missing`. |
| `probabilities_calibrated` | false dans cette version. |
| `notes` | Liste des avertissements propres à la journée. |

## Diagnostics globaux

`diagnostics.warnings` est une liste de messages (timeouts, matrices invalides, stocks incohérents, etc.). `matrices_loaded` liste les matrices valides chargées, par exemple `2627_j1`, `2627_j2` ; en dehors de la saison PP, cette liste peut être vide. `probabilities_calibrated` est false. `enr_model` décrit l'activation, les nombres de points terrestres et maritimes, `capacity_mw`, `weather_hours_by_component` (couverture totale de la requête météo, pas seulement six jours) et `calibrated` (false).

`tempo_stock_source` indique `Tempo_history`, `HA_end_today`, `HA_tomorrow_already_planned`, `HA_today_included` ou `HA_unverified`. `tempo_stock_at_end_today` contient les trois stocks réconciliés avant le lendemain.

## Exemple Home Assistant

```jinja
{% set j = state_attr('sensor.tempo_prevision_simple', 'J+3') %}
{% if j is mapping and j.get('p_rouge') is number %}
  {{ (100 * j.get('p_rouge')) | round(1) }} %
{% else %}
  Indisponible
{% endif %}
```

Pour un automatisme sensible aux tarifs, vérifier aussi la date cible, la fraîcheur de `generated_at` et la disponibilité de la couleur officielle. Une sortie partielle ou ancienne ne doit pas être traitée comme un signal de sécurité.

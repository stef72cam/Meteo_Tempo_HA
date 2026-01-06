##  Format de sortie JSON 

Le script retourne un objet JSON contenant :
- un bloc par jour de prévision : `J+1` à `J+6`
- des métadonnées globales (`generated_at`, éventuellement `error`)

Chaque bloc `J+n` est **autosuffisant** et décrit :
- les données énergétiques utilisées
- l’indicateur de tension calculé
- la décision de couleur Tempo
- les indicateurs de confiance

---

## 🧱 Structure générale

{
"J+1": { ... },
"J+2": { ... },
...
"generated_at": "timestamp ISO-8601",
"friendly_name": "tempo_prevision_simple"
}


---

##  Bloc journalier (`J+1` → `J+6`)

### `date`
- **Type** : `string` (`YYYY-MM-DD`)
- **Signification** : date calendaire correspondant au jour prévu.

---

### `couleur`
- **Type** : `string`
- **Valeurs** : `bleu` | `blanc` | `rouge`
- **Signification** : couleur Tempo finale retenue après application de
  l’ensemble des règles, filtres et contraintes.

---

### `modele`
- **Type** : `string`
- **Valeurs possibles** :
  - `modele_interne`
  - `override_RTE`
- **Signification** :
  - `modele_interne` : couleur issue du modèle probabiliste interne.
  - `override_RTE` : couleur forcée à partir de la publication officielle RTE (J+1 uniquement).

---

## ⚡ Données énergétiques (MW)

### `c_mw`
- **Type** : `number`
- **Signification** : consommation électrique nationale prévue (en MW).
- **Source** : API RTE (prévisions short-term ou weekly).

---

### `gen_mw`
- **Type** : `number`
- **Signification** : estimation de la production d’énergies renouvelables (ENR) utilisée.

---

### `gen_source`
- **Type** : `string`
- **Valeurs possibles** :
  - `RTE`
  - `estime_ratio_J+1`
  - `carryover_last_known`
- **Signification** :
  - indique l’origine réelle de la valeur `gen_mw`
  - `carryover_last_known` signifie que la valeur ENR a été prolongée au-delà
    de l’horizon de publication RTE.

---

### `c_net_mw`
- **Type** : `number`
- **Signification** : consommation nette utilisée pour le calcul de la tension.
  - c_net_mw = c_mw - gen_mw

---

##  Indice de tension réseau

### `z`
- **Type** : `number`
- **Signification** : indice de tension normalisé du réseau électrique.
- **Principe** :
  - calculé à partir de `c_net_mw`
  - calibré sur un historique glissant
  - normalisation par quantiles (Q40 / Q80)

Plus `z` est élevé → réseau sous tension → probabilité blanc / rouge plus forte.

---

### `z_source`
- **Type** : `string`
- **Valeur** : `annual_forecast`
- **Signification** : source utilisée pour la calibration de l’indice Z.

---

##  Contraintes Tempo

### `red_remaining`, `white_remaining`
- **Type** : `integer`
- **Signification** : nombre de jours Tempo restants au moment du calcul.
- **Utilisation** :
  - empêche des scénarios impossibles
  - biaise volontairement les probabilités en fin de saison.

---

##  Probabilités

### `p_bleu`, `p_blanc`, `p_rouge`
- **Type** : `number` (0.0 → 1.0)
- **Signification** : probabilités internes estimées pour chaque couleur Tempo.
- **Remarques** :
  - des valeurs proches indiquent une forte incertitude
  - en cas de `override_RTE`, la probabilité de la couleur officielle est forcée à `1.0`.

---

##  Système de confiance

### `confidence_score`
- **Type** : `integer` (1 → 5)
- **Signification** : score synthétique de confiance basé sur :
  - l’écart entre probabilités
  - l’éloignement temporel (J+1 vs J+6)
  - les contraintes Tempo restantes
  - la fiabilité des données (extrapolation ENR, etc.)

---

### `confidence_label`
- **Type** : `string`
- **Exemples** :
  - `Très forte`
  - `Forte`
  - `Moyenne`
  - `Faible`
  - `Très faible`
- **Signification** : interprétation humaine du score de confiance.

---

### `confidence_comment`
- **Type** : `string`
- **Signification** : commentaire contextuel expliquant la situation du jour
  (confirmation RTE, duel serré, risque rouge, etc.).

---

##  Bloc de debug du calcul Z

### `z_debug`
Expose comment l’indice Z a été calculé.

---

### `annual_days_count`
- Nombre total de jours disponibles dans le jeu de données annuel RTE.

---

### `annual_first_date`, `annual_last_date`
- Bornes temporelles du dataset annuel utilisé.

---

### `annual_sample_dates`
- Échantillon informatif des dates extrêmes du dataset.

---

### `z_try`
- Méthode de calcul tentée (`annual_forecast` dans le cas nominal).

---

### `z_error`, `z_error_type`
- Renseignés uniquement si une erreur ou un filtrage est survenu.

---

### `z_calibration`
Détails **réels** de la fenêtre utilisée pour calibrer l’indice Z.

Champs :
- `window_days` : taille de la fenêtre glissante (365 jours).
- `window_start_inclusive` : première date incluse.
- `window_end_inclusive` : dernière date incluse.
- `history_count` : nombre de points réellement utilisés.
- `history_first_date`, `history_last_date` : bornes effectives après filtrage.
- `method` : méthode de normalisation (`rte_like_quantiles`).
- `q40`, `q80` : quantiles utilisés.
- `denom` : dénominateur de normalisation (`q80 - q40`).

---

## 🕒 Métadonnées globales

### `generated_at`
- **Type** : timestamp ISO-8601
- **Signification** : date et heure exactes de génération des prévisions.

---

### `error`
- **Optionnel**
- **Signification** : message d’erreur fatal si l’exécution du script a échoué.

---

##  Notes d’interprétation

- Ce modèle fournit une **prévision probabiliste**, pas une annonce officielle EDF/RTE.
- La fiabilité augmente à mesure que l’on se rapproche du jour J.
- Le comportement est volontairement proche d’une **prévision météo** :
  explicable, contrainte et incertaine par nature.


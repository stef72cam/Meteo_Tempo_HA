# Tempo Prévision Simple

Outil de prévision **probabiliste** des jours **Tempo EDF (Bleu / Blanc / Rouge)**  
basé sur les **données RTE**, un **Z-score de tension du système électrique**,  
et un ensemble de **règles métier explicites, maîtrisées et explicables**.

L’objectif est d’anticiper les couleurs Tempo **48 à 72 heures à l’avance**,  
avec une **fiabilité croissante à l’approche du jour J**, tout en restant :
- transparent,
- stable,
- compréhensible,
- ajustable.

---

## Fonctionnement général

Le modèle calcule, pour chaque jour J+1 à J+6 :
- une probabilité **Bleu / Blanc / Rouge**
- une couleur dominante
- un score de confiance (1 à 5)
- un commentaire explicatif

Il ne s’agit **pas** d’un modèle d’apprentissage automatique,  
mais d’un **système déterministe** fondé sur :
- des indicateurs physiques (consommation, production),
- des règles Tempo connues,
- une logique probabiliste progressive.

---

## Sources de données

### RTE (Open Data)
- Consommation nationale prévisionnelle (MW)
- Prévisions de production ENR (éolien / solaire)
- Données journalières et multi-jours

### EDF Tempo
- Couleur officielle J+1 (override)
- Nombre de jours restants par couleur (bleu / blanc / rouge)

---

## Indicateur clé : le Z-score

Le **Z-score** représente la tension du système électrique français  
par rapport à une référence annuelle.

| Z-score | Interprétation |
|-------|----------------|
| Z < 0.6 | Système confortable |
| 0.6 ≤ Z < 1.0 | Tension modérée |
| 1.0 ≤ Z < 1.3 | Zone de vigilance |
| 1.3 ≤ Z < 1.6 | Forte tension |
| Z ≥ 1.6 | Tension critique |

Les transitions sont **progressives** :  
aucun seuil ne provoque de basculement brutal.

---

## Horizon de prévision et fiabilité

| Horizon | Données principales | Fiabilité |
|------|--------------------|----------|
| J+1 | RTE + couleur EDF officielle | Très forte |
| J+2 | RTE complètes | Forte |
| J+3 | RTE partiel / estimation | Moyenne |
| J+4 à J+6 | Carryover ENR | Faible |

À partir de **J+4**, les données ENR détaillées ne sont plus disponibles côté RTE.  
Le modèle conserve la **dernière valeur connue**, ce qui est explicitement signalé.

---

## Gestion du passage d’année

Le comportement dépend de la période :

- **Du 01/01 au 30/11**
  - appel RTE sans paramètres (comportement par défaut)

- **Du 01/12 au 31/12**
  - encadrement explicite :
    - start_date = 01/01 N
    - end_date = 01/01 N+1 (exclusive côté RTE)

- **Du 31/12 au 05/01**
  - encadrement maintenu pour éviter :
    - ruptures statistiques,
    - Z-score aberrant,
    - fallback intempestif

Ce mécanisme garantit une continuité correcte autour du Nouvel An.

---

## Règles structurelles Tempo

### Jours interdits / imposés
- **Dimanche** : bleu certain
- **Samedi** :
  - jamais rouge
  - plafonnement des extrêmes
  - renormalisation systématique
- Respect strict des jours restants par couleur

---

## Boost rouge (plein hiver)

Le boost rouge est **volontairement encadré**.

### Conditions :
- cœur d’hiver (décembre → février)
- jour ouvré (lundi → vendredi)
- rouge autorisé (jours restants)
- `1.05 ≤ Z ≤ 1.6`

### Objectifs :
- éviter les faux positifs rouge
- renforcer le rouge uniquement en tension réelle
- laisser vivre les duels blanc / rouge en zone limite

---

## Prélèvement sur blanc et bleu

Quand le rouge monte, il prélève une partie des probabilités non rouges.

- Ancienne valeur maximale : `0.55`
- Valeur actuelle : `0.45`

Principes :
- prélèvement **proportionnel**
- aucun plancher fixe
- aucun écrasement arbitraire

Cela évite les rouges artificiellement dominants.

---

## Désactivation de l’aide au bleu

En cœur d’hiver :
- dès que `Z ≥ 0.62`
- toute aide structurelle au bleu est désactivée

Objectif :
- éviter des scénarios incohérents du type :
  > Bleu dominant avec Z élevé en janvier

---

## Score de confiance

Le score (1 à 5) dépend de :
- l’écart entre les probabilités dominantes
- la distance temporelle (proche / moyen / lointain)
- la stabilité des données ENR
- la cohérence avec les règles Tempo

### Dégradation volontaire
À partir de **J+4** :
- la confiance est abaissée d’un cran
- le commentaire précise la fiabilité réduite
- aucune certitude artificielle n’est affichée

---

## Commentaires explicatifs

Les commentaires sont générés dynamiquement selon :
- duel bleu/blanc
- risque rouge en embuscade
- domination claire
- horizon temporel

Exemples :
- `Jour proche, rouge nettement devant.`
- `Duel bleu/blanc très serré, prévision sensible.`
- `Jour lointain, beaucoup d’incertitude.`

---

## Limitations connues

### 1. Données ENR absentes à long terme
À partir de J+4 :
- production ENR extrapolée
- fiabilité réduite
- confiance volontairement abaissée

---

### 2. Zones blanc / rouge ou bleu / blanc ambiguës
Quand :
- Z autour de la zone de bascule entre deux couleurs
- stocks encore confortables
- météo froide mais stable

RTE peut arbitrer différemment du modèle qui marquera une incertitude.

---

### 3. Décisions RTE non modélisables
Le modèle ne connaît pas :
- contraintes internes EDF/RTE
- arbitrages commerciaux
- décisions tardives

Ces choix sont **par nature imprévisibles**.
**Il est donc impossible de prédire une journée rouge si RTE vide les stocks** (obligatoire contractuellement entre le 01/11 et le 31/03) **mais que la tension du réseau est faible** (exemple en mars 2024 où la tension sur le réseau était faible et donc normalement bleue, mais pour vider les stocks, cette journée fut rouge). La probabilité existera, mais elle ne sera pas en tête, sans que cela ne soit un dysfonctionnement.

**Ceci est valable également pour une journée blanche**. Toujours pour une question de gestion des stocks, il est possible que d’après les indicateurs de tension du réseau, tout signale rouge mais que L’algorithme de RTE en décide autrement. Malheureusement dans la documentation disponible sur le net, rien n’est précisé sur comment sont gérés ces stocks. Uniquement sur des seuils de déclenchement rouge/blanc, et dans ce cas, l'erreur est quasi-inévitable.

---

### 4. Aucun apprentissage automatique
Choix volontaire :
- modèle déterministe
- explicable
- maîtrisable
- ajustable manuellement

### 5. Abscence de données
RTE publie les données du lendemain dans la nuit. Ainsi les données "J+2" sont manquantes entre 00h30 et 02h00 du matin environs. Il ne s'agit pas d'un bug mais d'une limitation technique liée à la publication des données sur l'API. Les statistiques sont à nouveaux disponibles à la prochaine mise à jour, aux alentours de 3H du matin.

---

## Philosophie du projet

Ce projet privilégie :
- la cohérence de données brutes
- l’explicabilité
- la stabilité
- la fiabilité, autant que possible

Il ne cherche pas à “deviner” ce que fera RTE,  mais à **comprendre le système électrique** et ses contraintes pour déterminer des probabilités.

---


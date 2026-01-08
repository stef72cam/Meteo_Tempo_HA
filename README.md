# Météo Tempo (Home Assistant) — Prévision J+1 à J+6 via API RTE

Ce projet fournit une “météo Tempo” (bleu / blanc / rouge) pour **J+1 à J+6**, exploitable dans **Home Assistant**, en s’appuyant sur les **API officielles RTE** (prévisions de consommation / production et prévisions annuelles).

 ⚠️ Il s’agit d’une estimation probabiliste, pas d’une certitude. Comme pour la météo, le modèle évalue des risques (probabilités),
      et non une vérité absolue. Ce projet ne fournit donc **pas une prédiction certaine** des jours Tempo (bleu / blanc / rouge).
  -  Ce n’est pas la vérité officielle EDF/RTE : c’est une **prévision**, avec des erreurs possibles basée sur des indicateurs publics (RTE) et des règles inspirées de l’historique EDF.
  -  La couleur **officielle** n’est connue que lorsque RTE la publie, la veille pour le lendemain.

> L'objectif est d'aider les potentiels utilisateurs de ce modèle à anticiper les postes de consommation pour les journées où les tarifs sont les plus élevés.

> Les prévisions peuvent changer en cours de journée, celles-ci sont **mises à jour en permanence !**

> Ce projet est purement personnel et est partagé à titre d'exemple pour ceux qui veulent simplement l'essayer, le personnaliser ou le modifier à leur guise. 
> Il n'a pour le moment aucune vocation à être maintenu ou déployé à grande échelle.


---

<img width="1797" height="872" alt="image" src="https://github.com/user-attachments/assets/3c0b8a7a-c6ec-4f31-8abe-d85d7b465884" />



## Fonctionnalités

- Prévision **Tempo J+1 → J+6** : `bleu`, `blanc`, `rouge`
- Probabilités associées : `p_bleu`, `p_blanc`, `p_rouge`
- Indice de “tension réseau” : `z` (calibré sur une fenêtre glissante d’un an)
- Score de confiance (1 → 5) + libellé + commentaire d'aide à la compréhension
- Des pastilles de couleur indiquant la tension sur le réseau
- Respect des règles Tempo :
  - rouge interdit week-end / fériés
  - saisonnalité (rouge uniquement sur période autorisée)
  - prise en compte du stock de jours restants (rouge/blanc/bleu)
- Mode “override” : possibilité d’imposer la couleur réelle J+1 (quand elle est connue), tout en gardant la cohérence des stocks

---

## Pré-requis

- Home Assistant OS / Supervised / Container
- `python3` disponible dans l’environnement HA
- Avoir un compte DATA RTE : data.rte-france.com
- Créer un projet dans "Mes applications" et associer les API : Tempo Like Supply Contract / Consumption / Generation Forecast
- Accès API RTE (Client ID / Client Secret)
- Avoir installé et configuré l'intégration "RTE Tempo" depuis HACS et ne pas modifier le nom des entités par défaut (surtout pour le nombre de jours restants)
- Des capteurs HA fournissant le stock Tempo (rouge/blanc/bleu restants) et éventuellement la couleur officielle J+1 --> fournis directement dans "configuration.yaml" dans les exemples
- Si utilisation tel quel avec affichage comme dans l'exemple, il faut avoir "bar-card" d'installé via le dépot HACS + Button-card

---

## Installation

1) Copier le script à la racine de Home Assistant :
- `config/tempo_prevision_simple.py`
  - Penser à renseigner les champs **Client_ID** et **Secret_ID**

2) Ajouter un **sensor command_line** dans le fichier de configuration de Home Assistant qui exécute le script et expose le JSON en attributs.


3) Ajouter les **template sensors** pour exposer facilement :
- la couleur J+1..J+6
- les probas
- le niveau de tension (Z + palette vert/jaune/orange/rouge/violet)
- le score et commentaire de confiance

   **Exemple complet à copier coller et prêt à être utilisé dans votre fichier configuration.yaml** : [`examples/configuration.yaml`](examples/configuration.yaml)
    - Attention: si les sections templates et command_line existent déjà dans votre fichier, il suffit de placer les sensors en dessous de ceux existants, sans faire de doublon de section.
    - Penser à redémarrer Home Assistant après avoir ajouté le fichier tempo_prevision_simple.py et créé tous les sensors !

 
4) Ajouter une automatisation pour effectuer la mise à jour régulière des valeurs
    - Possibilité de ne pas en faire mais d'ajouter à la place un scan_interval au capteur command_line tempo_prevision_simple

    - **Exemple d'automatisation prête à copier/coller et utilisée** [`examples/Maj_previsions`](examples/Maj_previsions) 
       - Effectue les mises à jour aux moments de la journée où RTE publie les données qui nous intéressent avec nouvelle tentative en cas de non réponse de l'API
         
6) Ajouter l'affichage dans son tableau de bord Home assistant

   Exemple complet : [`examples/Affichage_lovelace`](examples/Affichage_Lovelace)
---

## Capteurs Home Assistant (architecture)

### `sensor.tempo_prevision_simple` (command_line)
C’est **le capteur principal**.
- Il exécute le script python
- Il renvoie un JSON
- HA stocke ce JSON en **attributs** (`J+1`, …, `J+6`, `generated_at`, etc.)
- Son `state` est volontairement fixe (`ok`) : l’intérêt est dans les **attributs**

### Template sensors (extraction)
Les template sensors servent à :
- rendre les données exploitables dans Lovelace (bar-card, badges, automations)
- isoler des entités simples (`tempo_j1_couleur`, `tempo_j2_p_rouge`, etc.)
- éviter de manipuler des structures JSON complexes partout

---

## Format de sortie JSON (résumé)

Chaque `J+N` contient notamment :

- `date` : la date cible
- `couleur` : couleur retenue par le modèle
- `modele` : `modele_interne` ou `override_RTE`
- `c_mw` : consommation prévue (MW)
- `gen_mw` : production ENR estimée (MW)
- `gen_source` : source ENR (`RTE`, `estime_ratio_J+1`, `carryover_last_known`)
- `c_net_mw` : consommation nette = conso - ENR
- `z` : indice tension (calculé via prévisions annuelles RTE sur 365 jours glissants)
- `p_bleu / p_blanc / p_rouge` : probabilités
- `confidence_score / label / comment` : confiance (1..5)
- `z_debug` : bloc debug optionnel (diagnostic du calcul de Z)

 Détails de la sortie Json : [`docs/sortie_json.md`](docs/sortie_json.md)
 
 Détails du fonctionnement : [`docs/fonctionnement.md`](docs/fonctionnement.md)

---

## Disclaimer

- Le modèle ne remplace pas les annonces officielles Tempo.
- Les prévisions reposent sur des données externes (RTE) qui peuvent évoluer.
- Les résultats sont fournis “as-is”, sans garantie.
- Le code peut- être mis à jour pour fiabiliser les probabilités obtenues par le modèle
   - Ces fiabilisations reposent sur un ajustement du code pour affiner les réponses et limiter les grosses erreurs.
   - Ces ajustements s'effectuent à l'aide de backtess et des observations entre les prévisions du modèle et le choix réel de RTE.
      - Principe du Brier Score
- Le projet est personnel et développé par passion pour la domotique.
- La logique fonctionnelle et les choix de modélisation sont les miens.
- La création du code a été assisté par des outils d’IA, ce qui peut entraîner des choix non optimaux d’un point de vue purement informatique.
- Ce projet est partagé librement afin que d’autres puissent en bénéficier, sans prétention à l’exhaustivité ni à l’infaillibilité.

---

## Sources

Pour mener ce projet au stade fonctionnel tel qu'il est aujourd'hui, j'ai réalisé différents backtests sur les 10 années précédentes.
Les sources sur lesquelles je me suis basé pour faire mes tests et faire fonctionner ce modèle sont : 

 - https://www.rte-france.com/donnees-publications/eco2mix-donnees-temps-reel/synthese-donnees
 - https://www.rte-france.com/donnees-publications/eco2mix-donnees-temps-reel/telecharger-indicateurs
 - https://www.services-rte.com/files/live/sites/services-rte/files/pdf/20160106_Methode_de_choix_des_jours_Tempo.pdf
 - https://data.rte-france.com/catalog/-/api/doc/user-guide/Generation+Forecast/3.0
 - https://data.rte-france.com/catalog/-/api/doc/user-guide/Consumption/1.2
 -  L'observation réelle du modèle VS journée seléctionnée par RTE

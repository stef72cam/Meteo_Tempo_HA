# Météo Tempo (Home Assistant) — Prévision J+1 à J+6 via API RTE

Ce projet fournit une “météo Tempo” (bleu / blanc / rouge) pour **J+1 à J+6**, exploitable dans **Home Assistant**, en s’appuyant sur les **API officielles RTE** (prévisions de consommation / production et prévisions annuelles).

> ⚠️ Ce n’est pas la vérité officielle EDF : c’est une **prévision**, avec des erreurs possibles, comme pour la météo.  
> La couleur **officielle** n’est connue que lorsque RTE la publie.
> 
> Ce projet est purement personnel et est partagé à titre d'exemple pour ceux qui veulent simplement l'essayer, le personnaliser ou le modifier à leur guise. 
> Il n'a pas vocation à être maintenu ou déployé à grande échelle.


---

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
- Passionné par la domotique, mais n'ayant aucune connaissance en codage, la majeure partie de ce script a été réalisé à l'aide de l'IA.
- La logique et le fonctionnement voulu sont les miens, la rédaction du code absolument pas. Les spécialistes pourront y voir des abbérations et je m'en excuse d'avance.
- Pour rappel, c'est un projet purement personnel que je partage pour que je ne sois pas le seul à profiter.

---

## Sources

Pour mener ce projet au stade fonctionnel avec la fiabilité tel qu'elle est aujourd'hui (≈92%), j'ai réalisé différents backtests sur les 10 années précédentes.
Les sources sur lesquelles je me suis basé pour faire mes tests et faire fonctionner ce modèle sont : 

 - https://www.rte-france.com/donnees-publications/eco2mix-donnees-temps-reel/synthese-donnees
 - https://www.rte-france.com/donnees-publications/eco2mix-donnees-temps-reel/telecharger-indicateurs
 - https://www.services-rte.com/files/live/sites/services-rte/files/pdf/20160106_Methode_de_choix_des_jours_Tempo.pdf
 - https://data.rte-france.com/catalog/-/api/doc/user-guide/Generation+Forecast/3.0
 - https://data.rte-france.com/catalog/-/api/doc/user-guide/Consumption/1.2

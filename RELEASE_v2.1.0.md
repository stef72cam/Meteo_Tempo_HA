# 🚀 Météo Tempo V2.1.0 — Nouveau mécanisme PP et prévisions ENR

Cette version marque une refonte majeure du projet pour tenir compte de la nouvelle méthode de sélection des jours rouges Tempo appliquée à partir de novembre 2026.

### 🔴 Jours rouges : place à Bellman

- Exploitation des matrices officielles RTE J-1 et J-2 et des signaux PP.
- Calcul de la pointe résiduelle horaire et gestion probabiliste des stocks : plusieurs scénarios sont désormais envisagés pour les journées suivantes.

### 🌦️ Prévision de production renouvelable jusqu'à J+6

Lorsque les données nationales RTE manquent, le moteur estime l'éolien terrestre, offshore et le solaire via Open-Meteo (26 zones). Les données RTE restent prioritaires ; les profils RTE antérieurs servent de dernier recours.

### 🏠 Home Assistant

Nouvelle carte avec jauges de seuil PP, barres de probabilités et commentaires contextuels. Le Z reste interne au modèle blanc. Les données indisponibles ne sont plus assimilées à 0 %.

### ⚠️ Migration nécessaire

Ajouter l'API Signal V2 dans DATA RTE, télécharger les deux matrices de la saison en cours, remplacer le script et adapter les exemples de configuration et de carte. Voir [le guide de migration](https://github.com/stef72cam/Meteo_Tempo_HA/blob/main/docs/migration_v2.md).

Cette version a été essayée sur Home Assistant avec les API réelles. Les probabilités et l'estimation météo ne sont pas encore calibrées ; la couleur officielle Tempo reste la seule référence tarifaire. Les retours sont les bienvenus !

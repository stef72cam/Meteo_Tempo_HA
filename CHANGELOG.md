# Historique des versions

## v2.1.0 — 27 novembre 2026

- Refonte du moteur rouge : matrices officielles Bellman J-1/J-2, signaux PP, suivi distinct des stocks PP et Tempo et propagation des scénarios.
- Calcul horaire de la pointe résiduelle ; moyenne 06h–06h pour le modèle blanc et secours sur journée civile.
- Moteur ENR météorologique régional (20 points terrestres, 6 maritimes), complétant les données nationales RTE ; extrapolation de profils RTE en dernier recours.
- Réconciliation des stocks avec le calendrier ; traitement des signaux PP tardifs et données partielles.
- Nouvelle carte Lovelace : commentaires contextuels, probabilités, confiance, source ENR et mini-jauges PP ; retrait du Z de l'affichage.
- Champs de diagnostics et de qualité supplémentaires ; probabilités absentes représentées par null.
- Documentation et exemples adaptés à la V2.

Validation : rapport de développement de 42 tests automatisés réussis ; intégration du moteur vérifiée sur Home Assistant avec les API réelles. Les tests ne démontrent pas la calibration statistique. Le timeout hebdomadaire observé rappelle que le cache et la reprise automatique restent à développer.

## Versions précédentes

Voir les [releases GitHub](https://github.com/stef72cam/Meteo_Tempo_HA/releases) pour l'historique V1, dont V1.2.2 et son mécanisme Terminal Red Pressure. Les versions V2.0.0 et V2.0.1 ont servi d'étapes de développement avant cette mise à jour.

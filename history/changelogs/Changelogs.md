----

## [`V1.0.1`](https://github.com/stef72cam/Meteo_Tempo_HA/releases/tag/v1.01)



Update corrective : 
- Simplification de la fonction wrappers.
- Modification de la logique des journées rouges 
- Prise en compte de façon plus fiable des contraintes calendaires rouges (nombre de jours restant vs restant à poser par rapport à date contrainte).
--> Limitation technique du modèle, possibles mises à jour à venir. 

Correction d'un bug où les samedis étaient systématiquement à tort en duels bleu/blanc. 
Correction d'un fonctionnement du modèle où le rouge était trop appuyé avec Z pourtant pas à l'extrême.
Rouge arrivait trop tôt notamment quand Z est en zone intermédiaire (entre 1.10-1.20 environs) alors que blanc reste un scénario crédible.
Le rouge était renforcé dès z >= s_r_adj - 0.2
Effet trop large → rouge dominant trop tôt

**--> Suppression complète de cette logique**

**Nouvelle Logique**

if allowed_red and z is not None:
start = s_r_adj - 0.07
if z >= start and z < s_r_adj and score_r < score_w:
t = (z - start) / max(1e-6, (s_r_adj - start))
score_r = max(score_r, score_w * (0.40 + 0.40 * t))

- Fenêtre réduite : 0.07 (au lieu de ~0.20)
- Progressivité linéaire
- Rouge plafonné entre 40 % → 80 % du blanc
- Rouge ne dépasse jamais brutalement le blanc

## **Ajouts de nouvelles fonctions**
  **Détails : https://github.com/stef72cam/Meteo_Tempo_HA/issues/1#issuecomment-3719292837** 
  
--------
## V1.0.0
Première version. 

# Parcours (b) — YouTube 20 min en multicam, en vraie fenêtre

**Objectif** : trois caméras de 20 min, synchronisation par le son, changements d'angle, étalonnage, chapitres, export long. Les médias du banc sont **synthétiques**
(motif de test, même son de salle, claps communs). L'ADR-0011 demande aussi de **vraies caméras** : refaire les étapes M1 à M12 avec **vos propres rushes de 3 caméras**
(au moins 20 min, claps au début) et noter les écarts avec le jeu synthétique. Préparer : `python -m tools.recette run --journeys youtube --scale full` (≈ 15 à 25 min ; génère
`~/Movies/Kut-Studio-Recette/youtube-full/media/cam_a.mp4`, `cam_b.mp4`, `cam_c.mp4`, `narration.wav`, `nappe.wav`). Étapes A, B avant, Z après ([etapes-communes.md](etapes-communes.md)).

Vérité fabriquée : décalages des départs `cam_a` = 0, `cam_b` = **2,4 s**, `cam_c` = **5,12 s** ; claps à 8 s, 600 s et 1 190 s de plateau.

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| M1 | Importer `cam_a`, `cam_b`, `cam_c` (≈ 650 Mo chacune) | Import sans blocage ; miniatures et ondes arrivent en arrière-plan ; l'interface reste réactive | |
| M2 | Sélectionner les trois → **Créer une source Multicam** | Source « Plateau » à 3 angles | |
| M3 | Lancer la **synchronisation par le son** | Décalages retrouvés : `cam_b` = **2,4 s**, `cam_c` = **5,12 s** (écart < 40 ms), statut « excellent » ; durée de l'analyse notée ______ s | |
| M4 | Ouvrir le **moniteur Multicam** (grille des angles) pendant la lecture | Les trois angles défilent ensemble, **sans décalage visible** ; fluidité notée (images perdues) | |
| M5 | **Basculer en direct** d'un angle à l'autre pendant la lecture, une quinzaine de fois | Chaque bascule coupe le segment, aucune image noire, pas de rattrapage audio audible | |
| M6 | Poser la narration `narration.wav` (rôle **voix**) et la nappe (rôle **musique**) ; ducking ; compresseur sur la narration | Nappe sous la voix ; pas de saturation | |
| M7 | Politique audio de la source : angle « Large » seul (**fixe**) puis « mix » | Le son change comme attendu, sans claquement à la bascule | |
| M8 | **Étalonnage** : calque d'effets au-dessus de tout le montage (exposition +0,15, contraste, saturation, température) ; **page Couleur** : voir l'étalonnage animé du parcours c | Le moniteur reflète le réglage ; pas de pompage | |
| M9 | Bandeau « Lower third » à 4 s ; 10 repères de chapitres | Bandeau lisible, repères posés | |
| M10 | **Lecture** continue de 5 min au milieu du montage (angles qui changent) | Pas de gel, pas d'accumulation d'images perdues ; mémoire stable (Moniteur d'activité : RSS de l'application) | |
| M11 | **Exporter** en 1080p (préréglage YouTube) ; chronométrer ; **surveiller la mémoire et le disque libre** (Moniteur d'activité, onglets Mémoire et Disque) ; **annuler l'export** si la pression mémoire passe au rouge ou si le disque libre descend sous 5 Go (voir D-002 : le swap peut remplir le disque) | Fichier ≈ 20 min ; **durée d'export ______ min** (le banc donne la référence CPU) ; mémoire stable ; ventilateur / chaleur notés | |
| M12 | Ouvrir le MP4 dans QuickTime, sauter à 5 points au hasard (début, chaque chapitre, fin) | Image et son synchrones ; aucun noir ; claps (8 s, 600 s) alignés image / son | |
| M13 | Enregistrer, fermer, rouvrir | Projet à l'identique, angles en ligne | |

À noter pour la phase 2 : durée d'export de 20 min sur chaque Mac, ratio avec le temps réel, pic de mémoire.

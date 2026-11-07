# Parcours (c) — motion design avec 3D, en vraie fenêtre

**Objectif** : voir **à l'écran** ce que les audits ont jugé « jamais vu » : étalonnage animé, moniteur 3D, calques animés. Préparer :
`python -m tools.recette run --journeys motion3d --scale full` (médias : `fond.mp4`, `nappe.wav`, `titre.kut3d`). Étapes A, B avant, Z après ([etapes-communes.md](etapes-communes.md)).
Ouvrir `project.kut` (copié ailleurs) pour partir du montage scripté, **ou** le refaire à la main pour juger l'ergonomie : les deux se valent, noter lequel.

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| D1 | Poser `fond.mp4` (V1) et `nappe.wav` (A1) | Fond de motifs de test, 30 s | |
| D2 | **Étalonnage animé** : page **Couleur**, images clés (losanges) sur exposition (−0,3 → +0,3 → 0), température (−30 → +40 → 0), saturation, teinte | Les losanges apparaissent dans la timeline ; **le moniteur change de couleur en lecture** ; capture au début, au milieu, à la fin | |
| D3 | Comparer la lecture du moniteur et l'image après arrêt, à 3 instants | Mêmes couleurs (le moniteur est « fidèle d'abord ») ; écart → fiche | |
| D4 | **Effet animé** : vignette (intensité 0,1 → 0,7 → 0,2) | Le bord s'assombrit progressivement | |
| D5 | Titre « MOTION DESIGN » : révélation (`graphic.reveal`), position, espacement des lettres animés | Le texte apparaît lettre par lettre en glissant ; courbe ease-out | |
| D6 | Sous-titre à animation **machine à écrire** | Frappe régulière | |
| D7 | Trois **formes** (ellipses) en orbite, **groupées**, rotation du groupe | Rotation continue, formes qui respirent (échelle) | |
| D8 | **Classement animé** (5 lignes) | Barres qui montent en cascade, valeurs lisibles | |
| D9 | **Scène 3D** : ajouter la scène `titre.kut3d` (calque « Scène 3D ») | Texte 3D « KUT » doré extrudé, cube, sphère, tore, cône, cylindre éclairés ; fond transparent sur la vidéo | |
| D10 | **Moniteur 3D** : ouvrir la scène (double-clic), tourner la caméra, picking d'un objet | Rotation fluide ; l'objet cliqué est sélectionné ; la scène 3D se met à jour dans le montage | |
| D11 | Images clés sur le calque 3D : échelle 0,5 → 1, opacité 0 → 1, rotation −8° → 8° | Entrée en fondu avec grossissement | |
| D12 | **Lecture** du montage entier | Fluide (30 i/s) ; noter les images perdues ; la 3D ne fige pas l'interface | |
| D13 | **Exporter** en 1080p ; chronométrer | MP4 30 s ; **durée d'export ______ s** (référence du banc ______ s) ; 3D, texte et étalonnage animés **présents** | |
| D14 | Contact sheet de la sortie (5 images) comparée au moniteur | Mêmes rendus ; sinon fiche | |
| D15 | Enregistrer, fermer, rouvrir | Projet à l'identique ; la scène 3D se recharge (`.kut` v22 ou plus) | |

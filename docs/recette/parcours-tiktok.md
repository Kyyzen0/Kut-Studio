# Parcours (a) — TikTok 60 s, en vraie fenêtre

**Objectif** : un format court vertical de bout en bout : import, coupe sur le rythme, sous-titres animés, multi-format, export 9:16. À faire sur **le M1 Air
puis le M4 mini**. Préparer : `python -m tools.recette run --journeys tiktok --scale full` (génère les médias dans `~/Movies/Kut-Studio-Recette/tiktok-full/media/` et
mesure la version scriptée) ; noter les durées du rapport comme **point de comparaison**. Étapes d'installation et de mise à jour : [etapes-communes.md](etapes-communes.md)
(A, B avant ; Z après).

Médias : `v1.mp4`, `v2.mp4`, `v3.mp4` (verticaux 1080x1920, 30 i/s), `h1.mp4`, `h2.mp4` (horizontaux 1920x1080, **25 i/s**), `musique.wav` (120 BPM),
`voix.wav` (voix de test, syllabes sans texte). Chaque plan a une bande de couleur en haut et un compteur d'images (motif de test) : on voit à l'œil s'il avance.

| # | Étape | Résultat attendu | Observé / écart / capture |
| --- | --- | --- | --- |
| T1 | Nouveau projet vertical 1080x1920 à 30 i/s ; **importer** les 7 médias (glisser-déposer, puis **Importer**) | Miniatures et ondes sonores apparaissent ; durées exactes (≈ 14 s, 16 s, 12 s…) ; aucune erreur | |
| T2 | Poser `v1` puis `v2`, `h1`, `v3`, `h2` bout à bout sur V1 | Chaque plan se pose sans trou ; `h1` et `h2` (horizontaux) apparaissent avec des bandes ou se recadrent selon « remplir » | |
| T3 | Régler `h1` et `h2` sur **Remplir** | L'image remplit le cadre vertical, recadrée au centre | |
| T4 | Poser `musique.wav` sur A1, `voix.wav` sur une seconde piste audio ; rôles **musique** / **voix** ; activer le **ducking** « social punchy » | La musique baisse sous la voix ; le son reste net (écoute au casque) | |
| T5 | **Détecter le tempo** de `musique.wav` (outil de rythme) | ≈ **120 BPM** (±1 %), premier temps proche de 0 ; grille rythmique posée | |
| T6 | **Couper sur les temps** (un temps sur quatre) `v2` et `v3` | Coupes toutes les 2 s, **sans trou ni image noire** entre les morceaux | |
| T7 | Ajouter une transition `crossfade` (0,4 s) entre `v1` et `v2` et `wipe_left` (0,5 s) entre `h1` et `v3` | Les transitions se voient à la lecture. **Observer** : après l'ajout, le clip entrant a-t-il reculé et laissé un trou derrière lui ? (voir D-001 dans [defauts-connus.md](defauts-connus.md)) | |
| T8 | Zoom d'impact sur `v1` (≈ 1 s), secousse sur `h1`, flash blanc au début de `h1` | Effets visibles, sans saccade, sans image noire | |
| T9 | **Sous-titres** : si la **transcription** (whisper.cpp) est installée, la lancer sur `voix.wav` ; sinon saisir 6 à 8 lignes à la main | Lignes datées, ≤ 32 caractères ; avec la voix de test, **le texte reconnu peut être vide ou fantaisiste : sans objet** (seul le fonctionnement compte) | |
| T10 | Mettre les sous-titres en **karaoké** (« Sous-titre karaoké », animation `karaoke`) et un titre « Titre TikTok » animé `pop_in` | Le surlignage mot à mot suit l'horloge ; le texte reste lisible dans les **zones de sécurité** verticales | |
| T11 | **Étalonnage animé** (voir le parcours c pour le détail) : au moins une image clé d'exposition sur `v1` (0 → +0,3 → 0) | Courbe visible, lecture sans accroc, **le moniteur suit l'export** | |
| T12 | **Lecture** du montage entier au moniteur, 2 fois (début, puis milieu) ; noter la fluidité et les images perdues (diagnostic matériel) | Lecture fluide à 30 i/s, pas de saut visible ; son synchrone avec l'image (claquement de la coupe) | |
| T13 | **Multi-format** : « Exporter en plusieurs formats… » → ajouter 1:1 | Une séquence « version carrée » est créée ; le cadrage se recompose | |
| T14 | **Exporter** en 9:16 (préréglage TikTok) ; chronométrer ; ouvrir le MP4 dans QuickTime | Fichier 1080x1920, ≈ 59 s, son présent, lisible ; **durée d'export** notée pour comparaison avec le banc | |
| T15 | Comparer le MP4 au moniteur sur 5 images prises au hasard (titre, sous-titre, coupe, transition, flash) | Aspect identique (moniteur ≈ export) ; écarts → fiche | |
| T16 | Enregistrer, fermer, rouvrir | Le projet se rouvre à l'identique ; aucun avertissement | |

Valeurs de comparaison du banc (cette machine) : à copier depuis `rapport.md` — durée d'export 9:16 ______ s · 1:1 ______ s · pic de mémoire ______ Mo.

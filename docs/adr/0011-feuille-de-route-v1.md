# ADR-0011 : la feuille de route vers la v1 — des portes de sortie, pas des dates

**Statut :** Proposé le 2026-10-21. Aucune phase n'est lancée : l'utilisateur valide d'abord le cadre et répond aux sept décisions de « Décisions à prendre par l'utilisateur ».
**Date :** 2026-10-21
**Décideurs :** mainteneur de Kut-Studio
**S'appuie sur :** [ADR-0001](0001-moteur-unifie-gpu-adaptatif.md) (moteur GPU adaptatif), [ADR-0003](0003-native-engine-language-and-boundary.md) et [ADR-0004](0004-native-engine-language-cpp-vs-rust.md) (moteur natif), [ADR-0005](0005-mode-script-frontiere-de-securite.md) (scripts), [ADR-0006](0006-composition-visuelle-progressive.md) (composition), [ADR-0007](0007-modele-d-intentions.md) (intentions), [ADR-0008](0008-animation-etalonnage-et-effets.md) (animation), [ADR-0009](0009-kut3d-moteur-3d-scriptable.md) et [ADR-0010](0010-page-3d-edition-scene.md) (Kut-3D).

Demande de l'utilisateur (2026-10-21) : un plan jusqu'à la **v1**, avec **« un énorme bond en avant »**. Le cadre qu'il a fixé, reproduit tel quel :

1. La v1 est **« une plateforme qui peut tout faire »** pour **trois publics à égalité** : le créateur de formats courts (TikTok, Reels, Shorts), le créateur YouTube / de montage long et classique, le motion designer (3D comprise).
2. **Aucune date contrainte.** Les dates sont remplacées par des **portes de sortie mesurables** : une phase est finie quand ses critères sont atteints, pas quand un calendrier le dit.
3. **Ressources :** abonnements Claude Max ×5 (un superviseur et des développeurs en parallèle, **jusqu'à 4 chantiers à la fois**) ; Codex Plus comme relecteur indépendant de chaque PR (déjà branché). MiniMax Go et Devstral local via Hermes : **essais reportés** à la demande de l'utilisateur, **non planifiés ici** ; option ultérieure à évaluer sur des tâches vérifiables par test (§ « Parallélisation »).
4. Il veut **un énorme bond en avant**.

Cet ADR ne décide aucune conception technique : chaque phase aura son ADR ou sa PR de conception quand elle commencera. Il ordonne le travail, fixe ce qui prouve qu'une phase est finie, et dit ce que la v1 ne contiendra pas. **Aucune promesse de date.**

## Comment les faits ont été établis

Le matériau vient de trois audits en lecture seule du 2026-10-21 sur `origin/main` (32a1e6a). **Chaque affirmation reprise ici a été revérifiée dans le dépôt** (grep du code et des docs) avant d'être écrite. Sept ont dû être corrigées ou nuancées ; elles sont signalées **[corrigé]** ou **[INCERTAIN]** et résumées en « Corrections apportées aux audits ». Rien n'a été exécuté à l'écran : tout ce qui touche à un comportement réel sur une machine reste à prouver en phase 1.

La version publiée est la 0.8.0 (`core/app_version.py`) ; l'animation de l'étalonnage et des effets (ADR-0008) est terminée sous `[Unreleased]`, destinée à la 0.9.0.

---

## Résumé de la décision

1. **Sept phases ordonnées par portes de sortie**, de 0 à 6, plus un **backlog d'intelligence** qui n'entre dans le plan qu'après la phase 2. La phase 1 (recette sur de vraies machines) passe **avant** tout nouveau socle.
2. **Priorité : un manque qui touche les trois publics passe avant un manque qui n'en touche qu'un** (moteur, interopérabilité, export, fiabilité d'abord ; page 3D et moteur d'intentions ensuite).
3. **Jamais de fonction nouvelle empilée sur un socle non validé en vraie fenêtre** : étalonnage animé, moniteur 3D, multicam avec vraies caméras sont aujourd'hui « jamais vus à l'écran » (ADR-0008, ADR-0010).
4. **Parallélisme limité à 4 chantiers** et seulement sur des fichiers disjoints (§ « Parallélisation ») ; deux chantiers ne touchent jamais en même temps le même registre de dette ni la même ligne de `CHANGELOG.md`.
5. **Le bond en avant se mesure** (§ « Comment on mesure le bond en avant ») : couverture des attentes par public, temps d'export et de lecture, plantages par heure de session. Un chiffre mesuré sur médias réels, jamais un seuil de durée dans un test unitaire.
6. **Enveloppe totale estimée : environ 130 à 190 PR de ≈ 500 lignes** (tailles S / M / L ci-dessous), à lire comme un ordre de grandeur, pas comme un engagement.

---

## Contexte et vision

Kut-Studio est aujourd'hui un montage non destructif complet avec des points forts rares : **multicam** (`Sequence.multicam`, `docs/multicam.md`), **étalonnage riche** (roues, courbes, nœuds, scopes, LUT `.cube`, 4:2:0 fidèle ; `docs/color.md`), **chaîne sociale complète** (formats, zones de sécurité, rythme, sous-titres animés, multi-format ; `docs/social-video.md`), **mode script** (ADR-0005, `docs/scripting.md`), **Kut-3D** (ADR-0009) et l'**animation des effets et de l'étalonnage** (ADR-0008). Ces points forts sont ce qu'il faut **protéger** : aucune phase ne les dégrade (leurs tests de parité aperçu / export restent des portes de toutes les phases).

La v1 est « une plateforme qui peut tout faire ». Les audits montrent que ce qui manque tient en huit familles, du plus transversal au plus spécialisé :

| Famille | Ce qui manque (état vérifié) | Publics |
| --- | --- | --- |
| **Fiabilité** | Aucune recette sur machines réelles (Windows / Linux GPU, multicam avec vraies caméras, étalonnage animé et moniteur 3D « jamais vus à l'écran », sessions longues). Paquets Windows **non signés** (`docs/updates.md`, section signatures), Linux sans vérification. | 3 |
| **Moteur / performance** | Un aperçu fidèle attend ses segments (ADR-0001 : 5,5 à 6,5 s de calcul pour un segment de 2 s sur le montage de 31 s en 1080×1920) ; mograph = Qt → PNG → ffconcat → FFmpeg (`docs/motion-graphics.md`, « Performances ») ; aperçu GPU partiel et « matériel non testé ici » hors macOS (`docs/gpu-preview.md`, section limites) ; un `ClipWidget` par clip (`ui/timeline_panel.py:46,183`) ; historique en copie profonde (`core/edit_history.py:14`, `MAX_HISTORY = 100`). | 3 |
| **Interopérabilité** | Aucun import / export XML, EDL, FCPXML, OTIO, AAF ; aucun import SVG, Lottie, After Effects (aucune occurrence dans `core/` ni `ui/`). | 3 |
| **Livraison** | H.264 / HEVC en 8 bits (`OUTPUT_PIXEL_FORMAT = "yuv420p"`, `core/export_engine.py:106`) ; 10 bits seulement en ProRes 422 HQ (`PRORES_PIXEL_FORMAT = "yuv422p10le"`, ligne 113) ; pas de HDR ni de tone mapping ; pas d'alpha à l'export de la timeline (le ProRes 4444 n'existe que pour les plans de Kut-3D, `docs/kut3d-film.md`) ; pas de séquence PNG, GIF, AV1 ; pas de chapitres MP4 (la catégorie de repère `chapter` existe, `core/project_model.py:695`, mais rien ne l'écrit dans le fichier) ; loudness à cible fixe −14 LUFS (`core/loudness.py:27`) ; pas de de-esser. | 3 |
| **Motion design** | Couleurs de calques non animables (`docs/motion-graphics.md:457`) ; pas d'animation caractère par caractère à sélecteur (**[corrigé]** des révélations mot / lettre existent) ; pas de texte sur tracé, de trim paths, de mattes de piste (alpha / luma), d'expressions utilisateur, de caméra 2.5D ; éditeur de courbes une propriété à la fois (`docs/animation.md:443`) ; parentage : un clip vidéo peut être parent mais jamais enfant. | motion, 3D |
| **Page 3D** | Document éditable livré (lot 0 d'ADR-0010), aucune page ; pas d'ombres, d'IBL ni de mipmaps ; caméra statique ; animation d'objets de scène question ouverte. | motion |
| **Contenus** | Aucun sticker, musique libre, LUT livrée, transition de créateur (glitch, zoom, whip), « enregistrer mon projet comme modèle », publication directe, éditeur de miniature (la catégorie de repère `cover` existe, pas d'éditeur), enregistrement écran / webcam (seul `core/audio_recorder.py` enregistre, de l'audio). | court, long |
| **Intelligence** | Pas de suivi automatique de sujet ou de visage, de détourage / rotoscopie, de TTS, d'IA générative, de moteur de particules ; transcription = whisper.cpp et modèle à installer par l'utilisateur ; sous-titres incrustés = libass requis. | court, long |

## Principes

1. **Une porte de sortie plutôt qu'une date.** Chaque phase se termine quand des critères **testables** sont atteints (un test, une mesure sur médias réels, un parcours passé sur une machine nommée). Une phase dont la porte n'est pas atteinte ne se clôt pas, elle se découpe.
2. **Trois publics, à égalité.** Un manque qui touche les trois publics (ex. interopérabilité, lecture fluide, signature des paquets) passe avant un manque qui n'en touche qu'un. Le tableau croisé de l'annexe porte cette règle.
3. **Ne jamais empiler sur un socle non validé en vraie fenêtre.** Une fonction qui s'appuie sur du code « jamais vu à l'écran » attend la recette de la phase 1. Les tests prouvent des octets et des identités, pas qu'un geste est faisable à la souris.
4. **Mesurer avant de décider, sur des médias réels.** Les audits récents (ADR-0001, ADR-0003) ont déjà montré que l'intuition se trompe sur les goulots (le `-ss` en sortie coûtait ×7,8). Une phase de performance commence par une mesure de référence figée.
5. **Compléter ce qui existe avant de réécrire** (ADR-0006) : un nouveau moteur n'est décidé que si les mesures d'un chemin existant montrent qu'il a atteint ses limites.
6. **Chaque PR garde les gardes existantes** : formes de ce dépôt (relecture Codex, `mypy`, cliquet de dette, audit i18n, `CHANGELOG.md`). Rien ici ne les assouplit.

## Les phases

Tailles : **S** = 1 à 3 PR, **M** = 4 à 10, **L** = 11 à 25 PR de ≈ 500 lignes. Ordre de grandeur, jamais un engagement.

### Phase 0 — Ce rapport et la dette de documentation

**Objectif :** une feuille de route validée et une documentation qui ne ment plus.
**Périmètre :** cet ADR ; corriger les passages périmés listés en « Dette de documentation » (une PR de doc, sans code).
**Portes de sortie :** (1) l'utilisateur a répondu aux sept décisions ; (2) les sept passages de la dette de doc sont corrigés et relus ; (3) `pytest -k "docs or changelog"` passe.
**Taille :** S (2 PR). **Dépendances :** aucune. **Risques :** faible ; une doc corrigée trop vite peut en laisser d'autres périmées, d'où la porte (2) qui recompte par grep.
**Hors v1 :** réécrire les ADR existants.

### Phase 1 — Recette sur de vraies machines

**Objectif :** savoir enfin ce qui marche, sur le matériel des utilisateurs, avant de construire dessus.
**Périmètre :**
- **Trois parcours scénarisés**, rejoués à chaque candidate de version : (a) un TikTok de 60 s (import, coupe sur le rythme, sous-titres animés, multi-format, export 9:16) ; (b) une vidéo YouTube de 20 min en multicam (vraies caméras, synchronisation audio, coupes, étalonnage, export long) ; (c) un motion design avec 3D (calques animés, scène Kut-3D, étalonnage animé, export).
- **Machines :** macOS Apple Silicon (référence), Windows avec GPU NVIDIA, et au moins un Windows sans GPU dédié ; Linux : **smoke test scripté** sur le paquet publié (démarrage puis export CPU d'un montage de 10 s, vérifié par `ffprobe`), sans recette GPU ni parcours complet (le public est macOS / Windows, § « Décisions »).
- **Installation propre**, **mise à jour** depuis la version précédente, **signature et notarisation macOS** vérifiées de bout en bout ; **signature Authenticode Windows** décidée (certificat : coût et délai hors du dépôt).
- **Mesure des plantages** : journal local déjà présent (`core/diagnostics_log.py`) ; indicateur « plantages par heure de session » sur séances de 2 h.
- Les trois tests déjà repérés instables en CI sont revérifiés en boucle (le CHANGELOG `[Unreleased]` en note la correction).
**Portes de sortie :** (1) les trois parcours passent sur macOS et sur Windows, chaque écart est soit corrigé, soit inscrit comme défaut connu avec sa PR ; (2) **0 plantage** sur trois séances de 2 h par plate-forme ; (3) installation propre et mise à jour réussies sur chaque plate-forme, paquet macOS notarisé accepté par Gatekeeper ; (4) les fonctions « jamais vues à l'écran » (étalonnage animé, moniteur 3D, multicam) sont vues et capturées ; (5) le smoke test Linux (démarrage + export CPU) passe sur le paquet publié ; (6) **mesure de référence de lecture** (4K et 1080p, gigue, images perdues sur 60 s) relevée sur le Mac de référence, le Windows avec GPU NVIDIA et le Windows sans GPU dédié : ce sont les bases des objectifs de la phase 2.
**Taille :** M (≈ 8 PR de correctifs + banc de recette scripté, mais le travail est surtout humain sur les machines). **Dépendances :** machines Windows accessibles (décision 3). **Risques :** la recette révèle de gros défauts qui repoussent les phases suivantes, c'est son rôle ; le certificat Windows peut tarder.
**Hors v1 :** recette Linux complète sur GPU ; tests de charge multi-heures sur 8K.

### Phase 2 — Moteur

**Objectif :** que les montages réels se lisent et s'exportent sans attente, pour les trois publics.
**Périmètre :** trancher l'ADR-0001 (aperçu fidèle sans attente de segment) et l'ADR-0003 / 0004 (moteur natif) **sur des mesures**, puis exécuter le chemin retenu. Le fil conducteur est la mesure de référence de la phase 1 : trois montages types figés (un court social, un long multicam, un motion design) avec leurs temps d'export et de lecture.
Pistes déjà chiffrées dans le dépôt, à départager : calques en textures GPU (ADR-0001, étape « calques en textures », `docs/performance.md:350`) ; remplacer Qt → PNG → ffconcat par un flux plus direct (`docs/motion-graphics.md`, « Performances ») ; historique par parties partagées au lieu d'une copie profonde ; autosave incrémental (**[INCERTAIN]** le coût de 424 ms à 10 000 clips n'est écrit dans aucune doc du dépôt : à remesurer avant de s'en servir, le banc est `docs/performance.md:44`) ; virtualisation des `ClipWidget` ; moteur natif C++ (branche locale `engine/native-cpp`, **jamais poussée** : `git branch -a` n'en montre aucune copie sous `origin/`, et la mesure « lecture 4K hors fenêtre » vient de cette branche, **[INCERTAIN]** : non reproductible depuis `origin/main`).
**Portes de sortie :**
1. **Lecture fluide en vraie fenêtre** (pas hors fenêtre), mesure de gigue publiée, sans image perdue pendant 60 s sur un montage type : **4K sur macOS Apple Silicon** ; **4K sur Windows avec GPU NVIDIA** ; **1080p sur Windows sans GPU dédié** (4K sans GPU dédié : « au mieux », sans promesse). Les objectifs Windows sont figés après la mesure de référence de la phase 1 (décision 3).
2. **Export ≥ 2× plus rapide** que la référence de la phase 1, **mesuré sur médias réels** (pas sur `testsrc2`).
3. **Aperçu fidèle sans attente de segment** sur les trois montages types : l'image fidèle s'affiche en moins de 300 ms après un arrêt de lecture (valeur à confirmer par la mesure, décision 2).
4. Parité aperçu / export inchangée (les tests de parité existants passent) et empreinte du rendu (`RENDER_ENGINE_VERSION`) incrémentée à chaque changement de pixel.
**Taille :** **L** (≈ 20 à 30 PR si le moteur natif est retenu, M si l'on reste en Python + GPU). **Dépendances :** phase 1 (référence), décision 2 (natif ou non). **Risques :** le plus élevé du plan : licence LGPL de FFmpeg si l'on charge FFmpeg dans le processus (ADR-0003), build natif sur trois systèmes, dette de deux moteurs si l'on s'arrête à mi-chemin. Parade : une décision de Go / No-Go écrite après le premier jalon mesuré, jamais après coup.
**Hors v1 :** 8K (abandonné dans l'étude native), rendu distribué sur plusieurs machines, rendu cloud.

### Phase 3 — Interopérabilité et livraison

**Objectif :** que Kut-Studio s'insère dans un flux de travail existant et livre dans les formats que les trois publics exigent.
**Périmètre, par ordre de priorité :**
1. **OTIO** (OpenTimelineIO, JSON, sans dépendance native) import et export, puis **EDL** CMX3600 et **FCPXML** (import d'abord) ; XML Premiere / Final Cut ensuite ; **AAF** hors v1.
2. **Export** : 10 bits (HEVC main10, ProRes 4444), **alpha** (ProRes 4444, WebM VP9) pour la timeline, **HDR** (étiquettes BT.2020 / PQ / HLG et tone mapping vers SDR ; `core/color_matrix.py` sait déjà étiqueter `bt2020`), **séquence d'images** (PNG, EXR hors v1), GIF et WebP animé, **AV1**, **chapitres** MP4 depuis les repères `chapter`, cibles de loudness au choix (−14, −16, −23), de-esser.
3. **Boucles** (export en boucle sans couture) pour les formats courts.
**Portes de sortie :** (1) un projet OTIO exporté puis réimporté est **identique** (aller-retour octet-stable sur le sous-ensemble pris en charge, qui est écrit dans la doc) ; (2) un montage de 3 pistes ouvre dans DaVinci Resolve ou Final Cut avec les mêmes coupes (vérifié à la main, capture jointe) ; (3) chaque nouveau format d'export est validé par `ffprobe` (codec, profondeur, espace colorimétrique, alpha, chapitres) dans un test qui échoue si l'option est retirée ; (4) un clip HDR importé s'exporte en SDR avec un écart colorimétrique mesuré contre une référence, borné.
**Taille :** **L** (≈ 18 à 25 PR). **Dépendances :** phase 1 ; les formats d'export dépendent de la chaîne d'encodeurs de l'FFmpeg livré (vérifier AV1 et les encodeurs matériels, **[INCERTAIN]** : l'encodage matériel n'a été validé que sur VideoToolbox, `docs/hardware-encoding.md` décrit NVENC / QSV / AMF / VAAPI sans preuve d'exécution). **Risques :** fidélité de l'import (coupes, vitesses, transitions : tout n'a pas d'équivalent) ; HDR = sujet de colorimétrie où l'on se trompe sans références ; changements de format `.kut` (chaque champ nouveau a un défaut à la lecture, règle de `docs/social-video.md`).
**Hors v1 :** AAF, import After Effects, DCP, export EXR, rendu IMF.

### Phase 4 — Motion design, noyau

**Objectif :** qu'un motion designer retrouve ses gestes d'After Effects.
**Périmètre :** couleurs de calques animables (extension d'ADR-0008 aux `graphic.fill_color`, `stroke_color`, `glow_color`) ; **animateurs de texte** par caractère, mot, ligne, avec sélecteurs (le socle existe : `core/text_animations.py` et `core/text_runs.py` font déjà des révélations lettre / mot) ; texte sur tracé ; **trim paths** ; **mattes de piste** (alpha et luma entre calques ; aujourd'hui seul le masque propre à un clip existe, `core/mograph_ffmpeg.py:473`) ; **expressions utilisateur** (dont `wiggle`) évaluées par un langage borné, dans la frontière de sécurité d'ADR-0005 ; **caméra 2.5D** ; éditeur de courbes multi-propriétés ; parentage d'un clip vidéo comme enfant ; effets de distorsion / glitch / pixelisation ; import **SVG** puis **Lottie** (sous-ensemble documenté).
**Portes de sortie :** (1) chaque propriété nouvelle passe le test de parité aperçu / moniteur / export (même contrat que les 31 cibles d'animation) ; (2) 10 gabarits de référence de l'industrie (titre animé, lower-third, logo qui se dessine, transition de forme, etc.) sont reproduits, comparés image par image à une référence figée ; (3) une expression refusée (boucle, dépassement) ne change rien au projet ; (4) un fichier SVG et un Lottie du jeu d'essai s'importent avec la liste de ce qui est ignoré.
**Taille :** **L** (≈ 25 à 35 PR). **Dépendances :** phase 1 (moniteur et animation vus à l'écran), phase 2 pour le coût du rendu des calques. **Risques :** une expression est du code de l'utilisateur dans un moteur de rendu : même exigence de sécurité que le mode script ; Lottie est une grande surface (beaucoup de fonctions) et se découpe en sous-ensemble ; les mattes alourdissent le graphe FFmpeg.
**Hors v1 :** rotoscopie, système de particules (backlog), import After Effects (`.aep`), moteur d'expressions complet d'After Effects.

### Phase 5 — Extensibilité

**Objectif :** que la communauté et l'utilisateur étendent Kut-Studio sans le modifier.
**Périmètre :** format de **plug-ins déclaratif** (données seulement : effets composés, préréglages, transitions, modèles de texte ; aucun code exécuté, ADR écrit d'abord) ; **« enregistrer mon projet comme modèle »** (les emplacements existent déjà : `core/template_slots.py`, `core/project_templates.py`) ; **catalogue de contenus** (LUT livrées, transitions de créateur : glitch, zoom, whip ; stickers ; musique libre) avec licences vérifiables.
**Portes de sortie :** (1) un plug-in déclaratif invalide est refusé avec son code d'erreur et ne laisse rien ; (2) un modèle utilisateur s'enregistre et se rouvre à l'identique (octets stables) ; (3) chaque contenu livré a sa licence listée et vérifiée par un test ; (4) le catalogue ajoute au plus N Mo à l'application (N fixé par la décision 6).
**Taille :** M (≈ 8 à 14 PR) plus la production de contenus. **Dépendances :** phase 4 pour les modèles de motion. **Risques :** licences (musique, stickers : sources libres uniquement) ; poids du paquet.
**Hors v1 :** plug-ins exécutant du code, magasin en ligne, compte utilisateur, publication directe sur TikTok / Reels / YouTube (dépend d'API et de comptes, § « Décisions »).

### Phase 6 — Page signature

**Objectif :** une page qui montre ce que Kut-Studio fait que les autres ne font pas.
**Périmètre (recommandation révisée, décision 5) :** d'abord le **suivi automatique de sujet** (extrait du backlog : sert deux publics, courts et longs), puis l'un des deux candidats suivants, à choisir : **la page « 3D »** (ADR-0010, lots 1 à 9, soit ≈ 9 lots : une page, un viewport, un outliner, des propriétés, le picking, les gizmos, le lien avec le clip, l'import, la parité de script) **ou le moteur d'intentions** (ADR-0007 : un texte qui suit un sujet, première intention). Le reste du plan ne dépend d'aucun des deux.
**Portes de sortie :** page 3D : un parcours « créer une scène, l'appliquer, la voir dans la timeline, l'exporter » passé sans script ; chaque lot respecte ses critères propres (ADR-0010, § « Plan de livraison »). Intentions : une intention se crée, se dérive en lecture seule et se détache (critères d'ADR-0007).
**Taille :** **L** pour la page 3D (≈ 12 à 18 PR, dont deux lots L pour le viewport et les gizmos) ; M pour la première intention. **Dépendances :** phase 1 (le moniteur 3D n'a jamais été vu à l'écran), phase 4 pour la caméra 2.5D. **Risques :** le viewport QRhi n'est pas testable hors fenêtre ; sélection par rayon et gizmos demandent une vraie recette.
**Hors v1 :** animation de personnages, FBX, ombres et IBL (backlog), édition de maillage.

### Backlog d'intelligence (après la phase 2)

Aucune de ces fonctions n'entre dans la v1 par défaut : chacune dépend d'un **modèle local**, d'une **licence** et d'un **poids** à décider (décision 4).

| Fonction | Public | Dépendance | Poids estimé | Remarque |
| --- | --- | --- | --- | --- |
| Suivi automatique de sujet / visage (pour le recadrage 9:16) | court, long | détecteur léger (pas de cloud) ; le suivi par points existe déjà, `docs/tracking.md` | quelques dizaines de Mo | le reframe suivi existe (`core/follow_reframe.py`) mais à partir de points posés à la main |
| Détourage / suppression de fond / rotoscopie | court, motion | modèle de segmentation (SAM-like) ou matting vidéo | 100 Mo à 1 Go | explicitement hors périmètre du suivi aujourd'hui (`docs/tracking.md`, « Hors périmètre ») ; licence du modèle à vérifier |
| Synthèse vocale (TTS) | court, long | moteur local libre | 50 Mo à 500 Mo | langues fr / en / es à tenir |
| Transcription intégrée | court, long | whisper.cpp **fourni** et modèle téléchargeable au choix de l'utilisateur ; aujourd'hui rien n'est téléchargé (`docs/social-video.md`, transcription) | 150 Mo (base) à 1,5 Go | l'utilisateur installe encore le programme |
| Moteur de particules | motion | aucun modèle ; calcul déterministe sur CPU | — | seule la transition « désintégration » en parle (`core/transition_presets.py`), aucun moteur |
| IA générative | tous | — | — | **hors v1** |

**Option ultérieure, non planifiée :** les modèles distants ou locaux (MiniMax Go, Devstral via Hermes) pourront être essayés pour **écrire du code** sur des tâches **vérifiables par test** (portage de format, génération de jeux d'essai), jamais pour des décisions de conception ni de la relecture. À la demande de l'utilisateur, aucun essai n'est planifié ici.

---

## Parallélisation

Jusqu'à **4 chantiers** simultanés (limite des abonnements). Un chantier = un worktree, une branche, un superviseur qui relit. Règles :

1. **Fichiers disjoints.** Deux chantiers ne modifient jamais le même module. Les zones de conflit connues : `core/export_engine.py` (très gros : un seul chantier à la fois, phases 2 et 3) ; `core/project_io.py` (versions du format : un seul chantier qui l'incrémente) ; `core/animation_targets.py` (phases 4 et 6) ; `ui/i18n*.py` (libellés : chaque PR ajoute ses clés dans son propre fichier de traduction quand c'est possible).
2. **Registres de dette** : `tests/mypy_debt_baseline.txt` et les cliquets de l'audit i18n ne sont modifiés que dans la PR qui change la dette, jamais « en passant » ; deux chantiers qui les touchent se fusionnent l'un après l'autre.
3. **`CHANGELOG.md`** : une ligne par PR sous `[Unreleased]`, ajoutée au dernier moment (la fusion crée sinon un conflit à chaque PR) ; jamais de ligne partagée.
4. **Chaque PR est mergée seule**, après Codex et la suite complète sur les quatre systèmes de la CI ; une PR ouverte plus de quelques jours est mise à jour par un *merge* de `main`, jamais par un rebase.

Combinaisons recommandées (sans conflit de fichiers) :

| Fenêtre | Chantier A | Chantier B | Chantier C | Chantier D |
| --- | --- | --- | --- | --- |
| Après la phase 0 | Phase 1, parcours TikTok et macOS | Phase 1, parcours multicam et Windows | Dette de documentation | Interopérabilité : OTIO (lecture seule, aucun risque pour la recette) |
| Après la porte de la phase 1 | Phase 2, mesure et décision | Phase 3, export 10 bits / alpha | Phase 3, EDL / FCPXML | Phase 4, couleurs animées |
| Phase 2 en cours | Moteur (un seul chantier sur le chemin de rendu) | Phase 3 (modules d'import, pas l'export) | Phase 4, texte et trim paths | Phase 5, format déclaratif |

**Ce qui ne se parallélise pas :** la phase 1 avec tout ce qui touche au rendu ou à l'animation (on valide avant de modifier) ; le moteur natif avec toute modification du chemin de rendu FFmpeg.

## Décisions à prendre par l'utilisateur

Liste courte, avec ma recommandation chiffrée.

1. **Ordre des phases : recette (1) avant le moteur (2) ?** *Recommandation : oui.* La recette coûte ≈ 8 PR et deux semaines de machines ; elle donne la référence de la phase 2 et évite de reconstruire sur un socle fragile.
2. **Moteur natif C++ : le tenter ou rester Python + GPU + FFmpeg ?** *Recommandation : mesurer d'abord (≈ 4 PR de bancs sur les trois montages types), puis trancher. Prior : natif seulement si l'export ne dépasse pas ×1,5 sans lui* (le spike GPU a donné ×1,5, ADR-0001), *car la cible de la porte est ×2 et le natif coûte 20 à 30 PR et une dépendance LGPL.*
3. **Plates-formes de la recette : macOS + Windows seulement ?** *Recommandation : oui pour la v1 ; Linux = smoke test scripté « démarre et exporte en CPU » dans la phase 1 (aucune garantie GPU ni de lecture).* Il faut un accès à un Windows avec GPU NVIDIA (l'utilisateur ou un prêt) et décider d'un **certificat Authenticode** (≈ 200 à 400 €/an, à confirmer).
4. **Intelligence : lesquelles entrent dans la v1 ?** *Recommandation : suivi automatique de sujet (taille S-M, 3 à 6 PR, modèle léger) et transcription fournie ; détourage seulement si l'utilisateur accepte un modèle de plusieurs centaines de Mo ; TTS et IA générative hors v1.*
5. **Page signature : la page 3D (ADR-0010) ou le moteur d'intentions (ADR-0007) ?** *Recommandation révisée avec la règle de priorité par public (principe 2).* Ni la page 3D ni les intentions ne touchent trois publics : la page 3D sert **un** public (motion design + 3D ; aucune preuve dans l'audit ni le dépôt qu'un autre public l'utilise), la première intention (texte qui suit un sujet) sert surtout le public formats courts. À égalité de portée, le critère de départage est la préparation : la page 3D a un plan chiffré (9 lots, ≈ 12 à 18 PR) et son document est livré (ADR-0010 lot 0). **Mais** la règle plaide d'abord pour le **suivi automatique de sujet** (backlog, S-M, 3 à 6 PR), qui sert **deux** publics (courts et longs, via le reframe 9:16 et la mise au point d'un titre) et rend la première intention bien plus utile. Recommandation chiffrée : phase 6 = (a) suivi automatique de sujet (3 à 6 PR), puis (b) la page 3D (12 à 18 PR). Alternative : page 3D seule, si l'utilisateur place le motion design 3D avant le gain pour les deux autres publics.
6. **Poids de l'application et contenus livrés :** *Recommandation : plafond de 150 Mo de contenus embarqués (LUT, transitions, polices, quelques stickers libres), le reste en téléchargement à la demande, sans compte.* Base mesurée : assets publiés de la v0.8.0 (publiée le 2026-10-20, `gh release view v0.8.0`) : macOS arm64 57 012 059 octets (≈ 54,4 Mo), Windows x64 76 583 879 (≈ 73,0 Mo), Linux x64 111 668 466 (≈ 106,5 Mo). Le plafond de 150 Mo de contenus s'ajouterait à ces paquets (≈ +200 à +260 % sur macOS) : à reconfirmer à 100 Mo si l'utilisateur veut que le paquet macOS reste sous 160 Mo. (Le 53 Mo d'ADR-0003 est l'historique de la v0.4.0.)
7. **Publication directe (TikTok / Reels / YouTube) :** *Recommandation : hors v1.* Elle dépend d'API tierces, de comptes et d'une revue des plateformes que l'application ne contrôle pas ; en échange, la phase 3 ajoute les chapitres et la sortie multi-format, qui couvrent 80 % du besoin sans compte.

## Comment on mesure le bond en avant

Trois familles d'indicateurs, relevées à la fin de chaque phase et publiées dans `docs/perf/` (le dossier existe déjà : `RESULTS.md`, `baseline.json`, `after.json`).

1. **Couverture des attentes par public.** La liste des attentes tirée des trois audits (une attente = une ligne de l'annexe, par public) est notée *couverte / partielle / absente*. Point de départ à fixer dans la phase 0 (il sera la première mesure) ; objectif : **toutes les attentes « les trois publics » couvertes**, puis ≥ 80 % des attentes de chaque public pris seul.
2. **Temps.** Sur les trois montages types de la phase 1 : durée d'export, temps d'affichage de l'image fidèle après un arrêt, gigue de lecture 4K, durée d'ouverture d'un projet de 10 000 clips, durée d'une sauvegarde. Médiane de 3 exécutions, machine nommée, version de l'application et de FFmpeg notées. **Aucune de ces durées n'est un seuil de test unitaire** (règle de ce dépôt : les tests portent sur des identités, des octets et des ratios).
3. **Fiabilité.** Plantages par heure de session sur les séances de recette (cible : 0) ; nombre de tests instables en CI sur 30 jours (cible : 0) ; taux de réussite de l'installation propre et de la mise à jour (cible : 100 % sur les machines de recette).

« Un énorme bond en avant » signifie ici : **export ≥ 2× plus rapide, lecture 4K fluide en vraie fenêtre, les trois parcours passés sur macOS et Windows, et les interopérabilités OTIO / EDL en place**. Ce sont les quatre portes qui se voient depuis l'extérieur.

## Ce qu'on ne fera pas dans la v1

- Rotoscopie IA, IA générative, génération de contenu (hors backlog intelligence).
- Animation de personnages 3D, FBX, édition de maillage, ombres / IBL (sauf si la décision 5 ouvre un jalon).
- Import After Effects (`.aep`), AAF, export EXR / IMF / DCP.
- Plug-ins exécutant du code, magasin en ligne, comptes utilisateur, collaboration en temps réel.
- Publication directe sur les plateformes (décision 7), planificateur de publication, éditeur de miniature (sauf si la phase 3 laisse du temps ; la catégorie de repère `cover` existe).
- Enregistrement écran / webcam (public court : utile, mais un chantier de capture par système ; à décider après la phase 3).
- 8K, rendu distribué ou dans le nuage.
- Support Linux au-delà du smoke test de la phase 1 (démarrage et export CPU d'un montage court) : aucune garantie GPU, de lecture ni de parcours complet.
- Lecture 4K fluide sans GPU dédié sous Windows, et toute promesse de performance sous Linux : la performance de la v1 est promise sur macOS Apple Silicon et Windows (4K avec GPU NVIDIA, 1080p sans GPU dédié).

---

## Dette de documentation (phase 0)

Passages vérifiés comme périmés le 2026-10-21 (à corriger en une PR de doc, sans code) :

| Fichier | Dit | Réalité vérifiée |
| --- | --- | --- |
| `docs/social-video.md:219` | « `CURRENT_VERSION` reste 16 » | `CURRENT_VERSION = 17` (`core/project_io.py:117`) ; les écritures montent jusqu'à `GRADE_ANIMATION_VERSION = 24` (ligne 221) **[corrigé : la valeur est 17, pas 24 ; 24 est la version écrite quand un étalonnage est animé]** |
| `docs/motion-graphics.md:473` | « Pas de rotoscopie, système nodal, expressions, 3D ni caméra » | le système nodal existe (ADR-0002, pages Couleur et Composition), la 3D existe (Kut-3D, ADR-0009) ; seuls rotoscopie et expressions restent absents. La ligne 11 (« Ce n'est pas un logiciel nodal ») est dans le même cas pour les clips |
| `docs/animation.md:449` | « Pas encore : tracking, expressions » | le suivi alimente le moteur d'animation par des images clés dérivées (`docs/tracking.md`) ; seul « expressions » reste vrai |
| `docs/animation.md:442` | l'audio « a l'architecture mais pas encore ses cibles » | **[nuancé]** le volume s'anime par l'automation de piste (`docs/track-automation.md`, `core/audio_automation.py`), qui n'est pas une cible du moteur d'images clés : à reformuler, pas à supprimer |
| `docs/animation.md:443-445` | les couleurs de l'étalonnage « pas encore par script » | les verbes de script d'étalonnage animé sont livrés (étape 9, `CHANGELOG.md` `[Unreleased]`) |
| `docs/hardware-encoding.md` (section « Périmètre ») | « aucun preset ne propose HEVC » | `core/render_presets.py:189,191` propose Full HD et UHD en HEVC, `ExportFormat.MP4_HEVC` existe **[corrigé]** |
| `docs/gpu-preview.md` (section limites) | Direct3D 11 / Vulkan « non testés ici » | exact, mais à croiser avec la phase 1 : c'est la liste que la recette doit vider |

## Corrections apportées aux audits

Les cinq premières sont celles que je signale en tête du rapport de PR ; les autres sont dans le texte.

1. **« Le format courant est 24 »** : `CURRENT_VERSION` vaut **17** ; 24 est la version maximale écrite (étalonnage animé). `docs/social-video.md` cite 16 : périmé de deux façons.
2. **« Pas d'animateur de texte par caractère »** : nuancé. Des révélations mot par mot, lettre par lettre (machine à écrire) et karaoké existent (`core/text_animations.py` : `word_by_word`, `typewriter`, `karaoke`). Ce qui manque : animateurs à **sélecteurs** (plage, décalage, aléa) et animation de couleur ou de transformation par caractère.
3. **« H.264 / HEVC seulement via preset H.264 »** : `docs/hardware-encoding.md` dit qu'aucun preset ne propose HEVC ; le code en propose deux (`core/render_presets.py:189,191`). La doc est périmée, pas l'audit.
4. **« Autosave 424 ms à 10 000 clips »** : **[INCERTAIN]**, aucune doc du dépôt ne l'enregistre. À remesurer en phase 2 avant de s'en servir.
5. **« Moteur natif : lecture 4K mesurée hors fenêtre »** : **[INCERTAIN]**, la mesure vient de la branche locale `engine/native-cpp`, qui n'existe pas sous `origin/`. Non reproductible depuis `origin/main` ; elle sera refaite en phase 2.
6. **« Encodage matériel exécuté seulement sur VideoToolbox »** : **[INCERTAIN]**. Le code et `docs/hardware-encoding.md` décrivent NVENC / QSV / AMF / VAAPI avec leurs options, mais aucune doc ne dit sur quelles machines ils ont tourné ; la recette de la phase 1 le tranchera.
7. **« HEIC incertain »** : confirmé **absent** au sens du code : aucune occurrence de `heic` / `heif` dans `core/` ni `ui/` ; seuls PNG, JPEG, BMP, WebP et TIFF sont listés (`core/film_render.py:38`, `core/template_slots.py:28`). L'ouverture réelle par Qt n'a pas été essayée.

## Limites de cet ADR

- Les tailles (S / M / L) et l'enveloppe de 130 à 190 PR sont des estimations par analogie avec les ADR précédents (ADR-0008 : 9 étapes ; ADR-0010 : 9 lots). Elles seront réestimées à la fin de chaque phase.
- Les portes de sortie qui citent des seuils (« 300 ms », « 0 plantage sur trois séances ») sont des propositions à valider avec la première mesure ; changer un seuil se fait dans cet ADR, pas en silence.
- Rien ici n'a été lancé à l'écran ; cet ADR ne contient aucune mesure nouvelle.

---

## Annexe : tableau croisé des écarts (manque × public × phase)

Publics : **C** = formats courts, **L** = YouTube / montage long, **M** = motion design et 3D. « ● » = le manque touche ce public. « Phase » = où le plan le traite ; « BL » = backlog d'intelligence ; « — » = hors v1.

| # | Manque (vérifié) | C | L | M | Phase |
| --- | --- | --- | --- | --- | --- |
| 1 | Recette sur machines réelles (Windows, Linux GPU, multicam réel, sessions longues ; Linux = smoke test seulement) | ● | ● | ● | 1 |
| 2 | Fonctions « jamais vues à l'écran » (étalonnage animé, moniteur 3D) | | | ● | 1 |
| 3 | Signature Authenticode Windows, notarisation / mise à jour à valider | ● | ● | ● | 1 |
| 4 | Plantages non mesurés par heure de session | ● | ● | ● | 1 |
| 5 | Aperçu fidèle qui attend ses segments (ADR-0001) | ● | ● | ● | 2 |
| 6 | Mograph Qt → PNG → ffconcat | | | ● | 2 |
| 7 | Aperçu GPU partiel, Windows / Linux non testé | ● | ● | ● | 1, 2 |
| 8 | Un `ClipWidget` par clip, timeline lourde sur longs montages | | ● | | 2 |
| 9 | Autosave réécrit tout le `.kut` (coût **[INCERTAIN]**) | | ● | | 2 |
| 10 | Historique en copie profonde, `MAX_HISTORY = 100` | | ● | ● | 2 |
| 11 | Moteur natif C++ non décidé, branche jamais poussée | ● | ● | ● | 2 |
| 12 | Import / export OTIO, EDL, FCPXML, XML | ● | ● | ● | 3 |
| 13 | AAF | | ● | | — |
| 14 | Export 10 bits H.264 / HEVC (seul le ProRes l'est) | | ● | ● | 3 |
| 15 | HDR et tone mapping | | ● | | 3 |
| 16 | Alpha à l'export de la timeline | | | ● | 3 |
| 17 | Séquence PNG, GIF, WebP animé, AV1 | ● | | ● | 3 |
| 18 | Chapitres MP4, horodatages de publication | | ● | | 3 |
| 19 | Boucles sans couture | ● | | ● | 3 |
| 20 | Loudness à cible fixe −14 LUFS, pas de de-esser | | ● | | 3 |
| 21 | Import SVG, Lottie | | | ● | 4 |
| 22 | Import After Effects | | | ● | — |
| 23 | Couleurs de calques non animables | | | ● | 4 |
| 24 | Animateurs de texte à sélecteurs, texte sur tracé | ● | | ● | 4 |
| 25 | Trim paths | | | ● | 4 |
| 26 | Mattes de piste (alpha / luma) | ● | | ● | 4 |
| 27 | Expressions utilisateur, `wiggle` | | | ● | 4 |
| 28 | Caméra 2.5D | | | ● | 4 |
| 29 | Éditeur de courbes une propriété à la fois | | | ● | 4 |
| 30 | Parentage limité pour les clips vidéo | | | ● | 4 |
| 31 | Effets sans distorsion, glitch, pixelisation | ● | | ● | 4 |
| 32 | Plug-ins déclaratifs | ● | ● | ● | 5 |
| 33 | « Enregistrer mon projet comme modèle » | ● | | | 5 |
| 34 | Stickers, musique libre, LUT livrées | ● | ● | | 5 |
| 35 | Transitions de créateur (glitch, zoom, whip) | ● | | | 5 |
| 36 | Page « 3D » d'édition (ADR-0010 lots 1 à 9) | | | ● | 6 |
| 37 | Ombres, IBL, mipmaps, caméra 3D animée, animation d'objets de scène | | | ● | — (jalon possible) |
| 38 | Moteur d'intentions (ADR-0007) | ● | | | 6 (au choix) |
| 39 | Suivi automatique de sujet / visage | ● | ● | | 6 (a), modèle en BL |
| 40 | Détourage / rotoscopie | ● | | ● | BL |
| 41 | Synthèse vocale | ● | ● | | BL |
| 42 | Moteur de particules | | | ● | BL |
| 43 | Transcription fournie (aujourd'hui installée par l'utilisateur) | ● | ● | | BL |
| 44 | Publication directe TikTok / Reels / YouTube | ● | | | — |
| 45 | Éditeur de miniature | ● | ● | | — |
| 46 | Enregistrement écran / webcam | ● | ● | | — |
| 47 | HEIC (absent du code ; essai réel non fait) | ● | | | 3 (si peu coûteux) |
| 48 | Dette de documentation (7 passages) | ● | ● | ● | 0 |

# Vidéo sociale (0.2.0 « Social Night »)

Monter une vidéo verticale de réseau social **dans l'application, sans code** : format 9:16 / 4:5 / 1:1, montage sur
le temps, titres façon TikTok, effets lumineux, templates à remplir, SFX, son normalisé et export prêt à publier.

Point de départ : l'edit F1 « Singapore GP 2026 » (35 s, 1080×1920, 19 plans à 120 BPM) avait dû être écrit en Python
(`productions/singapore_gp_2026/`, hors dépôt). Chaque chose que ce script faisait à la main est devenue une fonction de
`core/` avec son interface, et le template **Night Race** en reprend la structure.

## Pour l'utilisateur

### 1. Créer le projet

*Réseaux sociaux › Nouveau projet réseaux sociaux…* : nom, **format** (Vertical 9:16 1080×1920, Portrait 4:5 1080×1350,
Carré 1:1 1080×1080, Paysage 16:9), **cadence** (30 ou 60 i/s), zones de plateforme, et un **template** choisi dans une
galerie à vignettes (ou « Projet vide »). Le projet a ses pistes nommées : V1 images, G1 titres, A1 musique (rôle
*musique*), A2 voix (*voix*), A3 SFX (*sfx*) — les rôles servent au ducking et au placement des SFX.

*Réglages de la séquence…* change ensuite la taille du cadre et la cadence.

### 2. Cadrer

* **Zones de la plateforme** (*Réseaux sociaux › Zones de la plateforme*) : TikTok, Reels ou Shorts. Le viewer grise la barre du haut, la
  légende du bas et la colonne d'actions ; ces zones sont asymétriques, comme sur les applications.
* **Remplir le cadre** (*Réseaux sociaux › Remplir le cadre (recadrer)*, ou l'inspecteur) : un plan 16:9 posé dans un cadre 9:16 est agrandi jusqu'à le couvrir (au lieu de bandes noires).
  *Cadrage X / Y* (−1 … 1, animables) déplace la fenêtre dans l'image : un **pan animé** recadre un plan horizontal en
  vertical.
* **Ken Burns** sur les photos (et les plans) sélectionnés ; *Photos* propose de remplir le cadre et de poser un
  Ken Burns à chaque import (*Photos importées*), avec une durée par défaut réglable (préférences de l'application, pas du projet).

### 3. Monter sur le temps

* **Grille rythmique** (*Réseaux sociaux › Grille rythmique › Tempo et calage…*) : tempo en BPM (saisi, tapé au clavier ou **détecté** sur
  le clip musique sélectionné), décalage du premier temps, temps par mesure. La règle montre les temps (plus marqués sur
  les mesures) et le magnétisme s'y accroche (*Aimanter aux temps*).
* **Couper sur les temps** : découpe le clip sélectionné tous les N temps. **Répartir sur la grille** : pose les clips
  sélectionnés bout à bout, N temps chacun.

### 4. Titres

Dans l'éditeur de graphiques d'un texte :

* **Contour extérieur** : le contour est dessiné **sous** le remplissage ; l'intérieur des lettres reste à la couleur du
  texte, même avec un contour épais.
* **Emojis couleur** dans le texte (🔥 😱 👇) : dessinés avec la police emoji du système, sans contour ni ombre.
* **Animations** : pop-in avec dépassement, glissé depuis les 4 côtés, fondu montant, rebond en boucle, **mot par mot**,
  machine à écrire, **karaoké** ; **mots mis en couleur** (indices fixes, couleur de surbrillance).
* **Karaoké calé sur la voix** : *Synchroniser sur la voix* estime le temps de chaque mot à partir du clip voix sous le titre
  (segments voisés, alignement par programmation dynamique). C'est une estimation : les temps se corrigent un par un.
* **Polices embarquées** (OFL) : Anton et Saira ExtraCondensed, identiques sur toutes les machines.
* Presets motion graphics, catégorie **Vertical** : Titre TikTok, Mot en couleur, Sous-titre karaoké. Les presets
  intégrés sont reconstruits sur la toile du projet (le lower third se recentre en 9:16).

### 5. Lumière et impact

* **Néon** des textes et des formes (couleur, rayon, intensité ; les deux derniers animables).
* **Calques de lumière** (bibliothèque *Graphiques › Lumière*) : light leak, flare anamorphique, lignes de vitesse,
  traînées, étincelles, flash, **grain** — en Addition (le grain en Incrustation), déterministes (même image à chaque
  rendu).
* **Effets vidéo** : glow / bloom, aberration chromatique, heat haze ; presets d'effets **Night Look** et **Neon Rush**
  (sur un calque d'effets, ils s'appliquent à tout le montage).
* **Impact** : zoom d'impact (le plan entre agrandi de 8 % et se pose en 0,3 s), secousse de caméra, flash blanc à la tête
  de lecture, et **Impact sur chaque cut (zoom + flash)** sur la piste vidéo.

### 6. Templates « Night »

| Template | Durée | Contenu |
| --- | --- | --- |
| **Night Race** | 35 s | 19 plans à 120 BPM, punch-in sur chaque cut (14 % sur les plans « warp »), flash, accroche, titres, classement, appel aux commentaires |
| **City Lights** | 16 s | 8 plans de 2 s en Ken Burns, light leaks sur les cuts, titre néon, phrase en karaoké |
| **Classement** | 8 s | un tableau de cinq lignes qui glissent l'une après l'autre |
| **CTA commentaires** | 6 s | question, 👇 qui rebondit, pastilles DIRECT / RÉSUMÉ |

Chaque template construit un projet ordinaire : lit rythmique synthétisé (à remplacer par votre musique) et ses repères
(*Accroche*, *Drop*, *Fin*), SFX sur les cuts, ducking « TikTok punchy » sous la piste voix, Night Look sur un calque
d'effets, grain. Les titres sont traduits et rétrécis s'ils dépassent la largeur du cadre.

**Emplacements** : un plan vide est une **carte numérotée** (`01`, `02`…). **Glisser une vidéo** sur la carte dans la
timeline la remplit en gardant sa place, son zoom d'impact, ses effets et le cadrage « remplir » (un média plus court
raccourcit le plan). À l'ajout d'un export, la barre d'état signale les emplacements encore vides : ils sortiraient comme
des cartes.

**Classement** (*Réseaux sociaux › Classement…*) : sur un calque du classement, rouvre son tableau (rang, nom, valeur,
couleur de la palette, durée) ; sinon en crée un à la tête de lecture. Valider remplace les calques du classement.

### 7. Son

* **Bibliothèque SFX** (section Audio, onglet *SFX*) : whooshes, impact, risers, cymbale inversée, passages de moteur avec
  effet Doppler, montées en régime, lit rythmique 120 BPM — synthétisés, libres de droits. *Écouter*, *Poser* (ou
  double-clic) : le son est posé sur son **ancre** (le centre d'un whoosh, le pic d'un passage, la fin d'un riser tombe sur
  la tête de lecture), sur une piste SFX libre ou nouvelle, un peu plus bas près d'une voix.
* **Placer un SFX sur chaque cut** (onglet *SFX* ou *Réseaux sociaux › Audio*) : un son par cut de la piste vidéo, les sons
  sélectionnés tournant (trois whooshes sinon). Une seule entrée d'historique.
* **Ducker la musique sous la voix** : presets *Voix douce* (−6 dB), *Voix sur musique* (−12 dB), *TikTok punchy*
  (−18 dB, attaque 20 ms, relâchement 250 ms).

### 8. Exporter

* Presets **TikTok**, **TikTok 60 fps**, **Instagram Reels**, **YouTube Shorts**, **Instagram 4:5**, **Carré 1:1** :
  H.264, son normalisé à **−14 LUFS**, copie d'aperçu et couverture (trois cases du panneau d'export, qui suivent le
  preset et se décochent).
* **Normalisation** : une passe de mesure EBU R 128 du mixage, puis un gain **statique** et un limiteur à −1 dBFS. La
  dynamique du montage est gardée ; seul le niveau d'ensemble bouge. Le détail du job montre la mesure.
* **Copie d'aperçu** (`<nom>-apercu.mp4`) : 720 px sur le petit côté, moins de 30 Mo, taille vérifiée.
* **Couverture** (`<nom>-couverture.jpg`) : l'image au marqueur *Couverture* (*Réseaux sociaux › Couverture à la tête de
  lecture* ; un seul par séquence), sinon à la tête de lecture lors de l'ajout de l'export.

## Architecture

Les règles de [architecture.md](architecture.md) s'appliquent sans exception : `core/` n'importe jamais `ui/`, une
information a une source, l'aperçu fidèle rend le graphe de l'export, les processus passent par
`core/process_supervisor.py`.

### Où vit chaque chose

| Domaine | Cœur | Interface |
| --- | --- | --- |
| Formats, projet social | `social_formats.py` | `social_dialogs.py`, `main_window_mixins/social.py` |
| Zones de plateforme | `canvas_guides.py` (`PLATFORM_ZONES`) | `viewer_overlay.py` |
| Remplir, pan | `ClipTransform.fill`, `pan_x` / `pan_y` (`visual_effects.py`) ; `export_engine.py` (crop exact) ; `tracking_motion.fit_box` (GPU) | inspecteur |
| Ken Burns | `ken_burns.py` | mixin social |
| Grille rythmique | `beat_grid.py` (modèle), `beat_edit.py` (couper, répartir), `beat_detection.py` (numpy) | `beat_grid_dialog.py`, mixin `beat_grid.py`, règle |
| Texte | `text_runs.py` (emojis, mots), `text_animations.py`, `word_timing.py`, `bundled_fonts.py` | `text_animation_editor.py` |
| Lumière, impact | `light_layers.py`, `impact_fx.py`, effets `GLOW` / `CHROMATIC_ABERRATION` / `HEAT_HAZE` (`effects_model.py`) | bibliothèques Graphiques et Effets |
| Templates | `project_templates.py`, `template_slots.py`, `leaderboard.py` | `template_gallery.py`, `leaderboard_dialog.py`, mixin `templates.py` |
| SFX, ducking | `sfx_synth.py`, `sfx_placement.py`, `audio_automation.DUCKING_PRESETS` | `sfx_library.py`, mixin `social_audio.py` |
| Loudness, livrables | `loudness.py`, `social_deliverables.py`, `render_presets.py`, `render_queue.py` | `export_panel.py`, `render_queue_panel.py` |

### Parité aperçu = export = GPU

| Famille | Calculée par | Pourquoi c'est identique |
| --- | --- | --- |
| Calques procéduraux (lumière, néon, grain, cartes d'emplacement, emojis) | le rastériseur Qt partagé (`mograph_raster`), à graine fixe | même code pour l'export, l'aperçu fidèle et le moniteur ; le moniteur GPU compose lui-même les calques en Addition / Écran / Incrustation (`gpu_composite`) |
| Mouvements (impact, secousse, Ken Burns, pan, pop-in) | images-clés de transform (cubiques exactes) | les trois rendus lisent le même évaluateur |
| Effets de voisinage (bloom, aberration, heat haze) | FFmpeg + shader + référence numpy | `tests/test_light_effects_export.py`, `tests/test_gpu_*` comparent FFmpeg, l'oracle numpy et les shaders |

Le FFmpeg de référence (9.0.2) n'a ni `drawtext` ni `libass` : tout texte est rastérisé par Qt. Les commandes utilisent
`-fps_mode`, jamais `-vsync`.

Trois points d'architecture méritent d'être connus :

* **Carte d'emplacement** : un clip de piste vidéo dont `template_slot` est renseigné et dont le média manque n'est pas
  un « média introuvable » : `build_render_plan` le remplace par un calque de texte à la taille du cadre
  (`template_slots.slot_card`), animé comme le clip. `RenderPlan.empty_slots` les liste.
* **Loudness** : la mesure est faite par le graphe audio de l'export (`_build_filter_complex(..., audio_only=True)`), à
  l'étape de préparation de la file ; le plan porte ensuite `loudness_gain_db` (dans l'empreinte). C'est un réglage
  d'export : l'aperçu n'est pas normalisé.
* **Livrables** : tirés du MP4 exporté, dans le fil de travail de la file, après le rendu ; un livrable manquant est
  rapporté dans le job sans faire échouer la vidéo.

### Versions et caches

* `RENDER_ENGINE_VERSION` = **9** (`core/filter_graph.py`) : 8 pour le flou mis à l'échelle de la sortie, 9 pour la
  toile fixe de `rotate` sous une échelle animée (côté multiple de 4 : un côté ≡ 2 mod 4 décalait la chrominance).
* `RASTER_VERSION` (`core/mograph_raster.py`) entre dans le nom des PNG des calques et dans l'empreinte : un changement
  de dessin ne ressert jamais d'anciennes images.
* `SYNTH_VERSION` (`core/sfx_synth.py`) entre dans le nom des fichiers SFX (`<id>-v1.wav`) ; un fichier absent est
  resynthétisé à l'identique, dans le dossier de données de l'utilisateur (`platform_paths.user_data_dir`, isolé par
  `KUT_STUDIO_DATA_DIR` dans les tests).

### Format `.kut`

`CURRENT_VERSION` reste 16 : chaque champ nouveau a un défaut à la lecture et n'est écrit que s'il est réglé.

| Champ | Où | Défaut |
| --- | --- | --- |
| `fill`, `pan_x`, `pan_y` | transform du clip | faux, 0, 0 |
| `beat_grid` | séquence | absent (pas de grille) |
| `generated_groups` | séquence | absent |
| `template_slot` | clip | absent |
| `stroke_position`, `word_reveal`, `reveal`, `highlight_*`, `word_times`, `glow_*`, `light_*` | calque graphique | rendu des anciens fichiers inchangé |
| catégories de marqueur `music_cue`, `cover` | marqueur | `standard` |

`tests/rich_project.py` les contient tous : `test_kut_integrity.py` vérifie l'aller-retour et la suppression de chaque
clé.

## Tests

Tout ce qui touche au rendu est vérifié avec le **vrai FFmpeg** (ignoré avec une raison s'il manque) : cadrage et pan
(`test_framing.py`), ratio de sortie (`test_output_aspect.py`), contour extérieur, emojis et révélation
(`test_text_animation.py`), lumière (`test_light_layers.py`, `test_light_effects_export.py`), impact
(`test_impact_fx.py`), loudness (`test_loudness.py` : −14 ± 0,5 LUFS pour un mixage très bas ou presque saturé),
livrables (`test_social_deliverables.py` : couverture à l'image exacte, copie sous son plafond), templates
(`test_project_templates.py`, `test_template_slots.py`). Le BPM est détecté sur des boucles synthétisées
(`test_beat_detection.py`, ±0,5 BPM et ±20 ms).

## Limites connues

* La normalisation ne s'applique qu'à l'export (l'aperçu garde le niveau du mixage).
* Un emplacement de template n'accepte qu'une vidéo : une photo se pose sur une piste de titres.
* Le pan de recadrage ne suit pas encore un point de tracking (il s'anime à la main).
* Le karaoké calé sur la voix est une estimation (enveloppe d'énergie, pas de reconnaissance vocale).
* Les noms des presets motion graphics intégrés ne sont pas traduits (comme les presets existants).

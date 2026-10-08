# Journal des modifications

Les changements notables de Kut-Studio, version par version. Le format suit
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et la numérotation
[Semantic Versioning](https://semver.org/lang/fr/). Les paquets de chaque version et la liste complète des pull
requests sont sur la page [Releases](https://github.com/Kyyzen0/Kut-Studio/releases).

**Tenir ce journal.** Une pull request qui change ce que voit ou fait l'utilisateur ajoute sa ligne sous
`[Unreleased]`, dans la bonne catégorie (*Added*, *Changed*, *Fixed*, *Removed*…), dite du point de vue de
l'utilisateur. Au moment d'une release, en même temps que `APP_VERSION` passe à la nouvelle version dans
`core/app_version.py` (étape 1 de la procédure de [docs/updates.md](docs/updates.md)), `[Unreleased]` devient
`[X.Y.Z] - AAAA-MM-JJ` (date de publication) et une section `[Unreleased]` vide est rouverte. Le tag `vX.Y.Z` poussé
ensuite déclenche `.github/workflows/release.yml`.

## [Unreleased]

### Added

- Export H.265 (HEVC) : deux presets MP4, « H.265 1080p » et « H.265 4K ». Ils suivent le même choix d'encodeur que
  H.264 (matériel quand la machine le valide, CPU sinon) ; à qualité visuelle comparable, le fichier est plus léger.
- Ouverture d'un projet `.kut` par double-clic : depuis le Finder sous macOS. Sous Windows et Linux, le bouton
  « Associer les projets .kut » des préférences écrit l'association dans le profil de l'utilisateur, sans droits
  administrateur.
- Découpage aux changements de plan : « Découper aux changements de plan », dans le menu de séquence, coupe le clip
  sélectionné à chaque changement de plan détecté par FFmpeg (le clip d'origine garde le premier plan). Un clip retimé,
  inversé, figé ou imbriqué est refusé. L'opération s'annule comme les autres coupes.

## [0.2.6] - 2026-10-08

Version corrective : la lecture d'un montage chargé redevient fluide quand les scopes sont affichés.

### Fixed

- La lecture d'un montage riche en titres et calques (l'edit vertical « Night Race », par exemple) n'est plus
  saccadée ni ne gèle l'application quand les scopes sont affichés : l'image analysée par les scopes était préparée
  sur le fil de l'interface, jusqu'à plusieurs secondes par image. Elle est maintenant préparée en tâche de fond, à la
  taille que l'analyse utilise, et la lecture suit l'horloge réelle (une image en retard est sautée au lieu de
  ralentir la vidéo). Mesuré : de 1,2 s à 8 s de vidéo lues en 8 s.
- Les scopes fonctionnent pour toutes les largeurs d'image : une largeur dont le triple n'est pas multiple de 4
  (1366 px, par exemple) donnait une erreur « taille de buffer incohérente » et des scopes vides.

## [0.2.5] - 2026-10-08

Version de consolidation après « Social Night » : surtout des correctifs d'export (couleurs, cadence, ProRes), de
compatibilité FFmpeg et de fiabilité.

### Added

- Au démarrage, la barre d'état prévient quand l'aperçu GPU reste suspendu après un plantage, et indique le réglage
  qui le rétablit (*Préférences › Performance › Matériel › Rendu de l'aperçu › GPU*) ; le diagnostic matériel le
  nomme en toutes lettres.
- Développement : la CI teste aussi FFmpeg 7.1 et Python 3.13 (Debian 13), en plus de FFmpeg 6.1 et de la dernière
  version sous Python 3.11.
- Développement : une release installe des versions figées de ses dépendances (`constraints.txt`) ; un même tag
  reconstruit les mêmes paquets.
- Développement : mesure de la couverture de code (`core/` et `ui/`, branches comprises) et cliquet en CI qui bloque
  toute baisse, sans service externe ([docs/coverage.md](docs/coverage.md)).

### Changed

- L'aperçu fidèle d'un passage éloigné du début de la timeline est bien plus rapide : un segment ne compose plus tout
  ce qui le précède. Sur un montage social de 31 s, un segment à 24 s passe de 5,7 s à 1,4 s, à image et son
  identiques.
- Le curseur « Volume » de l'onglet Audio s'appelle « Volume du moniteur » : une infobulle précise qu'il ne règle que
  l'écoute de l'aperçu (rien n'est enregistré ni exporté), il va de 0 à 100 %, et son libellé passe au-dessus du
  curseur pour ne plus élargir l'inspecteur dans les petites fenêtres.
- Les échecs que l'application tolère sans s'arrêter (un aperçu qui ne se rafraîchit pas, un rendu d'arrière-plan
  qui ne s'annule pas, un préchargement de l'aperçu abandonné, des scopes non calculés, un panneau des calques
  vide…) laissent désormais une trace dans le journal de diagnostic au lieu de passer inaperçus.
- L'édition de l'automation de volume de piste n'est plus présentée comme disponible : l'interface ne l'expose pas
  encore. Elle est inscrite à la feuille de route ; le ducking des presets réseaux sociaux reste disponible.
- Python 3.11 est annoncé comme version minimale pour lancer Kut-Studio depuis les sources : la documentation
  indiquait 3.10, avec lequel l'application ne démarre pas.
- Développement : une seule fonction d'écriture atomique (`core/atomic_io`) remplace quatorze copies ; quatre caches
  JSON qui s'écrivaient par un nom temporaire fixe ne peuvent plus s'écraser entre deux instances. mypy vérifie aussi
  le corps des fonctions non annotées, et un garde-fou refuse les `assert` en production et les `except` qui avalent
  l'erreur sans laisser de trace.

### Fixed

- Un export dont la source n'est pas balisée BT.709 (capture d'écran, vidéo SD, montage fait seulement de titres et
  de formes, séquence vide) n'est plus assombri : la conversion de couleur de sortie lui retirait près de 2 niveaux
  avec FFmpeg 9 et près de 4 avec FFmpeg 7.1 (Mac Apple Silicon). Les sources BT.709 ne changent pas, et
  les modes de fusion, les séquences imbriquées et les scopes donnent maintenant les mêmes couleurs quelle que soit la
  version de FFmpeg. L'export est aussi plus rapide (environ −20 % en 1080p H.264, −15 % en ProRes, mesuré sur Mac) ;
  l'aperçu fidèle, qui compose avec le même graphe, en profite.
- À l'export, la position d'un clip est arrondie au pixel le plus proche ; elle était ramenée au pixel pair inférieur,
  si bien qu'un déplacement lent avançait par pas de 2 pixels et qu'une vidéo stabilisée tremblait d'un pixel une
  image sur deux.
- L'export ProRes Master est bien en ProRes 422 HQ (4:2:2 10 bits) : il sortait en 4:4:4 sous l'étiquette 422 HQ, et
  le décodage matériel le refusait quand on réimportait le fichier.
- Avec FFmpeg 7.0 ou 7.1 (Debian 13, Ubuntu récents, `ffmpeg@7` de Homebrew) : l'export d'un projet contenant une
  transition échouait (« current rate of 1/0 is invalid ») ; un arrêt sur image n'affichait qu'une image puis du noir ;
  un arrêt sur image d'une séquence imbriquée faisait planter FFmpeg ; l'interpolation lisait des images source
  dupliquées ou manquantes, plus sombres de deux niveaux.
- Le ducking ne coupe plus au hasard la fin de la musique : selon l'ordre dans lequel FFmpeg terminait la musique et
  la voix, jusqu'à 1,3 s de musique disparaissait en fin de timeline (toutes versions de FFmpeg).
- L'export garde la cadence du projet. Les presets (YouTube, H.264, ProRes Master, réseaux sociaux…) exportaient
  toujours à 30 ou 60 i/s : un projet en 25, 23,976 ou 29,97 i/s ressortait avec des images dupliquées et des
  saccades. Ils suivent désormais la cadence de la séquence ; seul « TikTok 60 fps » impose la sienne, et le panneau
  Export prévient quand une cadence imposée diffère de la séquence. Le preset Custom propose « Cadence du projet »
  (par défaut), 23,976, 29,97, 50 et 59,94 i/s.
- Une vidéo exportée en 29,97, 23,976 ou 59,94 i/s porte sa cadence exacte (30000/1001…) au lieu d'une valeur
  approchée (2997/100) que certains lecteurs et logiciels de montage signalent comme non standard.
- L'import d'un sous-titre `.srt` ou d'une LUT `.cube` enregistré par le Bloc-notes Windows ou Subtitle Edit
  fonctionne : la marque d'encodage (BOM), l'UTF-16 « Unicode » et les vieux fichiers en Windows-1252 sont acceptés.
  Avant, la première réplique d'un SRT avec BOM était refusée et la LUT était déclarée invalide.
- L'export des sous-titres en `.srt` ne laisse plus un fichier tronqué si l'écriture échoue en cours de route.
- L'application Windows construite a de nouveau son icône (le fichier `.ico` n'était jamais généré).
- Un projet `.kut` qui contient une clé inconnue est refusé comme tout fichier abîmé (`ValueError`, message clair),
  y compris quand le chargement est appelé hors de l'interface.
- Supprimer ou enregistrer un preset d'effets ou de transition n'ajoute plus d'étape « Annuler » qui ne défaisait
  rien, et ne fait plus perdre « Rétablir » : une modification annulée juste avant peut toujours être rétablie.
- Marquer ou démarquer une transition comme favorite l'annonce (« … ajoutée aux favoris », « … retirée des
  favoris ») au lieu d'afficher « Transition ajoutée. » alors qu'aucune transition n'était posée.
- Sous Windows, *Supprimer le proxy* (bibliothèque) ne laisse plus de fichier derrière lui quand un antivirus ou
  l'indexation tient un instant le fichier tout juste écrit : la suppression est réessayée, et un fichier qui reste
  verrouillé est signalé dans le journal de diagnostic au lieu de passer pour supprimé.
- Fermer la fenêtre pendant qu'un segment d'aperçu fidèle se calcule ne peut plus faire planter l'application : la
  fermeture annule le rendu et attend la fin de son thread avant de détruire la fenêtre.

## [0.2.0] - 2026-10-06

« Social Night » : monter une vidéo verticale de réseau social dans l'application, sans code.

### Added

- Assistant *Nouveau projet réseaux sociaux* (9:16, 4:5, 1:1 ; 30 ou 60 i/s) avec galerie de templates, zones
  TikTok / Reels / Shorts, cadrage « remplir » avec pan animé et Ken Burns.
- Grille rythmique : BPM saisi, tapé ou détecté dans la musique, temps sur la règle, magnétisme, *couper sur les
  temps* et *répartir sur la grille*.
- Texte façon TikTok : contour extérieur, emojis couleur, animations pop-in / glissé / rebond, mot par mot, karaoké
  synchronisé sur la voix, mots en couleur ; polices Anton et Saira ExtraCondensed embarquées (OFL).
- Effets de lumière identiques en aperçu, à l'export et sur le moniteur GPU (néon, light leaks, flare, lignes de
  vitesse, traînées, étincelles, flash, grain, bloom, aberration chromatique, heat haze), zoom d'impact, secousse,
  impact sur chaque cut ; presets *Night Look* et *Neon Rush*.
- Templates *Night* : Night Race, City Lights, Classement, CTA commentaires ; un emplacement se remplit en y déposant
  une vidéo.
- Bibliothèque d'effets sonores synthétisés, libres de droits, un effet sur chaque cut, et presets de ducking.
- Presets d'export TikTok, TikTok 60 i/s, Instagram Reels, YouTube Shorts, Instagram 4:5 et Carré 1:1, avec
  normalisation à −14 LUFS, copie d'aperçu de moins de 30 Mo et image de couverture.

### Fixed

- Un projet qui utilise le ducking ou une automation de volume de piste ne fait plus échouer l'export ni l'aperçu
  fidèle ; la profondeur de ducking réglée est respectée et l'attaque / le relâchement ont leur vraie durée.

## [0.1.0] - 2026-10-05

Première version publiée, avec des paquets pour macOS (Apple Silicon), Windows (x64) et Linux (x86-64, glibc 2.39 ou
plus récente). Les paquets ne sont pas signés (macOS : non notarisé) ; FFmpeg et ffprobe s'installent à part.

### Added

- Bibliothèque de projet, timeline multipiste (outils lame, roll, slip et slide, magnétisme, montage ripple,
  marqueurs, annuler / rétablir) et moniteur d'aperçu fidèle par FFmpeg, avec cache de rendu.
- Inspecteur (clip, couleur, effets, audio, graphiques, compositing), étalonnage avec import de LUT `.cube` et scopes
  (forme d'onde, histogramme, vectorscope).
- Compositing non destructif : masques, chroma key, modes de fusion identiques en aperçu et à l'export.
- Animation par images-clés (maintien, linéaire, ease, Bézier) avec Graph Editor.
- Motion graphics : calques de texte, formes, aplats et images, groupes, nulls, calques d'effets, parentage.
- Audio : mixeur, bibliothèque d'effets audio avec favoris et presets personnels, enregistrement de voix off.
- Titres stylés, presets de texte, import et export de sous-titres SRT.
- Tracking 2D et stabilisation, séquences multiples et imbriquées, Multicam (synchronisation, montage en direct avec
  les touches 1 à 9) et remappage temporel (courbe de vitesse, mélange d'images, flux optique).
- Export MP4 / MOV (H.264, ProRes) avec presets et file de rendu persistante ; encodage et décodage matériels quand
  FFmpeg les prend en charge, avec repli sur le processeur ; aperçu GPU (Metal, Direct3D 11, OpenGL).
- Couche de performance : proxies, cache unifié avec budget disque, qualité d'aperçu *Auto*.
- Espace de travail à panneaux, thèmes sombre et clair, interface en français, anglais et espagnol, raccourcis
  clavier configurables.
- Projets `.kut` avec enregistrement automatique et lecture des anciennes versions du format ; journal de diagnostic.
- Recherche de mises à jour sur GitHub Releases (*Aide › Rechercher des mises à jour…*), téléchargement vérifié
  (taille et SHA-256) et installation assistée.

[Unreleased]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.0...v0.2.5
[0.2.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Kyyzen0/Kut-Studio/releases/tag/v0.1.0

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

- Au démarrage, la barre d'état prévient quand l'aperçu GPU reste suspendu après un plantage, et indique le réglage
  qui le rétablit (*Préférences › Performance › Matériel › Rendu de l'aperçu › GPU*) ; le diagnostic matériel le
  nomme en toutes lettres.
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
  qui ne s'annule pas…) laissent désormais une trace dans le journal de diagnostic au lieu de passer inaperçus.
- L'édition de l'automation de volume de piste n'est plus présentée comme disponible : l'interface ne l'expose pas
  encore. Elle est inscrite à la feuille de route ; le ducking des presets réseaux sociaux reste disponible.
- Python 3.11 est annoncé comme version minimale pour lancer Kut-Studio depuis les sources : la documentation
  indiquait 3.10, avec lequel l'application ne démarre pas.

### Fixed

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

[Unreleased]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Kyyzen0/Kut-Studio/releases/tag/v0.1.0

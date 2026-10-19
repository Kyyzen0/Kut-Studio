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

- Les scripts savent monter : rogner, couper (la moitié droite se cite ensuite), dupliquer, glisser, déplacer d'une piste à l'autre, supprimer en refermant le trou, gérer les pistes (supprimer, renommer, ordonner, fader et panoramique) et les repères, avec les mêmes refus que l'interface.

### Fixed

- Six transitions (pixellisation, radiale, cercle qui s'ouvre, cercle qui se ferme, lissé gauche et droite) s'exportent enfin avec leur propre effet : elles étaient remplacées en silence par un simple fondu enchaîné.

## [0.7.0] - 2026-10-19

Version qui permet d'**animer les effets avec des images clés** : la luminosité, le contraste et la saturation de la correction de couleur, et l'intensité de la vignette ont leurs losanges ‹ ◆ ›, comme le mouvement, avec l'éditeur de courbes. Les scripts gagnent des verbes pour les images clés de masque et de calque graphique et pour les effets de clip. Deux correctifs s'y ajoutent : la luminosité négative de l'aperçu suit l'export, et annuler un export sous Windows ne laisse plus de fichier caché.

**À savoir** : c'est une version « mineure » parce qu'elle change le **format des projets**. Un projet qui contient une courbe d'effet s'enregistre au format 20 et ne s'ouvre plus dans une version antérieure (qui le refuse au lieu de perdre l'animation) ; un projet sans courbe d'effet reste écrit exactement comme avant et s'ouvre partout. Seuls ces quatre réglages sont animables pour l'instant : le flou, la netteté, la lueur, l'aberration chromatique et la brume de chaleur restent fixes.

### Added

- **Animer la correction de couleur et la vignette avec des images clés.** Dans l'onglet Effets de l'inspecteur, la luminosité, le contraste et la saturation de la *Correction couleur*, et l'intensité de la *Vignette*, ont comme Mouvement leurs losanges ‹ ◆ › : le losange pose ou retire une image clé à la tête de lecture, ‹ et › sautent d'une image clé à l'autre, et le curseur montre la valeur à la position de lecture (le modifier écrit l'image clé de cet instant). Chaque modification est annulable, deux effets du même type ont chacun leurs courbes, et une piste ou un calque verrouillé refuse la modification. Le clic droit sur le losange propose « Supprimer l'animation de l'effet » (en Avancé : interpolation, copier / coller et éditeur de courbes, qui liste ces réglages sous « Correction couleur — Luminosité »). Les losanges sont du niveau Studio. Voir [docs/animation.md](docs/animation.md).

  **À savoir** : les autres réglages d'effets (flou, netteté, lueur, aberration chromatique, brume de chaleur) ne sont pas encore animables. Un clip dont un effet est animé ne peut pas être converti en composition (la composition rendrait l'effet à sa valeur de base) : retirez d'abord l'animation de l'effet par le clic droit sur le losange. Un projet qui contient une courbe d'effet s'enregistre au format 20 et ne s'ouvre plus dans une version antérieure de Kut-Studio ; sans courbe, le fichier reste identique à avant.
- **Scripts : animer plus de propriétés, ajouter et régler des effets.** Un script peut poser des images clés sur les propriétés des calques graphiques (taille, contour, police, ombre, lueur…), le cadrage `pan_x` / `pan_y` et les masques de compositing, et ajouter, retirer, activer, régler et réordonner les effets visuels d'un clip vidéo (`add_effect`, `update_effect`…) avec les mêmes bornes, verrous et genres de piste que l'interface. Voir [docs/scripting.md](docs/scripting.md).

### Fixed

- **Aperçu : la luminosité de la correction de couleur suit l'export.** Le moniteur s'écartait de 2 à 3 niveaux de l'export à luminosité négative (par exemple −0,1 à −0,4), à cause de l'arrondi en float32 que fait le filtre `eq` d'FFmpeg ; il le reproduit maintenant.
- **Windows : annuler un export ne laisse plus de fichier temporaire caché.** Avec un FFmpeg installé par Chocolatey (un lanceur qui démarre le vrai FFmpeg), l'annulation n'arrêtait que le lanceur ; le vrai FFmpeg continuait d'écrire et gardait le fichier `.partial` verrouillé. L'annulation arrête maintenant tout l'arbre de processus et la suppression patiente jusqu'à 10 s.

## [0.6.5] - 2026-10-18

Version qui **termine les niveaux d'usage** commencés en 0.6.4 : les menus, le rail de la bibliothèque, la barre de la timeline, les pages, les menus du clic droit, le panneau Calques et la courbe de volume suivent maintenant le niveau Simple, Studio ou Avancé choisi, avec en plus deux correctifs : le clic droit dans la zone vide du panneau Calques et la reprise simultanée d'un verrou de projet périmé sous Windows.

**À savoir** : une installation neuve démarre au niveau **Simple**, qui cache davantage de menus que la 0.6.4. Si une fonction semble manquer, elle n'est pas supprimée : choisissez Studio ou Avancé dans Préférences, dans le menu Fenêtre ou dans la barre du haut. Les raccourcis clavier restent actifs à tous les niveaux, même quand l'entrée de menu est cachée. Un utilisateur qui avait déjà des préférences reste au niveau **Avancé** et retrouve exactement ses menus.

### Changed

- **Les menus, le rail de la bibliothèque, la barre de la timeline et les menus du clic droit suivent le niveau d'usage.** En Simple, les menus ne gardent que l'essentiel (fichier, annuler/rétablir, dupliquer, supprimer, couper, pistes, texte, réseaux sociaux de base, pages Montage et Audio) ; Studio y ajoute les calques (formes, groupes, repères, alignement), la grille rythmique, l'impact, la page Couleur (aussi dans la barre du haut ; Composition reste en Avancé), les scopes et la vitesse du clic droit, avec les outils roll, slip, slide et l'enregistrement dans la barre de la timeline ; Avancé montre tout, comme avant. Si la page ou la section du rail affichée disparaît en descendant de niveau, l'interface revient sur Montage ou Médias sans toucher au projet. Une entrée absente du menu reste utilisable par son **raccourci clavier, qui répond à tous les niveaux**, et en passant au niveau supérieur ; rien n'est grisé ni réinitialisé, et les pistes et calques verrouillés le restent à tous les niveaux. Voir [docs/usage-levels.md](docs/usage-levels.md).
- **Le panneau Calques et la courbe de volume d'une piste audio suivent aussi le niveau d'usage.** En Simple, le clic droit sur un calque ne propose que renommer, dupliquer et supprimer, et « Ajouter » que texte, aplat de couleur et image ; Studio y ajoute grouper, copier / coller les attributs, préréglage, formes, contrôleur nul et calque d'effets (et les boutons « Grouper » et « Préréglages » de la barre du panneau) ; Avancé ajoute le parent et le flou de mouvement. Sur la courbe de volume, « Supprimer le point » et « Effacer la courbe » restent partout ; « Ajouter un point ici » et « Remettre à 0 dB » sont en Avancé. Les dossiers de la bibliothèque et les éditeurs de nœuds Couleur et Composition ne changent pas. Les raccourcis restent actifs à tous les niveaux.

### Fixed

- **Clic droit dans la zone vide du panneau Calques** : le menu « Ajouter » s'ouvre de nouveau (il échouait sur une erreur interne).
- **Deux instances qui reprennent en même temps le verrou périmé d'un projet ne plantent plus sous Windows.** L'une attend son tour ; si la garde de reprise reste prise, elle refuse proprement avec le message habituel au lieu d'une erreur brute.

## [0.6.4] - 2026-10-17

Version qui **rapproche les couleurs de la version App Store** et **rend l'inspecteur sensible au niveau d'usage** : la chrominance des vidéos 4:2:0 est interpolée comme le fait macOS (sur une vidéo HEVC 10 bits en 4K, de 90 % à 0 % des pixels qui s'écartent de plus d'un niveau du rendu App Store), l'inspecteur n'affiche que l'essentiel en Simple, davantage en Studio et tout en Avancé, et quatre correctifs de robustesse et de sécurité (presets, sous-titres ASS, protocoles FFmpeg, timeline) s'y ajoutent.

**À savoir** : l'export de vidéos 4:2:0 demande un peu plus de CPU (environ 18 ms par image 1080p, mesuré sur Mac avec FFmpeg 9.0.2 ; non mesuré sur Windows et Linux), et les aperçus fidèles déjà calculés sont recalculés. Une installation neuve démarre au niveau **Simple** ; un utilisateur qui avait déjà des préférences reste en **Avancé** et voit tous les contrôles, mais quelques-uns changent de place dans l'inspecteur (détail ci-dessous) ; rien n'est retiré ni réinitialisé. Les menus, les raccourcis et les rails ne changent pas encore selon le niveau.

### Changed

- **L'inspecteur suit le niveau d'usage (Simple, Studio, Avancé).** En Simple, l'inspecteur n'affiche que l'essentiel (clip, valeurs de mouvement, « Remplir le cadre », recadrage, gain et fondus, sous-titre et calque graphique de base) ; Studio y ajoute étalonnage, vitesse, effets, transitions, images-clés, fusion, masques simples et clé chroma ; Avancé montre tout. Les groupes d'un niveau supérieur se rouvrent d'un clic avec « Afficher plus d'options » en bas de l'inspecteur, et chaque groupe replie ses réglages d'un niveau supérieur derrière un pliant « Avancé », placé sous les réglages de base. Rien n'est retiré ni réinitialisé : un réglage caché garde sa valeur, et les pistes et calques verrouillés le restent à tous les niveaux. Pour que les réglages de base restent au-dessus du pliant, quelques contrôles changent de place : les losanges d'images-clés sont regroupés en bas de Mouvement (« Réinitialiser » passe avant l'alignement et la transformation avancée), le panoramique passe après les fondus, la section « Temps » clôt Vitesse, « Réinitialiser » précède les courbes d'étalonnage, le recadrage précède la fusion, et les blocs du sous-titre et du calque graphique sont réordonnés. Les menus, les raccourcis et les rails ne changent pas encore.
- **Couleurs plus proches de la version App Store.** La chrominance des vidéos 4:2:0 (la plupart des fichiers H.264 et HEVC) n'est plus recopiée par blocs de 2×2 pixels : elle est interpolée à partir du coin haut gauche de chaque bloc, comme le fait macOS. Sur une vidéo HEVC 10 bits en 4K, le nombre de pixels qui s'écartent de plus d'un niveau du rendu de la version App Store passe de 90 % à 0 % (de 21 % à 10 % en H.264 8 bits, le plancher du système). L'aperçu rapide et l'export suivent la même règle. Les bandes noires d'une vidéo mise au cadre sont maintenant d'un nombre pair de pixels (un pixel de moins ou de plus), ce qui évite un décalage de couleur au bord de l'image. Les aperçus fidèles déjà calculés sont recalculés. La conversion passe par un format intermédiaire de 16 bits, qui évite une erreur de couleur de swscale (FFmpeg 6.1 et 7.1) : elle coûte environ 18 ms de CPU de plus par image 1080p (mesuré sur Mac avec FFmpeg 9.0.2, non mesuré sur Windows et Linux).

### Fixed

- Presets : l'enregistrement d'un preset d'effet ou de transition ne plante plus sous PySide6 6.11 (`QDialog.Accepted` sur la classe).
- Sous-titres ASS : le texte d'un `.kut` / SRT forgé ne peut plus injecter de balises d'override (`{…}` échappées, antislashs littéraux préservés) ; `font_family` ne décale plus les colonnes `Style:`.
- Export FFmpeg : les chemins média / LUT issus du projet refusent les protocoles non locaux (http, rtsp, lavfi, concatf…); `-protocol_whitelist` est répété devant chaque `-i` (option d'entrée FFmpeg).
- Timeline : un clip dont le média a disparu est ignoré (hors ligne) par `evaluate_timeline`, comme l'index et le plan de rendu, au lieu de lever une `KeyError`.
- Site : `script.js` n'écrit plus via `innerHTML` ; la CSP coupe `connect-src` (aucun fetch).

## [0.6.3] - 2026-10-16

Version de **correctifs** : l'aperçu du moniteur concorde de nouveau avec l'export quand une matrice de couleur est forcée, sept défauts d'interface relevés lors d'un test réel sur un Mac sont corrigés (molette qui modifiait les champs, saisie « 0,5 », groupe qui se repliait, et d'autres), et la recherche de FFmpeg est plus robuste. Elle ajoute aussi le **réglage du niveau d'usage** (Simple / Studio / Avancé), qui ne masque encore rien.

**À savoir** : la correction de couleur complète celle de la 0.6.2. Si vous aviez forcé BT.601 ou BT.709 sur un média, l'aperçu fidèle (le segment mis en cache) lui appliquait une seconde conversion et s'écartait de l'export de plusieurs dizaines de niveaux ; l'export, lui, était juste. Le niveau d'usage se règle dans Préférences, dans le menu Fenêtre ou dans la barre du haut (la puce s'abrège, puis disparaît, quand la barre manque de place) ; il n'a pas encore d'effet sur l'interface. Si FFmpeg était « introuvable » à l'export alors qu'il est installé, le journal de démarrage consigne désormais la recherche : joignez-le à votre signalement.


### Added

- **Niveau d'usage** : Simple / Studio / Avancé, choisis dans Préférences, dans le menu Fenêtre ou dans la barre du haut ; il règle ce qui est affiché (l'inspecteur est sensible au niveau ; les menus et raccourcis suivront). Voir [docs/usage-levels.md](docs/usage-levels.md).

### Fixed

- Projet : le dialogue « Enregistrer les modifications ? » nomme un projet jamais nommé « Mon montage », comme la barre du haut (il affichait « Projet sans titre »).
- Moniteur : supprimer des clips (ou le dernier) efface aussi le sous-titre ou le titre du clip supprimé ; il restait affiché à côté de « Aucun clip » jusqu'au prochain import.
- Bibliothèque : un média posé sur la timeline (bouton « Ajouter à la timeline », duplication, suppression, annulation) n'est plus affiché « inutilisé » : le compteur d'utilisations suit les clips.
- Inspecteur : une valeur tapée dans un champ du groupe Mouvement (« 0,5 » dans l'opacité) n'est plus réécrite à chaque frappe (elle donnait « 0,00 ») ; elle est prise à Entrée ou en quittant le champ, et le point vaut la virgule.
- Inspecteur : valider un champ avec Entrée ne replie plus le groupe qui le contient (Mouvement, par exemple) ; seul l'en-tête, atteint au clavier, se replie avec Entrée ou Espace.
- Inspecteur : faire défiler le panneau à la molette ou au trackpad ne modifie plus un champ survolé (nombre, curseur, liste déroulante) ; un champ ne réagit à la molette que s'il a le focus.
- **FFmpeg** : repli sur les dossiers Homebrew / MacPorts si le `PATH` ne le trouve pas, et le journal de démarrage consigne la recherche.
- Couleur : l'aperçu fidèle (segment en cache) n'applique plus une seconde fois la matrice du média : il concorde de nouveau avec l'export, y compris quand BT.601 est forcé et quand le segment est réduit (toujours lu en BT.709).

### Changed

- Thème sombre « Contraste+ » : textes secondaires, bordures et repères plus lisibles, fonds graphite, accent turquoise plus vif et tête de lecture corail (distincte de l'accent).

## [0.6.2] - 2026-10-15

Version de **couleur, de scripts et de verrous** : un média HD sans espace colorimétrique se lit enfin en BT.709 (avec un choix par média pour garder l'ancien rendu),
le mode script sait créer et modifier les masques d'un clip, et les derniers trous des verrous de piste et de calque sont fermés.


**À savoir** : les médias vidéo **HD non étiquetés** (720 lignes ou 1280 colonnes et plus, sans espace colorimétrique dans le fichier : captures d'écran, certaines
caméras ou exports anciens) étaient lus en BT.601 ; ils sont désormais lus en **BT.709**, comme le fait le reste de l'industrie. Leurs couleurs changent donc
légèrement dans le moniteur, l'aperçu fidèle, l'export et les miniatures : environ 1 niveau en moyenne sur une image naturelle (1,1 mesuré), jusqu'à une
vingtaine sur les couleurs très saturées. Les médias **étiquetés** et les médias **SD non étiquetés** ne changent pas. Pour retrouver l'ancien rendu d'un
média : bibliothèque › clic droit sur le média › *Espace colorimétrique* › *BT.601* (ou le verbe de script `set_media_color_matrix`). Les segments d'aperçu
fidèle déjà rendus sont refaits une fois. Sans GPU (moniteur CPU), la lecture garde la lecture des couleurs de la vidéo Qt ; la matrice voulue s'y voit en pause, une fois le segment fidèle rendu.

### Added

- **Scripts** : les verbes `add_mask`, `set_mask`, `set_mask_points` et `remove_mask` créent, modifient et retirent les masques de compositing d'un clip (rectangle, ellipse, polygone, courbe de Bézier ; mode, inversion, adoucissement, dilatation, opacité), avec le même résultat que l'inspecteur Compositing, les mêmes refus (piste ou calque verrouillé, piste sans image, recadrage réservé à `set_crop`, valeurs hors bornes jamais ramenées) et une seule étape d'annulation.
- **Espace colorimétrique d'un média** : *Auto* (défaut), *BT.601* ou *BT.709*, dans le menu du média de la bibliothèque, annulable d'un pas. Il force la lecture
  des couleurs de **tous** les clips du média, étiquette comprise ; il est refusé (grisé, avec la raison) si l'un d'eux est sur une piste ou un calque verrouillé.
  Un projet qui en utilise un s'écrit en version 19 du format `.kut` (un ancien Kut-Studio le refuse au lieu de changer les couleurs sans le dire) ; les autres
  projets s'écrivent exactement comme avant.
- **Scripts** : le verbe `set_media_color_matrix` (`asset` ou `clip`, `matrix` : `auto`, `bt601`, `bt709`), mêmes refus que l'interface, une seule étape d'annulation.
  Voir [docs/scripting.md](docs/scripting.md).

### Changed

- **Couleur** : un flux vidéo non étiqueté de 720 lignes ou 1280 colonnes et plus est lu en BT.709 (BT.601 en dessous, plage limitée), au lieu de BT.601 partout. Une
  seule règle (`core/color_matrix.py`) pour le moniteur GPU, l'aperçu fidèle, l'export, les miniatures, les images préparées du remappage, la pipette, les flux
  du moniteur Multicam et les LUT d'étalonnage cuites ; vérifiée au niveau près entre la référence du moniteur, l'export réel et la miniature
  (`tests/test_color_matrix_parity.py`). Les caches (aperçu fidèle, miniatures, LUT) ne resservent rien de rendu avec l'ancienne règle.

### Fixed

- **Verrous** : une piste ou un calque verrouillé (et un clip d'un autre genre de piste) refuse désormais aussi, sans rien écrire ni toucher à l'historique, les
  commandes « Remplir le cadre », « Recadrer en suivant le tracker », Ken Burns, zoom d'impact, secousse et « Impact sur chaque cut » du menu Réseaux sociaux,
  les modèles de texte et la saisie, le style et la remise à zéro d'un sous-titre, la pose, la modification et le retrait d'une transition, et l'ajout d'un
  sous-titre sur une piste verrouillée. Les images-clés et la transformation d'un clip sont gardées dans le cœur, donc aussi pour les scripts. Annuler et
  rétablir fonctionnent comme avant. La liste des entrées gardées est dans [docs/lock-guards.md](docs/lock-guards.md).

## [0.6.1] - 2026-10-14

Version de **correctifs et d'ouverture au mode script** : le moniteur affiche l'image en pause après une édition, le mode script sait poser des images fixes
et dessiner des fenêtres d'étalonnage en courbe de Bézier, et la prise de verrou d'un projet ne plante plus sous Windows quand deux processus se croisent.

**À savoir** : un calque d'image posé par script (`add_image_layer`) est un calque graphique, donc au-dessus de toute la vidéo ; il n'a ni transitions
entre clips vidéo ni remappage de temps. Les masques de compositing d'un **clip** n'ont toujours pas de verbe de script ; seules les fenêtres d'étalonnage
en courbe en ont (`add_color_window`, `set_color_window_points`).

**Réserve** : la correction de l'image en pause (moniteur noir après une édition) est vérifiée avec un vrai lecteur jusqu'à la sortie du
lecteur vidéo, mais **pas encore dans la fenêtre réelle sous Metal (macOS)**. Si le moniteur reste noir après une édition, Lecture puis Pause
le fait revenir, et un signalement est le bienvenu.

### Added

- **Scripts : images fixes.** Le verbe `add_image_layer` pose une image (PNG, JPEG, WebP, BMP, TIFF du dossier du script, lecture seule, dimensions et poids bornés, format vérifié sur le contenu) en **calque graphique** de durée donnée. C'est un calque : il passe au-dessus de toute la vidéo, sans transitions entre clips vidéo ni remappage de temps. Voir [docs/scripting.md](docs/scripting.md).
- **Scripts** : les fenêtres d'étalonnage en courbe de Bézier (`add_color_window` avec `shape: "path"` et `nodes`, nouveau verbe `set_color_window_points`) ; valeurs hors bornes refusées, jamais ramenées, une seule étape d'annulation.

### Fixed

- Moniteur : l'image s'affiche tout de suite après une édition ou un clic sur la règle, sans devoir passer par Lecture puis Pause.
- Windows : ouvrir un projet pendant qu'un autre processus supprime ou remplace son fichier de verrou ne fait plus planter la prise de verrou ; la lecture est retentée un court instant, puis le projet est refusé proprement si l'état reste indéterminé.

### Changed

- CI Windows : l'installation de FFmpeg réessaie Chocolatey puis se replie sur un build figé dont le SHA-256 est vérifié (`docs/ci-ffmpeg-windows.md`).

## [0.6.0] - 2026-10-14

Cette version rend le **moniteur beaucoup plus direct** : on y sélectionne n'importe quel clip d'un clic, on le déplace, le redimensionne
et le fait pivoter avec accrochage, on l'aligne sur le cadre, on le recadre et on dessine des masques, courbes de Bézier comprises. Le
**mode script** couvre maintenant l'essentiel du montage (65 verbes : séquences imbriquées, multicam, étalonnage avancé, recadrage), et
l'inspecteur respecte enfin les verrous de piste et de calque.

**À savoir** : un projet qui contient un **masque en courbe de Bézier** s'enregistre en **format 18** et ne s'ouvre plus dans une version
antérieure à la 0.6.0 (elle le refuse avec un message clair, sans perdre la courbe) ; tous les autres projets restent au format 17,
octet pour octet comme avant. Quand le moniteur a le focus et qu'un calque éditable est sélectionné, les flèches **Gauche / Droite** le
déplacent d'un pixel au lieu de changer d'image (sans calque éditable, ou avec Ctrl / Alt, elles gardent leur rôle).

### Added

- **Recadrage non destructif.** Inspecteur → Compositing : quatre champs (gauche, haut, droite, bas, en %) et « Réinitialiser le recadrage » (aussi dans Calques). Le recadrage est un masque rectangle « Recadrage » rangé en dernier de la pile : modifiable ensuite, identique à l'aperçu et à l'export, et les verbes de script `set_crop` / `clear_crop` le pilotent.
- **Moniteur : poignées de masque.** Un masque choisi dans la pile de compositing se règle directement dans le moniteur (déplacer, redimensionner, pivoter, adoucir, sommets d'un polygone) ; image-clé à la tête de lecture si la propriété est animée, une seule étape d'historique par geste. Voir [docs/motion-graphics.md](docs/motion-graphics.md).
- **Masque en courbe de Bézier.** Inspecteur → Compositing → forme *Courbe* : un contour fermé de 3 à 128 nœuds à tangentes, rendu à l'identique dans l'aperçu et à l'export. Dans le moniteur : glisser nœuds et tangentes (Alt : tangente seule), double-clic sur le contour pour ajouter un nœud, sur un nœud pour le lisser ou l'angulariser, Alt + clic ou Suppr pour le retirer. Les nœuds ne s'animent pas ; position, taille, rotation, adoucissement et opacité restent animables. Les projets sans courbe s'enregistrent comme avant (format 17) ; un projet qui en contient une passe au format 18, qu'une version plus ancienne de Kut-Studio refuse d'ouvrir au lieu de perdre la courbe.
- **Moniteur : aimantation à l'échelle et à la rotation.** Redimensionner un calque l'accroche aux bords du cadre, aux guides et aux autres calques ; la rotation s'accroche aux multiples de 45° (Alt désactive, Maj garde le pas de 15°).
- **Moniteur : alignement sur le cadre et flèches.** Le clip sélectionné s'aligne sur un bord ou le centre du cadre (six boutons dans l'inspecteur, section Mouvement, ou Calques → *Aligner sur le cadre*), et les flèches du clavier le déplacent d'un pixel (Maj : dix) quand le moniteur a le focus.
- **Scripts déclaratifs.** `Kut-Studio apply script.json projet.kut -o resultat.kut` applique un fichier JSON d'opérations (bus, inserts,
  routage, clips, repères) **en tout ou rien**, en une étape d'historique, sans jamais exécuter de code. `--dry-run` rend le rapport sans
  rien écrire ; `validate script.json` liste toutes les erreurs d'un coup ; `Kut-Studio schema` écrit le schéma JSON. Le projet d'origine
  n'est jamais écrasé sans `--in-place`. Voir [docs/scripting.md](docs/scripting.md).
- **Scripts : médias, audio de piste, graphiques.** Dix verbes de plus (25 en tout) : `import_media` (lecture seule, **dans le dossier du
  script** : en sortir demande `--allow-outside`, liens symboliques résolus), les inserts de piste (dont l'égaliseur paramétrique),
  couper / verrouiller / masquer une piste, et les calques graphiques (`add_graphic`, `set_graphic`).
- **Un projet ouvert n'est plus réécrit par un script.** L'application pose un verrou (`nom.kut.lock`) tant qu'un projet est ouvert ;
  `apply --in-place` (ou `--overwrite`) refuse d'écrire dans un projet tenu par un autre, dit qui le tient et quel fichier supprimer si
  cette utilisation n'existe plus. Un verrou n'est repris que si son propriétaire est prouvé disparu (un numéro de processus réutilisé
  ne passe pas pour l'ancien). La fenêtre prévient si le projet est déjà tenu ailleurs, sans jamais refuser de l'ouvrir.
- **Scripts : pistes, sous-titres, transitions, images clés, étalonnage.** Huit verbes de plus (33 en tout). Un script ne reçoit jamais
  une valeur ramenée en silence (bornes du modèle, nom de piste déjà pris, instant hors du clip) et le verrou d'une piste est respecté.
- **Scripts : étalonnage avancé.** Courbes, roues, LUT ``.cube``, nœuds (série / parallèle / calques), flou et netteté : douze verbes de plus
  (45 en tout). ``set_color_grade`` / ``reset_color_grade`` acceptent un ``node`` ; ``import_lut`` lit un fichier dans le dossier du script
  comme ``import_media``. Voir [docs/scripting.md](docs/scripting.md).
- **Scripts : fenêtres et qualifieur.** Cinq verbes de plus (50 en tout) : ``add/set/remove_color_window``, ``set/clear_color_qualifier``.
  Les bornes d'une fenêtre sont celles du modèle (rien n'est ramené en silence) ; un qualifieur avec bas > haut est refusé.
- **Scripts : séquences imbriquées.** Sept verbes de plus (55 en tout) : créer, renommer, reformater, dupliquer et supprimer une séquence,
  l'insérer comme clip imbriqué, et `set_active_sequence` pour agir sur une autre séquence. Cycles, profondeur, pistes verrouillées ou
  d'un mauvais genre et durées hors de la source sont refusés comme dans l'interface.
- **Moniteur : un clic sélectionne n'importe quel clip.** Clips vidéo, images fixes et clips de composition se sélectionnent d'un clic dans le moniteur (jusqu'ici seuls les calques graphiques l'étaient), dans l'ordre du rendu ; sur un clip déjà sélectionné, un calque posé au-dessus reste cliquable. La tête de lecture ne bouge pas.
- **Scripts : Multicam.** Huit verbes de plus (63 en tout) : créer une source depuis des médias, l'insérer, basculer ou remplacer l'angle d'un
  segment, régler la politique audio, renommer, ajouter et recaler un angle. Un angle se désigne par `angle-N` ou son nom exact ; un décalage
  qui n'est pas relatif (le plus petit différent de 0), un nom d'angle déjà pris ou une piste verrouillée sont refusés.

### Fixed

- **Moniteur : un clic sélectionne bien un clip quand plusieurs clips ou calques se recouvrent.** Le défaut venait de la zone cliquable de la surcouche (là où un nombre pair de formes se superposaient, deux vidéos plein cadre par exemple, Qt ne livrait pas le clic), pas du choix du clip ; même correction pour les poignées de masque, de courbe et de suivi, y compris pour un masque polygonal ou une fenêtre dont les côtés se croisent.
- **Compositing : le verrou est respecté.** Sur une piste verrouillée, un calque graphique verrouillé ou un clip audio / sous-titres, le groupe Compositing de l'inspecteur (masques, mode de fusion, incrustation) est grisé et n'écrit plus rien dans le projet ; un message l'explique.
- **Piste ou calque verrouillé : le reste de l'inspecteur le respecte aussi.** Mouvement (curseurs, images-clés, « Réinitialiser le mouvement »), transformation avancée, copier / coller et menu des losanges, Vitesse (inspecteur, menu et raccourcis), Effets et propriétés du calque graphique refusent la modification (message dans la barre d'état, rien dans l'historique), leurs groupes sont désactivés, les poignées du moniteur ne bougent plus un calque verrouillé et les verbes de script `set_graphic`, `add_keyframe`, `set_crop`… refusent un calque verrouillé. L'éditeur de courbes devient en lecture seule (courbe lisible, champs désactivés, aucune écriture) et l'application d'un préréglage d'effets est refusée, bouton de la bibliothèque compris.
- **Aperçu GPU, pistes du dessous.** Un WebM ou un Matroska dont ffprobe marque la durée du flux ``N/A`` (la durée est sur le conteneur)
  s'ouvre à nouveau dans le décodeur des pistes vidéo sous le clip du dessus. Avant, la sonde levait une erreur et la piste restait vide
  au moniteur, alors que l'export, lui, la rendait.
- **Un projet ouvert par un lien symbolique ne remplace plus le lien à l'enregistrement.** L'écriture atomique renommait le fichier
  temporaire sur le lien lui-même : le projet visé ne changeait pas et le lien devenait un fichier ordinaire. L'enregistrement écrit
  maintenant à travers le lien.

## [0.5.5] - 2026-10-13

Une version de transition : la **ligne de commande** (`Kut-Studio export | validate | info | presets`, sans fenêtre) pose le socle du
futur mode script, et le **moteur d'aperçu GPU** passe son étape 2 (calques par contenu, plusieurs pistes vidéo, grain en shader).
Un export annulé sous Windows ne laisse plus de fichier caché.

**À savoir** : aucun changement de format de projet (toujours le format 17) ; l'application se lance et se comporte comme avant,
la ligne de commande est un ajout.

### Added

- **Ligne de commande, sans fenêtre.** `Kut-Studio info | validate | presets | export` (depuis les sources : `python main.py …`) : le
  même binaire que l'application. `export` écrit une vidéo avec exactement la commande FFmpeg de l'interface, dans un fichier caché
  renommé à la fin (jamais de MP4 tronqué), annulable par Ctrl-C, et ne remplace jamais son projet ni un fichier existant sans
  `--overwrite`. `validate` dit **ce qu'un projet a perdu en silence** (effets, bus, transitions invalides ignorés à l'ouverture).
  `--json` rend un objet unique, les codes de sortie sont stables, les messages sont traduits. Voir [docs/cli.md](docs/cli.md).

- **Décodage multi-sources (`media_io`).** Ouverture, saut et décodage d'une image (PyAV si disponible, sinon
  FFmpeg) pour alimenter plusieurs pistes sur le moniteur GPU (ADR-0001).
- **Compositeur CPU allégé.** Aperçu à résolution réduite via le même plan que le GPU, dans un fil de travail
  (`core/cpu_composite.py`), pour les machines sans GPU utilisable.
- **Grain procédural en shader.** Le grain de film du moniteur GPU peut être une passe `grain` plutôt qu'un
  PNG plein cadre (ADR-0001).

### Changed

- **Export / aperçu fidèle : calques en PNG par contenu.** Un titre ou une forme éligible n'est plus redessiné
  plein cadre à chaque pose : l'encre est rastérisée une fois (cache LRU `content_texture`), puis posée dans le
  cadre (ADR-0001, étape 2). Voir [docs/gpu-preview.md](docs/gpu-preview.md).
- **Moniteur GPU : plusieurs pistes vidéo.** Les pistes sous le clip du dessus sont composées en temps réel
  (décodage `media_io` hors thread UI) ; le clip supérieur reste le lecteur Qt.

### Fixed

- **Export annulé sous Windows : plus de fichier caché oublié.** FFmpeg arrêté garde un instant sa poignée sur le fichier en cours
  d'écriture ; sa suppression échouait sans rien dire et laissait un `.<nom>.….partial.mp4` à côté de la destination. La suppression
  réessaie maintenant jusqu'à ce que le fichier soit libéré, et le journal le dit si elle abandonne.
- **Revue Codex (PR #108 / #113).** Underlays en coords canvas ; décodage PyAV jusqu'au PTS (RVBA différé) ;
  file de relecture vidée ; géométrie CPU logique à scale réduit ; état compositing complet des sous-pistes ;
  grain procédural branché et cadré par matte ; jobs underlay obsolètes coalescés ; plus d'écriture
  `c-*.png` inutilisée.

## [0.5.0] - 2026-10-12

Le **mixage audio** gagne ses trois outils de console : des **inserts** par piste (une chaîne d'effets sur la somme
des clips), un **égaliseur 4 bandes** et des **bus** (sous-mixages entre les pistes et le Master), tous entendus en
lecture comme à l'export. Côté aperçu, Auto **mesure la machine** et se règle sur la **batterie**, le mode économie et
la chauffe, et les titres et formes du moniteur GPU deviennent des textures à la taille de leur contenu.

**À savoir** : sur batterie, Auto descend d'un cran de qualité d'aperçu et ne précharge plus que le segment sous la
tête (l'export n'est jamais dégradé). Les projets gardent le format 17 ; un projet sans insert ni bus s'exporte comme
avant.

### Added

- **Bus audio.** Un bus est un sous-mixage entre des pistes et le Master : bouton **Nouveau bus** du mixeur, sortie de
  chaque piste choisie au bas de sa tranche (le Master, ou un bus). Chaque bus a son fader, sa coupure, ses inserts
  (les mêmes effets que les pistes, égaliseur compris) et son vumètre ; la lecture l'entend comme l'export. Une piste
  sans bus sonne comme avant, supprimer un bus renvoie ses pistes au Master, et tout s'annule. Voir
  [docs/audio.md](docs/audio.md).
- **Égaliseur 4 bandes.** Un nouvel effet audio, sur un clip ou en insert de piste : un plateau grave, deux cloches
  (fréquence, gain, Q) et un plateau aigu, de −18 à +18 dB. Une bande à 0 dB ne coûte rien ; par défaut, +3 dB de
  présence à 3 kHz. Voir [docs/audio.md](docs/audio.md).
- **Inserts de piste.** Chaque piste audio porte une chaîne d'effets qui traite la **somme** de ses clips (bouton **FX**
  de la tranche du mixeur) : un compresseur ou un limiteur de piste voit tout ce que la piste fait entendre. Les
  effets sont ceux des clips ; on peut les réordonner, les contourner, les régler, et tout s'annule. Le vumètre de la
  piste mesure la sortie de la chaîne, la lecture l'entend comme l'export, et un projet sans insert s'exporte comme
  avant. Voir [docs/audio.md](docs/audio.md).

### Changed

- **Moniteur GPU : calques graphiques en textures par contenu.** Un titre ou une forme éligible n'est plus
  redessiné plein cadre à chaque image qui ne change que sa pose : la rastérisation produit une texture à la taille
  réelle du contenu, animée par matrice et opacité dans le shader (ADR-0001, étape 2). Groupes, masques et flou de
  mouvement multi-échantillons restent en repli plein cadre. Voir [docs/gpu-preview.md](docs/gpu-preview.md).
- **Performance Auto : profil machine mesuré.** Au premier démarrage (ou si le cache est froid), un banc court
  (composition CPU, décodage FFmpeg, rastérisation tampon) classe la machine en low / balanced / high et Auto utilise
  ce résultat à la place de la seule heuristique cœurs/RAM. Couper avec `KUT_STUDIO_MACHINE_BENCH=off`.
- **Régulateur d'aperçu (énergie + dégradation).** Auto descend d'un cran sur batterie, mode économie ou chauffe ;
  le préchargement des segments voisins est suspendu sur batterie (le segment sous la tête reste rendu) ; à l'arrêt, rien n'est recalculé si rien n'a
  changé. L'échelle (proxies, effets de voisinage en 1/2, résolution, préchargement) part du profil mesuré.
  L'export n'est jamais touché. Forçage : `KUT_STUDIO_POWER=battery,thermal=serious`.
- **Aperçu fidèle : un clip vidéo déjà commencé** n'est plus traité depuis le début du média dans un segment tardif
  (coupe avant effets / conformation, comme l'audio du mixage en direct).

## [0.4.2] - 2026-10-11

Un lot de robustesse : quatre réglages, poussés à leurs bornes avec le vrai FFmpeg, faisaient échouer l'export ou
donnaient un rendu faux. Aucun format de projet ne change (toujours le format 17), aucun rendu existant non plus
hors de ces cas limites.

### Fixed

- **Vitesse : une ancre hors de la source faisait échouer l'export** (division par zéro). Avant l'entrée dans la
  fenêtre du clip, la première image est maintenant tenue ; une ancre à la fin ou au-delà ne laisse qu'une image.
- **Export : une résolution libre à côté impair** (1081 × 607…) faisait refuser l'export par x264, x265 et ProRes. Elle est
  arrondie au pair supérieur, sans perdre de pixel ; le résumé du préréglage annonce la taille réellement exportée.
- **Courbes de couleur : deux points trop proches faisaient échouer l'export.** FFmpeg exige que les abscisses tombent
  dans des cases de 1/255 différentes (pas seulement distinctes) ; le point gênant est décalé dans la case suivante,
  la marche qu'il dessine est conservée. Les courbes de l'éditeur (16 points) ne changent pas.
- **Sous-titres : la marge au bord va jusqu'à 200 px**, comme le propose l'éditeur de style (le modèle la limitait
  à 80 px, si bien que 80 et 200 donnaient le même rendu). La marge interne du fond reste limitée à 80 px.

## [0.4.1] - 2026-10-11

Un lot de corrections trouvées en poussant chaque réglage à ses bornes avec le vrai FFmpeg : l'**exposition** de
l'étalonnage est enfin une exposition en diaphragmes, les **sous-titres** dessinent à l'export le fond et la position
réglés dans l'éditeur de style (et la couleur de leur ombre), et trois réglages qui faisaient échouer ou noircir
l'export sont réparés (incrustation à tolérance 0, transitions plus courtes qu'une demi-image, durées de vitesse
minuscules).

**À savoir** : le rendu change pour les projets qui utilisent l'exposition (surtout aux valeurs négatives, qui étaient
trop sombres) et pour les sous-titres dotés d'un décalage d'ombre : revoyez-les. Les projets gardent le format 17.

### Changed

- ⚠️ **L'exposition de l'étalonnage est maintenant une vraie exposition en diaphragmes (EV).** Elle multiplie la
  lumière par 2^EV, comme l'annonce le réglage (−2 à +2). Avant, c'était un gamma : un gris moyen sortait à 0 à −2 EV
  (il en faut 66), et une couleur dérivait vers le rouge dans les ombres. **Rendu différent** pour les projets dont un
  nœud ou un clip utilise l'exposition (surtout les valeurs négatives, qui étaient bien trop sombres) : revoyez-les.
  À 0 EV, rien ne change.

### Fixed

- **Incrustation (chroma key) : une tolérance à 0 faisait échouer l'export.** FFmpeg refuse une similarité de 0, et le
  curseur démarre à 0. La tolérance est maintenant d'au moins 0,01 (le curseur ne descend plus plus bas).
- **Transitions** : une transition de moins d'une demi-image donnait un export **noir** de la transition à la fin. La
  durée minimale est de 0,05 s (la bibliothèque de l'interface commence à 0,1 s ; une transition plus courte, venue
  d'un fichier modifié à la main, est ignorée à l'ouverture). Une transition refusée (durée trop courte, ou déjà une
  sur cette jonction) ne déplace plus le clip suivant avant de lever l'erreur.
- **Vitesse** : une durée imposée ou un rognage de moins de 0,1 ms faisait échouer l'export (FFmpeg lit certaines
  durées comme un temps et refuse la notation scientifique « 1e-06 »).
- **Sous-titres : le fond et la position réglés dans l'éditeur de style sont maintenant dessinés à l'export.** Le fond
  (couleur et opacité) et le décalage horizontal / vertical étaient ignorés par l'export : une boîte de fond opaque
  n'apparaissait pas, un décalage ne déplaçait rien. Le fond est une boîte sous le texte, avec les marges internes du
  style ; le décalage est un pourcentage du cadre (vers la droite, vers le bas). Le moniteur les respecte aussi : il
  posait toujours le sous-titre en bas au centre, sans tenir compte de l'alignement, des marges ni du décalage.
- ⚠️ La couleur d'**ombre** des sous-titres, réglée dans le style mais jamais utilisée (l'ombre était toujours
  transparente), s'applique maintenant : un style avec un décalage d'ombre affiche une ombre. Voir
  [docs/subtitle-styles.md](docs/subtitle-styles.md).

## [0.4.0] - 2026-10-11

La **composition par nœuds**, à la manière de Fusion dans DaVinci Resolve : un clip de composition calcule son image
par un graphe de nœuds (sources, transformation, masque, incrustation, effets, étalonnage, fusion), avec sa page
Composition et la conversion des calques en nœuds ; en lecture, le moniteur lit la composition rendue d'avance. La
**page Audio** fait entendre en lecture le vrai mixage de l'export, avec de vrais vumètres. Aussi : une faille de
sécurité corrigée (un projet piégé pouvait injecter des filtres FFmpeg), l'export qui échouait avec un
panoramique non centré ou l'un de quatre effets audio, l'écho et la réverbération enfin audibles, un gel possible quand
les scopes étaient affichés, et les couleurs du moniteur GPU sur les vidéos au grain coloré.

**À savoir** : les projets passent au format 17 ; une version plus ancienne de Kut-Studio ne les ouvre plus.

### Added

- **Composition par nœuds**, à la manière de Fusion dans DaVinci Resolve : un clip de composition calcule son image par
  un graphe de nœuds.
  - Sources : un média du projet, un texte, une couleur unie. Traitements : transformation, masque, incrustation,
    effets, étalonnage. Fusion du premier plan sur le fond, avec son mode et son opacité. Le son des médias suit.
  - **Page Composition** (bouton *Composition* en haut, *Fenêtre*, ou double-clic sur le clip violet) : les nœuds, les
    réglages du nœud choisi, et le viewer qui montre la sortie ou, à la demande, le nœud choisi. On relie les nœuds à
    la souris ; **+** ajoute un nœud après le nœud choisi (une source vient par-dessus par une fusion).
  - *Convertir en composition* (clic droit dans la timeline, ou *Séquence*) : les clips vidéo et calques choisis
    deviennent les nœuds d'un seul clip, avec la même image. *Nouvelle composition* en crée une vide.
  - L'export et l'aperçu à l'arrêt sont exacts. En lecture, le moniteur lit la composition rendue d'avance en
    arrière-plan (comme le cache Fusion de Resolve), avec les réglages du clip appliqués en direct ; le temps de ce
    rendu, après une modification, il montre la source principale.
  - Les projets passent au format 17 : une version plus ancienne de Kut-Studio ne les ouvre plus.
- **On entend le vrai mixage pendant la lecture** : les pistes audio (musique, voix off), le volume, le panoramique,
  mute et solo, l'automation, les effets, le ducking et le Master, exactement comme l'export les produira. Avant, la
  lecture ne faisait entendre que le son du clip affiché. Le son est calé sur la tête de lecture ; une modification
  pendant la lecture s'entend après un court repos.
- **Vumètres réels** dans le mixeur : la crête de chaque piste, la crête et le RMS du Master, et la sonie (LUFS)
  momentanée et à court terme. À l'arrêt, ils retombent.
- **Page Audio** (bouton *Audio* en haut, *Fenêtre*, ou le rail) : le mixeur et ses vumètres sous le moniteur, avec la
  timeline, l'inspecteur sur l'onglet Audio.
- Développement : un test bloqué en CI écrit la pile Python de tous ses threads dans le journal avant son délai, même
  quand un blocage natif tient le GIL ; dans le workflow de release, l'étape des tests échoue à 45 min au lieu de
  laisser le job s'épuiser à 75 min ([docs/updates.md](docs/updates.md)).

### Changed

- Dans la barre supérieure et le menu *Fenêtre*, les pages sont dans l'ordre **Montage, Composition, Couleur, Audio** :
  la page Composition passe avant la page Couleur.

### Fixed

- **Quatre effets audio faisaient échouer l'export** : *Amélioration de la voix*, *Réduction de bruit*, *Compresseur* et
  *Limiteur* envoyaient à FFmpeg des valeurs qu'il refuse (des décibels là où il attend une amplitude linéaire, un
  nombre de pôles hors de sa plage, une option qui n'existe pas). Tout projet qui les utilisait ne s'exportait pas.
  Ils sont corrigés, et chaque réglage est borné à ce que FFmpeg accepte
  (le plancher de bruit de la réduction de bruit ne dépasse plus −20 dB, la décroissance de l'écho reste au-dessus de
  zéro). *Amélioration de la voix* : l'intensité choisit entre deux pentes de coupure (douce ou raide).
- **L'écho et la réverbération n'avaient pas d'écho audible** : le retard était donné à FFmpeg en secondes là où il
  attend des millisecondes, donc 1 000 fois trop court (un écho de 300 ms était demandé à 0,3 ms). Ils font maintenant
  l'écho réglé. ⚠️ Rendu différent pour les projets qui utilisaient ces deux effets.
- **Un panoramique non centré faisait échouer l'export**, l'aperçu fidèle et la mesure de sonie : FFmpeg refusait le
  filtre envoyé pour une piste ou un clip non centré (présent depuis l'ajout du mixeur). Le panoramique est maintenant
  une balance à puissance constante : au centre rien ne change, le côté opposé s'éteint progressivement (−3 dB à
  mi-course, silence au bout), l'autre côté n'est jamais amplifié.
- Moniteur GPU : sur une vidéo au grain coloré, les couleurs différaient de l'export (jusqu'à 58 niveaux sur un
  pixel). Le moniteur reconstruit maintenant la couleur 4:2:0 comme l'export. À l'export, l'effet *Heat haze* ne
  lisse plus la couleur de la vidéo : elle reste la même que sans l'effet.
- **Un projet modifié ou venu d'ailleurs ne peut plus injecter de filtres FFmpeg.** Les identifiants des nœuds
  d'étalonnage et de composition, lus tels quels dans le fichier `.kut`, servaient d'étiquettes au graphe de l'export,
  de l'aperçu fidèle et des scopes : un identifiant forgé pouvait y ajouter un filtre (lecture d'un fichier local…).
  Un identifiant doit maintenant tenir en 64 caractères ASCII (lettres, chiffres, `_`) ; les nôtres (`n1`, `f2`…)
  n'ont pas changé. À l'ouverture, un nœud de composition qui ne les respecte pas est retiré avec ses liens, et une
  chaîne d'étalonnage est renumérotée sans perdre un réglage (un avertissement va au journal).
- Une composition dure au plus une heure : une durée démesurée dans un fichier faisait construire, à l'arrêt de la
  lecture, un morceau d'aperçu par tranche de 10 s, jusqu'à saturer la mémoire. La durée d'un fichier est ramenée à une
  heure, et la plage du clip qui la porte avec elle (coupée à la fin ; un clip tout entier après l'heure garde 40 ms,
  le projet s'ouvre) ; *Convertir en composition* et *Nouvelle composition* refusent plus long, au lieu de couper.
- L'application pouvait se figer pour de bon quand les scopes étaient affichés : l'objet qui prépare l'image analysée
  était détruit sur le fil d'analyse, où Qt et Python s'attendaient l'un l'autre. Très probablement aussi le blocage
  de 50 min des tests Windows de la release 0.3.0.
- Fermer l'application depuis la page Composition, ou avec l'inspecteur ou le viewer masqué ou détaché, n'écrit plus
  d'erreur dans le journal : ces panneaux étaient détruits avant les dernières étapes de la fermeture, qui s'en
  servent encore.

## [0.3.0] - 2026-10-10

La **page Couleur**, à la manière de DaVinci Resolve : nœuds d'étalonnage en série, en parallèle et en calques, roues
lift / gamma / gain / offset, qualificateur et sa pipette, fenêtres suivies par le tracking, flou et netteté,
avant / après, le tout en temps réel dans le moniteur GPU et exact à l'export. Aussi : un moteur de rendu jusqu'à
60 % plus rapide au premier export, le panneau Historique, l'export en plusieurs formats sociaux, la courbe de
volume dans la timeline, des photos dans les templates et des formes d'onde exactes.

**À savoir** : les réglages *Température* et *Teinte* sont devenus une vraie balance des blancs et une vraie rotation
des teintes ; un projet qui les utilisait s'affiche et s'exporte différemment (voir *Changed*).

### Added

- **Page Couleur**, à la manière de DaVinci Resolve : grand moniteur et scopes au centre, l'inspecteur à gauche, les
  nœuds et les roues à droite, la timeline en bas. On y passe par le bouton *Couleur* au centre de la barre
  supérieure, par le rail ou par *Fenêtre › Page Couleur* ; *Montage* ramène la disposition de montage. Chaque page
  garde la disposition qu'on lui laisse.
- **Nœuds d'étalonnage** : un clip s'étalonne en plusieurs nœuds en série, chacun avec tous les réglages (roues,
  exposition, courbes, LUT…). On ajoute un nœud après le nœud courant (Alt+S), on le contourne (Ctrl+D), on le nomme,
  on le glisse à une autre place, on le réinitialise ou on le supprime, chaque geste en une étape d'historique.
  L'inspecteur règle le nœud choisi. Le moniteur montre toute la chaîne en temps réel, quel que soit le nombre de
  nœuds, et un clip qu'on ne découpe pas reste écrit dans le fichier comme avant.
- **Roues lift / gamma / gain / offset** : le palet pousse les noirs, les tons moyens, les blancs ou tout le signal
  vers une couleur (l'anneau suit le vectorscope), la molette en règle le niveau, Maj affine et le double-clic remet à
  zéro. L'export applique la formule exacte, niveau par niveau.
- **Nœuds parallèles et de calque** (page Couleur) :
  - **Alt+P** ajoute un nœud à côté du nœud courant ; les deux corrigent la même image et leurs corrections
    s'additionnent.
  - **Alt+L** fait un calque : la branche du dessous passe dessus, là où son qualifieur la sélectionne.
  - Les branches se réunissent par un mélangeur, qui disparaît quand il n'en reste qu'une. Le moniteur montre tout le
    graphe en temps réel, et l'export calcule chaque mélange au niveau près.
- **Qualificateur** : un nœud peut ne corriger qu'une partie de l'image, choisie par plages de teinte, de saturation
  et de luminance, avec leur douceur. Les plages se glissent sur des bandes colorées, et on peut inverser la sélection.
  *Afficher la sélection* montre dans le moniteur ce qui est choisi, le reste en gris.
- **Pipette** du qualificateur : un clic dans le viewer sélectionne la couleur qui arrive au nœud sous le clic ;
  Maj + clic élargit la sélection jusqu'à elle.
- **Fenêtres** (page Couleur, onglet *Fenêtres*) : un rectangle, une ellipse ou une forme libre limite la correction
  d'un nœud. On la glisse, l'agrandit et la tourne dans le viewer (les sommets d'une forme libre aussi), on l'adoucit,
  on l'inverse pour corriger l'extérieur, on en combine plusieurs. Avec le qualificateur, seul ce que les deux
  sélectionnent est corrigé. Une fenêtre suit un objet comme un masque : panneau Tracking, *Lier à*, la fenêtre.
- **Flou et netteté** d'un nœud (onglet *Flou*) : ils agissent sur sa correction, là où sa fenêtre ou son
  qualificateur la sélectionnent ; une fenêtre inversée floute l'arrière-plan. Temps réel dans le moniteur GPU, exacts
  à l'export.
- **Avant / après** : un trait sépare le moniteur, l'image sans étalonnage à gauche ; on le glisse pour le déplacer.
- **Bande des plans** sur la page Couleur : une vignette par plan vidéo, numérotée, marquée quand le plan est
  étalonné. Un clic passe à ce plan (sélectionné, tête de lecture à son début).
- Moniteur GPU : l'étalonnage se voit en temps réel, en lecture comme à l'arrêt (exposition, contraste,
  saturation, température, teinte, ombres, hautes lumières, courbes et LUT `.cube`), sur le clip affiché comme sur
  un calque d'effets. Le moniteur ne réimite pas l'étalonnage : FFmpeg passe un réseau de couleurs dans la chaîne
  exacte de l'export, et le résultat (une LUT 3D) est lu par le GPU. Écart mesuré avec l'export : 0,1 à 0,15 niveau
  en moyenne.
- Panneau **Historique** (*Fenêtre › Panneaux › Historique*) : toutes les étapes du montage, nommées, de l'ouverture du
  projet à la dernière modification. Un clic revient à une étape en une fois ; les étapes suivantes restent
  rétablissables (en italique) jusqu'à la prochaine modification, et l'état enregistré dans le fichier est marqué ●.
- *Réseaux sociaux › Exporter en plusieurs formats…* : une version du montage par format coché (9:16, 4:5, 1:1, 16:9),
  mise en page pour son cadre, puis un export par format dans la file de rendu (`nom_9x16.mp4`, `nom_1x1.mp4`…). Les
  plans remplissent l'image, titres et calques gardent leurs proportions (mis à l'échelle ensemble, sans se tasser),
  les fonds couvrent le cadre, et ce qui tomberait sous l'interface de la plateforme ou hors du cadre est déplacé
  quand c'est possible sans rien recouvrir. Chaque version reste une séquence du projet, à retoucher avant l'export.
- Courbe de volume des pistes audio, dans la timeline : une bande sous les clips (menu ⋯ de la piste › « Courbe de
  volume », ouverte d'office quand la piste a déjà une courbe). Double-clic pour poser un point, glisser pour le
  déplacer (Maj : gain seul), Suppr ou clic droit pour le retirer, le remettre à 0 dB ou effacer la courbe. La courbe
  dessinée est exactement celle que l'export applique ; chaque geste s'annule en une fois.
- Photos dans les emplacements des templates : glisser des photos du Finder sur un emplacement le remplit, lui puis
  les emplacements vides qui suivent ; aussi *Réseaux sociaux › Photos › Remplir les emplacements avec des photos…*.
  Chaque photo remplit le cadre avec un Ken Burns, se place parmi les pistes vidéo comme une vidéo et accepte les
  transitions ; le Night Look et les titres du template restent par-dessus.
- *Réseaux sociaux › Photos › Diaporama photo…* : un plan par photo choisie, à la tête de lecture, une mesure chacun
  sur la grille rythmique (sinon la durée des photos des préférences), en Ken Burns. Chaque plan reste un emplacement
  où redéposer une autre photo ou une vidéo.
- Menu ⋯ d'une piste audio : son rôle (voix, musique, effets sonores, autre), que lisent le ducking automatique, les
  sous-titres automatiques et la coupe au rythme, et « Baisser sous… » pour baisser cette piste sous une autre
  (ducking par paire, qu'on peut aussi retirer).

### Changed

- Étalonnage, **Température** : une vraie balance des blancs, qui réchauffe ou refroidit toute l'image sans changer
  la luminosité d'un gris. Avant, elle n'agissait presque pas sur les tons moyens clairs (un gris moyen ne se
  réchauffait pas). **Teinte** fait maintenant tourner les teintes (le rouge vers le jaune…) ; elle agissait comme
  une seconde température. Un projet qui utilisait ces deux réglages s'affiche et s'exporte différemment.
- *Afficher la sélection* et la comparaison avant / après marchent aussi sans moniteur GPU (l'aperçu fidèle les
  rend) ; elles étaient grisées.
- Plusieurs nœuds d'étalonnage arrondissent moins : un réglage neutre (exposition, contraste, saturation) ne fait plus
  d'aller-retour par le YUV. Sur cinq nœuds, 0,5 niveau d'écart moyen à la formule au lieu de 1,2.
- Formes d'onde des clips audio : elles montrent enfin **le son que le clip fait entendre**, à l'endroit où on
  l'entend : la portion de média du clip (et non tout le fichier étiré), sa vitesse et son sens, son gain, avec une
  crête toutes les 2 ms, nette à tous les zooms pour caler une coupe sur un coup de caisse claire. Avant, le son était
  réduit aux basses et la forme d'onde affichée avant le calcul était inventée ; un clip dont le son n'est pas encore
  lu reste maintenant plat.
- Courbe de volume : deux points sans maintien sont désormais reliés par une ligne droite. Avant, le volume sautait
  au niveau du point suivant dès le point précédent. Seuls des projets écrits à la main ou par script sont concernés :
  aucune interface ne posait de points jusqu'ici.
- Moteur de rendu plus rapide et plus sobre, à images identiques : le premier export de trois montages réels est
  38 à 60 % plus rapide (un TikTok de 31 s à 54 calques graphiques passe de 98 s à 41 s), et FFmpeg utilise jusqu'à
  deux fois moins de mémoire ; les segments d'aperçu fidèle se calculent jusqu'à 44 % plus vite. Un titre, un flash
  ou une forme n'est plus composé que pendant les images où il est visible, les images des calques sont écrites en
  parallèle et les médias d'un montage se partagent les fils de décodage.

### Fixed

- La largeur d'un panneau déplacé dans une autre zone (l'inspecteur à gauche, par exemple) est gardée : elle était
  attribuée aux Médias pour la zone de gauche et à l'inspecteur pour celle de droite, quel que soit le panneau qui s'y
  trouvait.
- Export : les titres et calques animés à 30 images/s sautaient une image sur six (et en doublaient une autre), une
  saccade visible dans tout pop-in, glissé ou karaoké. Chaque image de l'animation est maintenant exportée.
- Export : un plan vidéo non tourné était légèrement flou (décalé d'un demi-pixel et moyenné, netteté −12 %). Il sort
  désormais avec les pixels de son média.
- Étalonnage à l'export : une couleur saturée réchauffée ou refroidie (température, teinte, ombres ou hautes
  lumières réglées) ne sort plus grise. Un rouge vif passé en saturation 1,3 avec une température devenait un gris
  moyen ; sur des images très colorées, la moitié des pixels pouvait l'être.
- Étalonnage : les curseurs « Ombres » et « Hautes lumières » agissent enfin sur l'image. Ils étaient annulés à
  l'export (et donc dans l'aperçu) : un projet qui les avait réglés change d'aspect.
- Export : un clip audio qui commençait au milieu d'une rampe de la courbe de volume faisait échouer l'export
  (expression `volume` invalide).
- Export : une transition entre deux plans dont l'un a une échelle animée (zoom d'impact des templates, Ken Burns)
  ou une autre échelle que l'autre faisait échouer tout l'export ; le second plan est aussi posé à sa propre place
  pendant la transition, et non plus à celle du premier.

## [0.2.7] - 2026-10-09

Montage vertical plus rapide : couper au rythme de la musique, recadrer un plan 16:9 en 9:16 en suivant le sujet, et
sous-titrer une voix automatiquement, sur la machine. Aussi : export H.265, découpage aux changements de plan et
ouverture des projets `.kut` par double-clic.

### Added

- Export H.265 (HEVC) : deux presets MP4, « H.265 1080p » et « H.265 4K ». Ils suivent le même choix d'encodeur que
  H.264 (matériel quand la machine le valide, CPU sinon) ; à qualité visuelle comparable, le fichier est plus léger.
- Ouverture d'un projet `.kut` par double-clic : depuis le Finder sous macOS. Sous Windows et Linux, le bouton
  « Associer les projets .kut » des préférences écrit l'association dans le profil de l'utilisateur, sans droits
  administrateur.
- Découpage aux changements de plan : « Découper aux changements de plan », dans le menu de séquence, coupe le clip
  sélectionné à chaque changement de plan détecté par FFmpeg (le clip d'origine garde le premier plan). Un clip retimé,
  inversé, figé ou imbriqué est refusé. L'opération s'annule comme les autres coupes.
- Couper au rythme en deux clics : sans grille rythmique, « Couper sur les temps » (menu Grille rythmique) mesure
  d'abord le tempo de la musique (le clip audio sélectionné, sinon la piste Musique), en arrière-plan et annulable, puis
  pose la grille et coupe les plans sélectionnés sur ses temps. La musique elle-même n'est pas coupée, et une seule
  annulation retire la grille et les coupes. « Couper tous les… » coupe tous les 2, 4 ou 8 temps.
- Recadrage vertical qui suit le sujet : « Recadrer en suivant le tracker » (menu Réseaux sociaux) fait remplir le cadre
  au clip sélectionné et anime son cadrage pour garder au centre le point suivi dans l'onglet Suivi. Un plan 16:9 devient
  un plan 9:16 sans perdre son sujet, sans jamais montrer de bord noir. Le résultat est fait d'images-clés retouchables,
  et s'annule en une fois.
- Sous-titres automatiques, sans rien envoyer en ligne : *Réseaux sociaux › Sous-titres automatiques* transcrit la voix
  avec whisper.cpp installé sur la machine, puis pose des lignes courtes, en sous-titres ou en titres karaoké où chaque
  mot s'allume quand il est dit. Le programme `whisper-cli` et le modèle se règlent dans *Préférences › Transcription*
  (ils sont aussi trouvés tout seuls : Homebrew, dossier de données de l'application). Une annulation retire toutes les
  lignes.

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

[Unreleased]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.5...v0.7.0
[0.6.5]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.4...v0.6.5
[0.6.4]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.5.5...v0.6.0
[0.5.5]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.5.0...v0.5.5
[0.5.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.7...v0.3.0
[0.2.7]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.2.0...v0.2.5
[0.2.0]: https://github.com/Kyyzen0/Kut-Studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Kyyzen0/Kut-Studio/releases/tag/v0.1.0

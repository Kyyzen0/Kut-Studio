# Couverture libass (sous-titres) sur macOS en CI

Les sous-titres incrustés passent par le filtre FFmpeg `subtitles`, qui n'existe que si FFmpeg est compilé avec
`--enable-libass`. Le FFmpeg que la matrice principale installe sur macOS (`brew install ffmpeg`) **n'a pas libass** :
l'application le détecte et refuse avec un message clair, et les tests de sous-titres s'y sautent avec la raison.
Résultat : avant ce chantier, **la lecture des sous-titres par libass n'était pas couverte sur macOS par la CI**.

Ce document dit ce qui a été fait, ce qui a été **vérifié** et ce qui reste une **hypothèse** à confirmer par le premier
passage du job en CI.

## En bref

* Un job dédié **`macOS / libass (sous-titres)`** (`macos-libass` dans `.github/workflows/multiplatform.yml`) installe
  `ffmpeg-full` (formule de homebrew-core, bouteille avec libass), le rend visible à l'application
  (`KUT_STUDIO_FFMPEG`, `KUT_STUDIO_FFPROBE`, `PATH`) et n'exécute que les tests marqués `libass` (`pytest -m libass`).
* `KUT_STUDIO_REQUIRE_LIBASS=1` y transforme le **saut en échec** : un job vert signifie « libass a été testé ».
  Sans la variable (développeurs, matrice principale), le comportement est inchangé : saut avec la raison.
* La matrice principale (macOS / Windows / Ubuntu) n'est **pas modifiée** (le job est ajouté à la fin du fichier,
  sans `needs`).
* Le job est **bloquant dès le premier jour** ; la stratégie de repli, si la mise en place devient instable ou trop longue,
  est écrite plus bas.
* **Non prouvé tant que le job n'a pas tourné en CI** : voir « Ce qui est vérifié, ce qui ne l'est pas ».

## Les tests

| Fichier | Rôle |
| --- | --- |
| `tests/ffmpeg_caps.py` | Garde partagée : interroge le FFmpeg **de l'application** (`KUT_STUDIO_FFMPEG`… puis `PATH`), pas le premier `ffmpeg` du `PATH`. Dit si le filtre `subtitles` existe (`ffmpeg -filters`) et si `ffmpeg -buildconf` contient `--enable-libass`. `ensure_libass()` : continue, **saute avec la raison**, ou **échoue** avec `KUT_STUDIO_REQUIRE_LIBASS=1`. |
| `tests/conftest.py` | Déclare la marque `libass` et applique la garde à tout test marqué (`pytest_pyfunc_call`). |
| `tests/test_libass_subtitles.py` | Preuve que libass sert vraiment : configuration + filtre + détection de l'application, puis **rendu réel** avec le constructeur de filtre de l'application (`ExportEngine.build_frame_command`, graphe de l'export et de l'aperçu) sur une vidéo `lavfi` unie, **relue pixel par pixel** : l'image sans sous-titre reste uniforme ; avec, le texte clair change les pixels du bas centré ; aucun texte avant ou après la durée du sous-titre ; un fichier ASS (style personnalisé) est lu et rend du texte. |
| `tests/test_ffmpeg_caps.py` | La garde elle-même, avec de faux FFmpeg (partout) : saut par défaut, échec avec la variable, preuve `--enable-libass` en mode strict, FFmpeg inutilisable signalé. |
| `tests/test_ci_workflow.py` | Invariants du job : variable posée, pas de `continue-on-error`, pas de `needs`, matrice inchangée, permissions en lecture, actions épinglées. |
| Tests existants marqués `libass` | `test_real_ffmpeg_export_burns_subtitles_into_mp4`, `test_default_render_incruste_les_sous_titres`, `test_ffmpeg_opens_subtitle_and_lut_files_whatever_the_folder_name[subtitles-*]` (5 cas). Leur garde maison est remplacée par la garde partagée. |

`pytest -m libass` sélectionne 12 tests. Sans libass ils sont tous **sautés avec la raison** ; avec
`KUT_STUDIO_REQUIRE_LIBASS=1` ils **échouent** tous avec le message :

> KUT_STUDIO_REQUIRE_LIBASS=1 : libass est exigé mais le FFmpeg de l'application, /opt/homebrew/bin/ffmpeg (9.0.2), n'a
> pas le filtre « subtitles » (libass absent). Installez un FFmpeg avec libass (macOS : brew install ffmpeg-full, voir
> docs/ci-libass.md).

Les tests de pixels et de position tournent aussi sur Ubuntu et Windows (d'après le rapport de stabilisation, seul le
FFmpeg macOS de la CI n'a pas libass) : ils ne supposent aucune police précise (libass retombe sur celle du système),
mais ils n'ont **pas** été exécutés sur ces deux plateformes avant le premier passage de la CI.

## Comparatif des façons d'obtenir un FFmpeg avec libass sur le runner macOS

Légende : **V** = vérifié (commande lue ou exécutée sur cette machine, sans rien installer) ; **W** = lu sur le web
(formulae.brew.sh, dépôt du tap) sans le reproduire ; **H** = hypothèse à confirmer en CI.

### (a) `ffmpeg-full` de homebrew-core — **retenu**

* **V** La formule existe (`brew info ffmpeg-full` : `homebrew/core`, stable 9.0.2, « bottled »), est **keg-only**
  (n'est pas liée dans `/opt/homebrew/bin`, d'où `KUT_STUDIO_FFMPEG`), ne gêne pas `ffmpeg`.
* **V** Son `ffmpeg -buildconf` contient `--enable-libass` (avec `--enable-libfontconfig --enable-libfreetype
  --enable-libharfbuzz`), `ffmpeg -filters` liste `subtitles` et `ass`. Le `ffmpeg` classique (même version 9.0.2) n'a ni
  l'un ni l'autre.
* **V** Fermeture de dépendances : 47 directes, **102 formules** en tout (`brew deps --union`), soit 103 paquets avec
  `ffmpeg-full`. Poids mesuré localement : **≈ 790 Mo installés** ; **≈ 190 Mo de bouteilles** (99 des 103 retrouvées
  dans le cache de téléchargement ; compter 190 à 230 Mo avec les quatre manquantes). La bouteille de `ffmpeg-full` seule :
  24 Mo.
* **W** Bouteilles publiées pour `arm64_tahoe`, `arm64_sequoia`, `arm64_golden_gate` (macOS récents, Apple Silicon) et
  Linux ; **aucune bouteille Intel macOS**. `macos-latest` est arm64, donc couvert **si** son système (Sequoia ou plus
  récent) est bien celui d'une des bouteilles (**H**, à confirmer en CI). Un runner plus ancien (Sonoma) ou Intel n'aurait
  pas de bouteille.
* **V** (lu dans les sources de Homebrew installées, `formula_installer.rb`, non exécuté) : une formule du dépôt principal
  sans bouteille compatible fait **échouer** `brew install` (« no bottle available! ») au lieu de la compiler. Une
  absence de bouteille donne donc un échec rapide et lisible, pas une compilation de plusieurs heures. (Les dépendances,
  elles, pourraient se compiler : c'est le rôle du `timeout-minutes`.)
* **H** Durée : 3 à 8 minutes sur `macos-latest` (téléchargement ≈ 200 Mo depuis ghcr.io, puis mise en place d'une centaine
  de paquets dont une partie est déjà sur l'image du runner). La durée réelle sera écrite dans le résumé du job.
* **H** Stabilité : bonne (même mécanisme que l'étape `brew install ffmpeg` existante), mais dépend de ghcr.io et de
  l'API Homebrew ; trois tentatives sont prévues.
* **Cache** : voir « Pourquoi pas d'`actions/cache` » plus bas.

### (b) Tap dédié `homebrew-ffmpeg/ffmpeg`

* **W** Installation : `brew tap homebrew-ffmpeg/ffmpeg` puis `brew install homebrew-ffmpeg/ffmpeg/ffmpeg`, après
  désinstallation de l'`ffmpeg` du dépôt principal. Précision utile : dans la formule lue, **libass est une dépendance par
  défaut** (`depends_on "libass"`, `--enable-libass` inconditionnel) ; il n'y a **pas d'option `--with-libass`** (la
  formule déclare une quarantaine d'autres options).
* **W** La formule ne contient **pas de bloc `bottle`** : une installation compile FFmpeg sur le runner.
* **H** Durée : plusieurs dizaines de minutes de compilation (FFmpeg seul, les dépendances étant des bouteilles du dépôt
  principal) : incompatible avec un `timeout-minutes` serré, et sensible à toute évolution de la formule. Non retenu.
* Non exécuté (`brew tap` interdit ici) : tout ce paragraphe est de la lecture, pas une mesure.

### (c) Ajouter `libass` à un FFmpeg déjà installé

* **V** Impossible avec une bouteille : libass est une option de **configuration** (`--enable-libass`), absente du
  `ffmpeg` installé (son `-buildconf` ne la contient pas) ; on ne peut pas la lui greffer après coup.
* Il reste de compiler une copie de la formule du dépôt principal avec `depends_on "libass"` et `--enable-libass` dans un
  tap local. **H** : 10 à 20 minutes de compilation, formule à maintenir à chaque version de FFmpeg. Plus fragile et plus
  long que (a) pour le même résultat. Non retenu.

### (d) Binaire statique macOS téléchargé et vérifié par SHA-256

* **Non téléchargé, non exécuté, non vérifié** : rien de ce qui suit n'est une mesure. Les pistes habituelles sont des
  builds statiques tiers (ex. les sites de builds macOS de FFmpeg, ou le binaire livré par `imageio-ffmpeg`) ; il faudrait
  d'abord **confirmer qu'une build arm64 avec `--enable-libass` existe** (les builds Intel seraient exécutées sous Rosetta).
* Principe sûr si on y vient : version **et** somme SHA-256 épinglées dans le workflow (`shasum -a 256 -c`), échec si la
  somme diffère, binaire mis en cache par `actions/cache` avec la somme dans la clé, pas de `curl | sh`, aucune mise à jour
  automatique.
* **Risque chaîne d'approvisionnement** : on fait confiance à un hébergeur tiers (compte, domaine, GitHub Release) ; une
  somme épinglée protège d'une substitution *après* l'épinglage, pas d'une build déjà piégée, ni de la disparition de
  l'hébergeur (le job devient rouge pour une raison externe). Il faut aussi relire la licence (GPL) et la provenance de
  la build.
* **H** Durée : la plus courte (30 à 90 Mo à télécharger, ~20 s, ~0 avec le cache). Stabilité : dépend de l'hébergeur
  tiers. Cache : un seul fichier, 30 à 90 Mo.

### Synthèse

| | Durée estimée (H) | Stabilité | Poids / cache | Risque principal | Retenu |
| --- | --- | --- | --- | --- | --- |
| (a) `ffmpeg-full` | 3-8 min | bonne (bouteilles) | ≈ 200 Mo téléchargés, ≈ 790 Mo installés ; pas de cache | échec si les dépendances n'ont pas de bouteille pour le runner | **oui** |
| (b) tap | 20-40 min | moyenne (compilation) | cache du Cellar fragile | durée, formule tierce | non |
| (c) FFmpeg recompilé | 10-20 min | moyenne | idem | formule à maintenir | non |
| (d) binaire statique | < 2 min | dépend de l'hébergeur | 30-90 Mo, cache simple | chaîne d'approvisionnement | **repli** |

## Détails de l'installation

* `brew install ffmpeg-full` sans `HOMEBREW_NO_AUTO_UPDATE` : on veut l'API Homebrew à jour (une API périmée pourrait
  référencer une bouteille retirée). `HOMEBREW_NO_INSTALL_CLEANUP`, `HOMEBREW_NO_ANALYTICS` et `HOMEBREW_NO_ENV_HINTS`
  évitent du travail et du bruit inutiles.
* `ffmpeg-full` est keg-only : l'étape « Expose » écrit `KUT_STUDIO_FFMPEG` et `KUT_STUDIO_FFPROBE` dans `GITHUB_ENV`
  (c'est ce que lit `core/tool_paths.py`) et ajoute `bin/` en tête du `PATH` pour les tests qui lancent `ffmpeg` tel quel.
  L'étape suivante vérifie que **l'application** (`find_media_tool("ffmpeg")`) résout bien ce binaire, pas un autre.
* `pytest` est lancé sans `-n` : douze tests, quelques secondes une fois le cache de polices construit.

## Pourquoi pas d'`actions/cache` pour Homebrew

* Les bouteilles sont des `tar.gz` **déjà compressés** (≈ 200 Mo) : les restaurer depuis le cache d'Actions n'est pas
  plausiblement plus rapide que de les télécharger depuis ghcr.io (**H**, à mesurer). Le coût réel est la mise en place
  d'une centaine de paquets, que le cache de téléchargements n'évite pas.
* Restaurer un `Cellar` complet est fragile (liens dans `/opt/homebrew/opt` et `/opt/homebrew/bin`, métadonnées de
  Homebrew, versions de dépendances qui dérivent avec l'image du runner) : c'est l'occasion de faux échecs.
* Le cache `pip` (`setup-python`) est déjà utilisé, comme dans la matrice.
* La durée de l'étape d'installation est écrite dans le résumé du job (`GITHUB_STEP_SUMMARY`) : on décidera avec des
  chiffres. Si elle dépasse régulièrement ~8 minutes, la réponse n'est pas un cache Homebrew mais le repli (d).

## Lire le résultat du job

Onglet du job, étape par étape (le résumé du job donne la durée d'installation, le FFmpeg utilisé et le décompte) :

| Étape rouge | Signification |
| --- | --- |
| `Install FFmpeg with libass` | Homebrew n'a pas pu installer `ffmpeg-full` (réseau, ghcr.io, pas de bouteille pour ce runner). Chercher « no bottle available » dans le journal. Rien à voir avec le code. |
| `Expose ffmpeg-full…` | Le binaire n'est pas où Homebrew le dit. |
| `Verify the application will use an FFmpeg with libass` | Le FFmpeg du `PATH` n'a pas `--enable-libass` / le filtre `subtitles`, ou **l'application ne résout pas `ffmpeg-full`** (`KUT_STUDIO_FFMPEG` ignorée). |
| `Run subtitle tests (libass required)` | Un test de sous-titres échoue : **vraie régression** (ou police introuvable). Le message du test dit ce qui manque (nombre de pixels touchés, position du texte). |
| `Check that libass was really exercised` | Filet de sécurité : des tests ont été sautés ou sont trop peu nombreux (marque `libass` perdue). Le seuil `MINIMUM_TESTS` du script se baisse sciemment si on retire des tests `libass`. |

**Vert = les 12 tests `libass` ont tourné, aucun saut, avec un FFmpeg dont `-buildconf` contient `--enable-libass`.**

## Reproduire en local sur un Mac avec libass

```sh
brew install ffmpeg-full                      # keg-only : ne remplace pas votre ffmpeg
prefix="$(brew --prefix ffmpeg-full)"
export KUT_STUDIO_FFMPEG="$prefix/bin/ffmpeg"
export KUT_STUDIO_FFPROBE="$prefix/bin/ffprobe"
export PATH="$prefix/bin:$PATH"               # pour les tests qui lancent « ffmpeg » directement
KUT_STUDIO_REQUIRE_LIBASS=1 python -m pytest -m libass -rs
```

Sans `KUT_STUDIO_REQUIRE_LIBASS`, un FFmpeg sans libass fait **sauter** ces tests avec la raison (`-rs` l'affiche) ;
avec, ils **échouent**. Sur Linux et Windows, `ffmpeg` du gestionnaire de paquets a libass : `pytest -m libass` suffit.

## Stratégie si le job devient instable ou trop long

**Décision : le job est bloquant (pas de `continue-on-error`) dès le début.** Un job non bloquant rendrait la couverture
libass optionnelle sans que personne ne le voie : c'est précisément l'état qu'on quitte. Les risques sont traités
autrement :

1. **Aléas réseau** : trois tentatives pour `brew install`, avertissement à chaque échec ; `timeout-minutes: 20` borne le
   pire cas (compilation d'une dépendance sans bouteille).
2. **Absence de bouteille** : échec rapide et lisible (voir (a)), pas de compilation en silence.
3. **Si l'installation dépasse régulièrement ~8 minutes ou échoue pour des raisons externes plus d'une fois sur dix sur
   deux semaines** : passer à l'option **(d)** (binaire statique, somme SHA-256 épinglée, mis en cache). Le job reste
   bloquant et garde la même garde (`KUT_STUDIO_REQUIRE_LIBASS`, `ffmpeg -buildconf`, tests de pixels) : seule l'étape
   d'installation change.
4. **En dernier recours seulement, et temporairement** : `continue-on-error: true` sur le job, **avec** un ticket ouvert,
   une échéance, et une modification volontaire de `tests/test_ci_workflow.py` (qui interdit aujourd'hui cet attribut).
   Le résultat du job reste affiché dans la vérification de la PR ; ce n'est jamais un retrait silencieux.
5. Déclencher le job seulement sur `workflow_dispatch` ou sur un planning laisse les PR sans protection : **déconseillé**.
   Un filtre par chemins demanderait une action tierce (`dorny/paths-filter`) ou un workflow séparé ; il n'est pas utilisé
   pour garder le fichier simple.

Avant de déclarer ce job « requis » dans la protection de branche, le laisser tourner quelques fois pour mesurer durée
et stabilité.

## Constat : `force_style` écrase le style des fichiers ASS

Le rendu réel (impossible auparavant sans libass) a révélé que l'export et l'aperçu **ne respectent pas les styles
personnalisés** quand le sous-titre passe par un fichier ASS. Mesure avec `ffmpeg-full` 9.0.2, cadre 640×360, sous-titre
« Bonjour le monde », image prise en cours de sous-titre :

| Style du clip | Fichier | Pixels touchés | Zone (x, y) |
| --- | --- | --- | --- |
| par défaut | SRT | 2 902 | x 227-413, y 306-331 (bas centré, lisible) |
| 48 pt jaune, bas | ASS | 145 | x 295-343, y 338-343 |
| 48 pt jaune, **haut centré** | ASS | **145** | x 295-343, y 338-343 |
| 120 pt rouge, **milieu centré** | ASS | **145** | x 295-343, y 338-343 |

Trois styles différents donnent le **même** résultat : un texte minuscule, en bas. Le même rendu **sans** le paramètre
`force_style` du filtre (essai à la main, en retirant `:force_style=…` de la commande de l'application) applique bien
les styles : haut centré à y 19-31, 120 pt au milieu à y 166-200. Cause (confirmée par l'essai sans `force_style`) : `_SUBTITLE_FORCE_STYLE_RAW`
(`core/export_engine.py` : `FontSize=22`, `Alignment=2`, `MarginV=24`…) est ajouté aussi aux fichiers ASS, et libass
l'applique par-dessus **tous** les styles du fichier ; `FontSize=22` est de plus lu dans le repère ASS 1920×1080 du
fichier, ce qui explique la taille (6 lignes de pixels pour un cadre de 360). `force_style` n'a de sens que pour le SRT.

**Non corrigé** ici (hors du périmètre de ce chantier : il change le rendu des exports). Le test
`test_an_ass_file_with_a_custom_style_is_rendered_by_libass` est volontairement muet sur taille, couleur et position pour
ne pas figer ce comportement ; le correctif attendu est de ne pas poser `force_style` quand le fichier est un `.ass`, avec
un test de position et de couleur.

## Ce qui est vérifié, ce qui ne l'est pas

**Vérifié sur cette machine** (macOS arm64, Homebrew 7.0.7, `ffmpeg-full` 9.0.2 **déjà installé** avant ce travail, rien
installé pour l'occasion) :

* `pytest -m libass` avec `KUT_STUDIO_FFMPEG` / `KUT_STUDIO_FFPROBE` / `PATH` pointant sur `ffmpeg-full` et
  `KUT_STUDIO_REQUIRE_LIBASS=1` : 12 réussis, avec **le vrai chemin de rendu** (libass lit le SRT/ASS, les pixels sont
  relus). Les étapes du job hors installation (exposition, vérification, tests, contrôle des sauts) ont été rejouées en
  local avec les mêmes scripts.
* Avec le FFmpeg sans libass de cette machine : 12 sautés avec la raison ; avec la variable, 12 échecs avec le message
  ci-dessus ; les étapes de vérification et de contrôle des sauts du job échouent bien.
* Le test de pixels échoue si le filtre `subtitles` est remplacé par un passage direct, ou si l'alignement du style par
  défaut est déplacé en haut (essais temporaires, code remis en l'état).
* Le YAML se charge (Ruby `Psych`), la matrice principale est identique à `HEAD` après analyse, les scripts `run:` passent
  `bash -n`. `actionlint` n'est pas installé ici : il n'a pas été exécuté.

**Non prouvé tant que le job n'a pas tourné en CI** : que la bouteille `ffmpeg-full` s'installe sur `macos-latest` dans
le temps prévu ; que `brew --prefix ffmpeg-full` y donne le même chemin ; que le rendu (polices, `fontsdir`
`/System/Library/Fonts`, cache `fontconfig` du premier lancement) y passe les mêmes seuils de pixels ; que les tests de
pixels passent aussi sur Ubuntu et Windows. **La couverture libass sur macOS en CI n'est donc pas acquise avant le premier
passage vert du job.**

# Recette de Kut-Studio (ADR-0011, phase 1)

La recette répond à une question : **« est-ce que ça marche, sur le matériel des utilisateurs ? »** Elle a deux moitiés, qui ne se
remplacent pas :

| Moitié | Qui | Où | Ce qu'elle voit |
| --- | --- | --- | --- |
| **Le banc scripté** (`tools/recette/`) | une commande, **sans fenêtre** | n'importe quelle machine ; sert aussi la CI | construction, export, mesures, `ffprobe`, plantage d'un processus |
| **La recette humaine** (ce dossier) | l'utilisateur, **en vraie fenêtre** | MacBook Air M1 (sans ventilateur) et Mac mini M4 | l'écran, la lecture, le GPU, l'interface, l'installation |

Machines et périmètre (ADR-0011, décision 3) : **macOS Apple Silicon d'abord** (M1 Air = plancher de performance et banc de throttling,
M4 mini = référence rapide) ; pas de Mac Intel ; **Windows est validé par la CI et une bêta**, jamais en vraie fenêtre ; Linux = smoke
test de la CI.

## Lancer le banc sur une machine

Depuis la racine du dépôt, avec l'environnement de développement (`pip install -r requirements-dev.txt`) et FFmpeg dans le `PATH` :

```bash
python -m tools.recette run --scale full                      # les trois parcours, durées réelles (20 min de multicam)
python -m tools.recette run --scale quick                     # les mêmes parcours, courts (quelques minutes en tout)
python -m tools.recette run --journeys tiktok --scale full    # un seul parcours
python -m tools.recette smoke                                 # 10 s exportées en CPU (ce que lance la CI)
python -m tools.recette gen --scale full                      # seulement générer les médias
python -m tools.recette verify --scale full                   # vérifier leurs empreintes
python -m tools.recette run --reference docs/recette/references/mac-mini-m4.json   # écarts à une référence
python -m tools.recette run --save-reference docs/recette/references/macbook-air-m1.json   # enregistrer une référence
python -m tools.recette report rapport.json --reference ref.json                   # re-rendre un rapport
python -m tools.recette crashes --session 2026-10-22T09:00/2026-10-22T11:00        # plantages par heure
python -m tools.recette thermal --minutes 20                                       # relevé thermique (macOS)
```

`--app CHEMIN` remplace les sources par un exécutable : le **paquet installé** (`Kut-Studio.app/Contents/MacOS/Kut-Studio`, `Kut-Studio.exe`,
le binaire Linux) accepte les mêmes commandes (`validate`, `apply`, `describe`, `export` : voir [../cli.md](../cli.md)). `--hardware auto`
laisse l'export utiliser l'encodeur matériel (VideoToolbox) ; le défaut, `cpu`, est le seul **comparable d'une machine à l'autre**.

### Où vont les fichiers

- **Dossier de travail** : `--workdir`, sinon `$KUT_STUDIO_RECETTE_DIR`, sinon **`~/Movies/Kut-Studio-Recette`** (macOS) ou
  `~/Kut-Studio-Recette`. Il reçoit `<parcours>-<échelle>/` (médias, `manifest.json`, `script.json`, `project.kut`, `out/`) et `runs/<date>/`
  (rapport, journaux, dossiers isolés). **Il ne peut pas être dans le dépôt** (le banc refuse) : aucun média n'est commité. Pas non plus un
  dossier temporaire qui disparaît au redémarrage : les médias se régénèrent en quelques minutes, mais autant les garder.
- **Place** (échelle `full`) : prévoir **3 à 4 Go** (trois angles de 20 min, les sorties) ; le banc prévient sous 20 Go libres. Les fichiers temporaires de l'export
  (`TMPDIR`) sont eux aussi sous `runs/<date>/env/tmp`.
- **Garde-fous (obligatoires)** : un export qui grossit sans fin remplirait le disque de la machine de recette (le swap du système, hors des dossiers du banc, y compris). Le lanceur
  mesure toutes les ~0,4 s la mémoire de l'arbre de processus et toutes les ~2 s le disque libre ; si le disque libre passe sous `--min-free-gb` (défaut **4 Go**) ou si la
  mémoire dépasse `--max-rss-fraction` de la RAM (défaut **0,85**), il arrête l'enfant **proprement** (`SIGINT`, comme un Ctrl-C : le CLI annule l'export et nettoie) et le parcours est marqué
  « arrêt par le garde-fou ». C'est ce qui est arrivé à l'export de 20 min multicam (D-002 de [defauts-connus.md](defauts-connus.md)). Le rapport note aussi le **pic d'espace disque** de chaque export
  (dossiers isolés + sortie, hors swap).
- **La configuration réelle de l'utilisateur n'est jamais touchée.** Chaque exécution crée ses propres dossiers
  (`runs/<date>/env/{config,cache,data,log,proxy}`) et les donne à **chaque** processus par `KUT_STUDIO_CONFIG_DIR`, `_CACHE_DIR`, `_DATA_DIR`,
  `_LOG_DIR`, `_PROXY_DIR` ; `QT_QPA_PLATFORM=offscreen` ; recherche de mise à jour coupée. `assert_isolated` refuse un environnement qui désigne
  (ou contient) les vrais dossiers. Le banc **n'ouvre jamais l'application installée** et ne tue aucun processus (seul son propre enfant, après un
  délai dépassé : `SIGINT` d'abord, voir `runner.py`). Seule la commande `crashes` lit le journal réel, en lecture seule, parce que c'est son but.

## Ce que fait chaque parcours

| Parcours | Média généré (échelle `full`) | Script (`script.json`, verbes de [../scripting.md](../scripting.md)) | Sorties |
| --- | --- | --- | --- |
| `tiktok` (a) | 3 plans verticaux 1080x1920 30 i/s, 2 horizontaux 1920x1080 25 i/s, musique 120 BPM, voix synthétique ; ≈ 59 s | plans bout à bout, chevauchements de transitions, coupes sur la grille rythmique, ducking, zoom d'impact, secousse, flash, titre animé, sous-titres (piste libass si disponible, karaoké Qt sinon), version carrée | 9:16 (`tiktok`), 1:1 (`square`) |
| `youtube` (b) | 3 angles 1920x1080 25 i/s de **20 min**, même son de salle, claps communs, départs décalés de 0 / 2,4 / 5,12 s ; narration et nappe de 20 min | source Multicam, ≈ 30 changements d'angle, narration + compresseur, nappe sous la voix, calque d'étalonnage, bandeau, 10 chapitres | 1080p (`youtube`) |
| `motion3d` (c) | fond 1920x1080 30 i/s de 30 s, nappe, **`.kut3d`** (cube, sphère, tore, cône, cylindre, texte 3D « KUT ») | étalonnage animé (`color.*`), vignette animée, titre à révélation, texte machine à écrire, formes en orbite + groupe, classement animé, scène 3D animée | 1080p (`h264_1080p`) |
| `smoke` | 2 plans 1280x720 de 5 s, musique | 2 clips, un titre | 1080p, **10 s** |

Pour chacun : durée de **génération**, de **construction** (`apply`), `validate`/`describe`, **tempo** (parcours TikTok : l'estimation du moteur contre
120 BPM) ou **synchronisation audio** (multicam : l'estimation du moteur contre les décalages fabriqués, écart en ms), durée d'**export**, pic de
mémoire, taille de sortie, `ffprobe` (durée, flux, cadence, format de pixels), part **noire** et **gelée** de la sortie, code de sortie, plantage
(mort par signal). Le rapport (`rapport.json` + `rapport.md`) ajoute la machine (modèle, puce, mémoire, système, version de l'application et de
FFmpeg) et, avec `--reference`, des **ratios** par rapport à une référence enregistrée.

### Déterminisme et empreintes

Aucun média n'est commité : le banc les **génère** (FFmpeg `lavfi` `testsrc2` pour l'image, NumPy et un générateur SplitMix64 pour le son, une graine par média).
Le `manifest.json` du parcours porte, par média, l'**empreinte de la recette** (paramètres + `GENERATOR_VERSION` : identique partout) et le **SHA-256 du
fichier**. Un média n'est réutilisé que si sa recette n'a pas changé **et** si son SHA-256 est celui du manifeste ; sinon il est régénéré.
Deux générations sur une machine donnée donnent les mêmes octets (testé). Entre deux machines, les octets d'une vidéo ne sont comparables que pour la même
version de FFmpeg et de x264 (le rapport note la version) ; **la comparaison de référence porte sur l'empreinte de la recette**, qui ne dépend d'aucun outil.
`gradients` (lavfi) a été écarté : deux exécutions donnent des images différentes.

### Dégradation propre

| Ce qui manque | Ce que fait le banc |
| --- | --- |
| **libass** (filtre `subtitles`, absent du FFmpeg Homebrew de cette machine de développement) | le script n'utilise pas `add_subtitle_lines` (qui ferait refuser l'export) et pose **tous** les sous-titres en karaoké Qt ; le rapport note `libass: False` |
| **whisper.cpp** / son modèle | l'étape « transcription » est `skipped_no_whisper` / `skipped_no_model` ; le projet n'en dépend pas (le texte des sous-titres est connu). Avec `--whisper` et `--whisper-model` (ou les variables de l'application), le banc chronomètre une vraie transcription de la voix de test |
| **NumPy** | non requis par le banc lui-même, mais la génération du son et l'analyse (tempo, synchronisation) en ont besoin : l'erreur le dit |
| **FFmpeg / ffprobe** | les tests du banc qui les exigent se sautent avec la raison ; `run` s'arrête sur une erreur claire |
| **libx264** | les sources sont encodées en MPEG-4 (le manifeste note l'encodeur) |
| **`pmset`** (hors macOS) | le relevé thermique refuse de tourner, sans effet sur le reste |

## Ce que le banc prouve, et ce qu'il NE prouve PAS

**Il prouve**, sans fenêtre : que le projet se construit par script, que le `.kut` se recharge sans perte (`validate` code 0), que l'export CLI produit un
fichier dont `ffprobe` confirme la taille, la durée (à 3 % près de la timeline), le format de pixels et la présence du son, qu'il n'y a pas de plage noire
ou gelée dominante, que la synchronisation audio et la détection de tempo du moteur retrouvent la vérité fabriquée, combien de temps et de mémoire cela
coûte sur **cette** machine, et si un processus est **mort par signal**.

**Il ne prouve pas** :

- **aucun écran** : pas d'interface, pas de fenêtre, pas de polices du système (le texte vient des polices embarquées ; en `offscreen`, le moniteur reste noir) ;
- **aucun GPU** : l'export est CPU ; le moniteur GPU (Metal, Direct3D 11), le décodage matériel et le rendu du moniteur 3D ne sont pas exercés ;
- **aucune lecture à l'écran** : ni fluidité, ni gigue, ni images perdues, ni latence de scrub, ni son en direct ; la mesure de référence de lecture (4K et 1080p,
  60 s) est **humaine** ([seance-soutenue.md](seance-soutenue.md)) ;
- **aucune installation** : ni installeur, ni Gatekeeper, ni notarisation, ni mise à jour automatique ;
- **aucune ergonomie** : un parcours qui réussit par script peut être pénible à la souris ;
- **pas Windows** : la CI Windows prouve les tests, un export CPU, l'installation et la mise à jour ; la bêta fait le reste (ADR-0011, « Risque assumé ») ;
- **pas la qualité de l'image** : « pas noir, pas gelé » n'est pas « beau » ; la comparaison visuelle est dans les fiches humaines.

Aucun seuil de durée n'est un test : **les ratios et étiquettes du rapport sont indicatifs**, les tests pytest du banc (`tests/test_recette_bench.py`)
portent sur des identités, des octets, des codes et des états.

## Le smoke test de la CI (sans GPU)

`tests/test_recette_bench.py::test_smoke_montage_of_10_seconds_exports_on_cpu_and_is_checked_by_ffprobe` (marque `recette_smoke`) : démarrage du CLI
(`presets`), construction par script, export CPU d'un montage de 10 s, `ffprobe`. Il se **saute avec sa raison** sans FFmpeg et n'impose aucun seuil de temps. Il
tourne déjà dans la suite complète des quatre systèmes ; pour le lancer seul : `python -m pytest -m recette_smoke -q`. **Sur le paquet publié**
(porte « Linux » de l'ADR-0011) : `python -m tools.recette smoke --app ./dist/Kut-Studio/Kut-Studio` (code de sortie 0 = réussi ; JSON sur la sortie
standard) ; un job de workflow n'a qu'à lancer cette ligne après la construction du paquet (non branché dans ce lot : le workflow n'est pas modifié ici).

## La recette humaine

1. [etapes-communes.md](etapes-communes.md) : installation propre, premier lancement, mise à jour depuis la version précédente, signature et notarisation.
2. Un parcours : [parcours-tiktok.md](parcours-tiktok.md), [parcours-youtube-multicam.md](parcours-youtube-multicam.md), [parcours-motion-3d.md](parcours-motion-3d.md).
3. [mise-a-jour-automatique.md](mise-a-jour-automatique.md) : la mise à jour automatique macOS (« Installer et redémarrer »), son retour arrière et ses cas assistés.
3. [seance-soutenue.md](seance-soutenue.md) : 20 min soutenues sur le M1 Air (throttling) et mesure de référence de lecture.
4. [plantages-par-heure.md](plantages-par-heure.md) : séances de 2 h, indicateur « plantages par heure ».
5. [fiche-defaut.md](fiche-defaut.md) : le gabarit de fiche de défaut ; [defauts-connus.md](defauts-connus.md) : ce qui est déjà inscrit.

Les libellés de menus des checklists sont ceux de l'application à la rédaction ; en cas d'écart, noter le libellé réel dans la colonne « écart ».

Les médias que le banc génère servent aussi à la recette humaine : **ouvrez-les dans l'application** (`~/Movies/Kut-Studio-Recette/<parcours>-full/media/`) et, pour
partir du même projet que le script, ouvrez `project.kut` (copiez-le d'abord ailleurs : l'application peut le réenregistrer).

## Code

| Module | Rôle |
| --- | --- |
| `tools/recette/__main__.py` | les commandes |
| `tools/recette/env.py` | dossier de travail, **isolation**, outils, identité de la machine |
| `tools/recette/audio.py`, `media.py`, `scene3d.py` | génération déterministe, manifeste et empreintes, le `.kut3d` |
| `tools/recette/journeys/` | les parcours : médias, script, exports, analyses |
| `tools/recette/pipeline.py`, `runner.py`, `analyze.py` | le déroulé, le lanceur mesuré (mémoire, signaux), tempo et synchronisation |
| `tools/recette/probe.py`, `report.py` | `ffprobe` + noir / gelé, rapport et ratios |
| `tools/recette/crashes.py`, `thermal.py`, `whisper_check.py` | plantages par heure, relevé thermique, transcription optionnelle |

Tests : `tests/test_recette_bench.py`.

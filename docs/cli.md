# Ligne de commande

Kut-Studio se pilote sans fenêtre : **l'application elle-même est le CLI** (ADR-0005, étape 3). Même binaire, même installation,
aucun second programme.

```bash
Kut-Studio info projet.kut
Kut-Studio validate projet.kut
Kut-Studio presets
Kut-Studio export projet.kut -o video.mp4 --preset h264_1080p
Kut-Studio apply montage.json projet.kut -o projet-monte.kut      # un script déclaratif : voir scripting.md
```

Depuis les sources : `python main.py info projet.kut`. Une commande est le premier argument qui n'est pas une option globale
(`--lang`) ; un projet nommé `info` (sans extension) s'ouvre par `./info`.

## Commandes

| Commande | Rôle |
| --- | --- |
| `info PROJET` | résumé : séquences (taille, cadence, durée, pistes, clips), médias et médias introuvables |
| `validate PROJET` | le projet s'ouvre-t-il, et **qu'a-t-il perdu en route** (voir ci-dessous) |
| `presets` | les préréglages d'export (`h264_1080p`, `h265_4k`, `youtube`…) |
| `validate SCRIPT.json` | le script est-il valide ? Toutes ses erreurs d'un coup, sans projet |
| `apply SCRIPT PROJET -o SORTIE` | applique un script **en tout ou rien** ; `--dry-run` n'écrit rien, `--in-place` modifie le projet lui-même, `--allow-outside` laisse `import_media` lire hors du dossier du script (voir [scripting.md](scripting.md)) |
| `schema` | écrit le schéma JSON des scripts, généré depuis la table de verbes |
| `export PROJET -o SORTIE` | exporte, sans fenêtre, avec exactement la commande FFmpeg de l'interface |

Options communes : `--json` (un objet JSON sur la sortie standard), `--lang fr|en|es` (aussi `KUT_STUDIO_LANG`, sinon `LANG`).

`export` : `--preset ID` (défaut `h264_1080p`), `--sequence ID|NOM` (défaut : la séquence active), `--hardware cpu|auto|…`,
`--master-gain-db N`, `--master-mute` (le Master est un réglage de l'application, pas du projet : à donner ici), `--overwrite`,
`--no-atomic`, `--quiet`.

## Contrat pour les scripts

- **`--json`** : **un** objet JSON sur la sortie standard et rien d'autre ; les messages humains et la progression vont sur la
  sortie d'erreur. Clé `kut_cli` = version du schéma (`1`), qui ne change que par une rupture annoncée dans le journal.
- **Codes de sortie** : `0` réussi · `1` échec · `2` (`validate`) projet chargé **avec des entrées ignorées** · `64` mauvais usage de
  la commande (argument manquant, préréglage ou séquence inconnus, sortie qui existe déjà) · `130` interrompu (Ctrl-C).
- **Jamais de projet modifié** par ces commandes. Un export ne remplace jamais son propre projet, ni un fichier existant sans
  `--overwrite`.
- **Fichier final atomique** : FFmpeg écrit dans `.<nom>.<id>.partial.<ext>` puis le fichier est renommé : un échec ou Ctrl-C ne laisse
  jamais un MP4 tronqué sous le nom demandé. `--no-atomic` écrit directement.
- Un Ctrl-C **annule** l'export proprement (FFmpeg est tué, les fichiers temporaires nettoyés).

## `validate` : ce qu'un chargement réussi ne dit pas

Le chargement d'un `.kut` est strict pour la structure (enveloppe, pistes, clips, médias : un fichier abîmé est refusé, code `1`) et
**tolérant pour les sous-objets décoratifs** : un effet, un bus, une transition, une association de ducking ou une entrée de
bibliothèque invalide est ignoré seul, pour qu'un projet un peu abîmé reste ouvrable dans l'interface. Un fichier forgé peut donc être
accepté **en perdant du contenu sans le dire**. `validate` (et `info`, et un avertissement de `export`) le disent :

```text
$ Kut-Studio validate projet.kut
Projet chargé, mais 3 entrée(s) ignorée(s) :
  - effets de clip : 2
  - bus audio : 1
$ echo $?
2
```

Sections comptées (`core.project_io.LOAD_SECTIONS`, clés stables du JSON `dropped`) : `clip_effects`, `audio_effects` (effets audio
et inserts de piste ou de bus), `buses`, `transitions`, `ducking`, `color_presets`, `library_folders`, `library_tags`,
`library_assignments`. Un média introuvable n'est **pas** une entrée ignorée : il est listé à part et ne change pas le code de sortie
(l'export, lui, le refuse avec un message clair).

## Ce que l'export n'a pas (encore)

L'export en ligne de commande n'écrit pas les livrables annexes des préréglages sociaux (copie d'aperçu, image de couverture) ; la
normalisation de loudness d'un préréglage qui en demande une est faite (mesure puis gain). Le texte et les formes sont dessinés par Qt :
le CLI crée une `QGuiApplication` **hors écran** (aucune fenêtre, aucune boucle d'événements) et enregistre les polices embarquées, comme
l'application.

## Code

| Module | Rôle |
| --- | --- |
| `core/cli.py` | les commandes, le contrat de sortie, les codes |
| `main.py` | route `sys.argv` vers le CLI **avant** Qt |
| `core/export_runner.py` | `run_export` : l'exécuteur sans Qt (voir [ADR-0005](adr/0005-mode-script-frontiere-de-securite.md)) |
| `core/project_io.py` | `LoadReport` : ce que le chargement a ignoré |
| `ui/i18n_cli.py` | messages traduits (fr, en, es) |

Tests : `tests/test_cli.py` (commandes, JSON, codes, annulation, `main.py` en sous-processus), `tests/test_load_report.py`.

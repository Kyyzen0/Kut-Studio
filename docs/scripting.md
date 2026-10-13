# Scripts déclaratifs

Un **script** est un fichier JSON qui décrit des modifications d'un projet : une liste d'opérations, chacune un verbe et ses
paramètres (ADR-0005, étape 4). Il ne contient aucun code : le décodeur ne connaît que la table des verbes ci-dessous, et chaque verbe
appelle une fonction de `core/` que l'interface appelle aussi. L'aperçu et l'export lisent le projet modifié comme toujours.

```json
{
  "kut_script": 1,
  "description": "Un bus voix compressé",
  "ops": [
    {"op": "add_bus", "as": "voix", "name": "Voix"},
    {"op": "route_track", "track": "A1", "bus": {"ref": "voix"}},
    {"op": "add_bus_insert", "bus": {"ref": "voix"}, "effect": "compressor", "params": {"ratio": 4}}
  ]
}
```

```bash
Kut-Studio validate script.json                          # le script seul : toutes ses erreurs d'un coup
Kut-Studio apply script.json projet.kut --dry-run        # le rapport, rien d'écrit
Kut-Studio apply script.json projet.kut -o resultat.kut  # écrit un autre fichier
Kut-Studio schema > kut-script.schema.json               # pour un éditeur ou un outil
```

## Règles

- **Strict.** `kut_script` (la version) est obligatoire ; une version plus récente que celle de l'application est refusée. Un champ
  inconnu est une **erreur** (avec une suggestion : « vouliez-vous dire `volume_db` ? »), jamais ignoré. Types, bornes (secondes de 0 à
  1 000 000, dB de −60 à 12 pour un volume, comme le modèle), tailles (1 Mio, 5 000 opérations) sont vérifiés. Toutes les erreurs sont rendues d'un coup (50 au plus).
- **Tout ou rien.** Si une opération est refusée, le projet n'est pas touché, et le rapport dit laquelle (numéro, verbe, message).
  Un script réussi est **une** étape d'historique. Les clips imbriqués sont recadrés comme dans l'interface.
- **Rien n'est évalué.** Pas d'expression, pas de variable, pas de calcul : une valeur est une donnée.
- **Identifiants et alias.** Un verbe qui crée un objet accepte `"id"` (identifiant imposé : lettres, chiffres et `_`, 64 au plus) et
  `"as"` (un alias, un nom du script). Un alias sert d'identifiant s'il n'y a pas d'`id` ; une opération suivante le cite par
  `{"ref": "voix"}`. Un alias inconnu, défini deux fois, ou du mauvais genre est une erreur. Rejouer un script donne le même projet,
  identifiant par identifiant. Sans `id` ni `as`, l'identifiant est généré (donc différent à chaque rejeu).
- **Sélecteurs.** Un objet existant se désigne par son identifiant, ou par son **nom exact s'il est unique** ; un nom ambigu est une erreur.
  Les clips et les inserts se désignent par identifiant seulement.
- **Fichiers en lecture seule, dans le dossier du script** (voir « Fichiers » plus bas).
- **Pas d'écriture par défaut.** `apply` écrit dans un autre fichier (`-o`). Écrire dans le projet lui-même demande `--in-place`, et
  un `-o` qui désigne le projet (lien symbolique compris) est refusé. `--dry-run` travaille sur une copie.
- **Pas de verbe `export`** : exporter est une décision de l'appelant (`Kut-Studio export`).
- Les messages sont traduits (français, anglais, espagnol : `--lang`) ; les noms de verbes et de champs restent en anglais.

## Les verbes

Cette liste est celle de `core/scripting/verbs.py` ; un test vérifie qu'elle ne s'en écarte pas.

| Verbe | Rôle |
| --- | --- |
| `add_bus` | Ajoute un bus audio à la séquence active (`name`) |
| `remove_bus` | Supprime un bus ; ses pistes retournent au Master (`bus`) |
| `rename_bus` | Renomme un bus (`bus`, `name`) |
| `set_bus_volume` | Règle le volume d'un bus (`bus`, `volume_db`) |
| `set_bus_muted` | Coupe ou rétablit un bus (`bus`, `muted`) |
| `route_track` | Envoie une piste sur un bus, ou au Master sans `bus` (`track`, `bus`) |
| `add_bus_insert` | Ajoute un effet en insert sur un bus (`bus`, `effect`, `params`, `enabled`) |
| `remove_bus_insert` | Retire un insert (`bus`, `insert`) |
| `set_bus_insert_enabled` | Active ou désactive un insert (`bus`, `insert`, `enabled`) |
| `update_bus_insert` | Modifie les paramètres d'un insert (`bus`, `insert`, `params`) |
| `import_media` | Importe un fichier média dans le projet, **en lecture seule** (`path`) ; voir « Fichiers » ci-dessous |
| `add_clip` | Pose un média **du projet** sur une piste (`track`, `asset`, `at`) |
| `delete_clip` | Supprime un clip (`clip`) |
| `move_clip` | Déplace un clip (`clip`, `at`) |
| `set_clip_enabled` | Active ou désactive un clip (`clip`, `enabled`) |
| `add_track_insert` | Ajoute un effet en insert sur une piste audio (`track`, `effect`, `params`, `enabled`) ; un **égaliseur** est l'effet `parametric_eq` |
| `remove_track_insert` | Retire un insert de piste (`track`, `insert`) |
| `set_track_insert_enabled` | Active ou désactive un insert de piste (`track`, `insert`, `enabled`) |
| `update_track_insert` | Modifie les paramètres d'un insert de piste (`track`, `insert`, `params`) |
| `set_track_muted` | Coupe ou rétablit le son d'une piste (`track`, `muted`) |
| `set_track_locked` | Verrouille ou déverrouille une piste (`track`, `locked`) |
| `set_track_visible` | Affiche ou masque une piste (`track`, `visible`) |
| `add_graphic` | Pose un calque graphique : `graphic` = `text`, `rectangle`, `solid`, `shape`, `adjustment` ou `null` (`at`, `duration`, `text`, `shape`, `track`) |
| `set_graphic` | Modifie un calque : `text`, `fill_color`, `stroke_color` (`#RRGGBB` ou `#RRGGBBAA`), `stroke_width`, `font_size`, `width`, `height` (au moins un) |
| `add_marker` | Ajoute un repère (`at`, `name`, `category`) |

`params` d'un insert : un dictionnaire plat (nombres, booléens, textes courts) dont **le modèle** valide les noms et les bornes ; un
paramètre inconnu est refusé avec le message du modèle. Le schéma complet (`Kut-Studio schema`) liste les champs de chaque verbe.

## Fichiers : `import_media`

C'est le **seul** verbe qui lit un fichier, et il le lit en lecture seule (il sonde le média avec `ffprobe`, il n'écrit rien).

- Le chemin se résout **relativement au dossier du script**. Un chemin qui en sort (`../autre.wav`, un chemin absolu ailleurs, un lien
  symbolique qui s'échappe, vérifié après résolution du lien) est **refusé** : c'est à l'appelant de l'autoriser avec
  `Kut-Studio apply … --allow-outside`, jamais au script.
- Le fichier doit exister et être un fichier ordinaire ; un média déjà dans le projet (même chemin) est refusé, pas dupliqué.
- Le média reçoit l'identifiant `id` (ou l'alias `as`) : `{"op": "import_media", "path": "voix.wav", "as": "voix"}` puis
  `{"op": "add_clip", "track": "A1", "asset": {"ref": "voix"}, "at": 0}`.
- Le texte d'un chemin ne contient pas de caractère de contrôle et tient en 1 024 caractères.

## Pas encore là

- **Verbes de couleur, d'images clés, de transitions, de sous-titres, de calques d'image** : prochains lots de l'étape 4.
- **Verrou consultatif** pour `--in-place` : l'application ne pose pas encore de verrou, donc ne modifiez pas un projet ouvert dans la
  fenêtre.
- La **façade Python** (`run`) et la **console** viennent après (étapes 6 et 7 de l'ADR).

## Code

| Module | Rôle |
| --- | --- |
| `core/scripting/verbs.py` | la table `VERBS` : paramètres typés et bornés, fonction du modèle appelée ; `ScriptContext` (le dossier permis) |
| `core/scripting/script.py` | le décodeur strict (erreurs structurées), la lecture du fichier, l'application par la session |
| `core/scripting/schema.py` | le schéma JSON, généré depuis la table |
| `core/scripting/session.py` | la session : tout ou rien, une étape d'historique |
| `core/cli.py` | `apply`, `validate script.json`, `schema` |

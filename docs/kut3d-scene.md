# Fichier de scène `.kut3d`

Un `.kut3d` est une scène 3D sérialisée (caméra, lumières, fond, modèles, instances). Un modèle y est une
**référence** vers un fichier OBJ, glTF/GLB, PLY ou STL (voir [kut3d-import.md](kut3d-import.md)) ; le rendu est décrit
dans [kut3d-render.md](kut3d-render.md). API : `kut3d.load_scene`, `save_scene`, `dependency_files`.

## Format (version 1)

```json
{
  "format": "kut3d",
  "version": 1,
  "scene": {
    "camera": {"position": [0, 1.5, 6], "target": [0, 0.5, 0], "up": [0, 1, 0],
               "fov_y_deg": 40, "near": 0.1, "far": 100},
    "lights": [
      {"type": "ambient", "color": [1, 1, 1], "intensity": 0.2},
      {"type": "directional", "direction": [0.3, -1, -0.2], "intensity": 2.0},
      {"type": "point", "position": [2, 3, 4], "intensity": 5.0, "range": null}
    ],
    "background": null,
    "models": [
      {"id": "voiture", "path": "modeles/voiture.glb",
       "normalize": {"up_axis": "z", "unit_scale": 0.01, "target_size": 2.0, "center": true}}
    ],
    "instances": [
      {"model": "voiture", "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
       "clip": "roule", "time_offset": 0.0, "loop": true, "name": "voiture"}
    ]
  }
}
```

- Seules `camera` (avec `position` et `target`) et, par modèle, `id` et `path` sont obligatoires ; le reste prend les
  valeurs de `kut3d.Camera`, des lumières et d'`Instance`. `background` : `null` (transparent, défaut) ou RGBA dans [0, 1].
- Les matrices (`transform`, `root_transform`) sont 16 flottants, ligne par ligne, translation en 4e colonne.
- `normalize` (forme à écrire à la main) est appliqué par `load_model`. `save_scene` écrit toujours `root_transform`
  (aller-retour exact) ; si les deux sont présents, **`root_transform` prime**.
- `clip` : nom ou indice d'une animation du modèle, ou `null`. Deux instances du même modèle partagent une entrée de `models`.

## Temps de la scène

Le temps est en **secondes locales du clip** qui référencera la scène. La scène elle-même ne boucle pas : chaque
instance règle son animation avec `loop` et `time_offset` (temps de l'animation = `t + time_offset`).

## Chemins et confinement

Les chemins de modèles sont relatifs au dossier du `.kut3d`, écrits avec `/`. Sont refusés : chemin vide, absolu,
lettre de lecteur, `\`, caractère nul, segment vide ou `.`, `..`, noms réservés de Windows (`CON`, `NUL`, `COM1`…),
caractères `<>:"|?*`, nom finissant par un point ou une espace. La casse est préservée. Après résolution, le fichier doit
rester sous le dossier du `.kut3d` : un lien symbolique qui s'en échappe est refusé. `allow_outside=True` (jamais le
défaut) autorise `..` et les liens qui sortent ; les chemins absolus restent refusés. `save_scene` applique les mêmes règles.

## Refus à la lecture (`SceneFileError`, en français)

Version plus récente que 1 (`SceneVersionError`) ; clé inconnue à n'importe quel niveau ; clé en double ; NaN, infini
ou nombre démesuré ; type faux (un booléen n'est pas un nombre) ; tableau de la mauvaise longueur ; valeur refusée par
`Camera`, les lumières ou `Instance` ; modèle introuvable ou illisible ; clip inexistant ; identifiant de modèle en double
(1 à 64 caractères parmi lettres ASCII, chiffres, `_`, `-`). `save_scene` refuse un modèle sans fichier source.

## Limites

Fichier ≤ 16 Mio, JSON de profondeur bornée, ≤ 16 lumières, ≤ 2048 instances, ≤ 256 modèles, noms ≤ 200 caractères,
chemins ≤ 1024. L'écriture est atomique (fichier temporaire puis remplacement), triée, en ASCII, terminée par `\n`.
`dependency_files(path)` retourne le `.kut3d` puis, triés et sans doublon, les fichiers de modèle, leurs `.mtl`, `.bin`
externes et textures (`Model.dependencies`).

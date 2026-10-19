# Kut-3D : importer un modèle 3D

Phase 1 d'[ADR-0009](adr/0009-kut3d-moteur-3d-scriptable.md) : le paquet `kut3d/` lit des fichiers 3D et les range dans
un **modèle de scène immuable**. Il n'utilise que la bibliothèque standard et numpy, aucune fenêtre, aucun import de
Qt, de `ui/` ni de `core/`. Le rendu et la lecture des animations viennent aux phases suivantes.

## Utilisation

```python
from kut3d import load_model, ModelImportError

model = load_model("personnage.glb", up_axis="y", target_size=1.8)   # 1,8 unité de haut, centré
print(model.triangle_count, [m.name for m in model.materials], model.warnings)
lo, hi = model.bounds()                                             # boîte englobante, normalisation comprise
for clip in model.animations:
    print(clip.name, clip.duration, len(clip.channels))
```

`load_model(chemin, *, up_axis="y", unit_scale=1.0, target_size=None, center=True)` choisit l'importeur par extension
(insensible à la casse). `supported_extensions()` liste les formats ; `register_importer(extensions, fn)` en ajoute.

## Formats

| Format | Pris en charge | Non pris en charge |
| --- | --- | --- |
| OBJ + MTL | `v vt vn f` (toutes formes, indices négatifs, polygones en éventail), un `Mesh` par `usemtl`, `Kd Ke d Tr Pm Pr`, `map_Kd map_Ke map_Bump bump norm` | couleurs de sommet `v x y z r g b`, courbes, groupes lissés `s` |
| PLY | ascii, `binary_little_endian`, `binary_big_endian` ; `x y z`, normales, `u v`/`s t`, couleurs, faces | nuages de points sans face |
| STL | ascii et binaire (taille = 84 + 50 n, jamais le mot `solid`) ; sommets soudés, normales lisses recalculées | normales de facette du fichier |
| glTF / GLB | JSON, `.bin`, data URI, chunk GLB ; accessors (stride, `normalized`, `sparse`) ; TRIANGLES ; PBR ; nœuds ; skins ; `JOINTS_0`/`WEIGHTS_0` ; morph targets ; animations (LINEAR, STEP, CUBICSPLINE) | autres modes de primitive (ignorés), `TEXCOORD_1`+, plus de 4 influences, tangentes, caméras, lumières, Draco et toute extension requise inconnue (erreur) |

FBX, DAE, 3MF et USD ne sont pas lus (phase 6).

## Ce que contient le modèle

- `Model` : `nodes` (hiérarchie, TRS local, quaternions `xyzw`), `root_nodes`, `meshes`, `materials`, `skeletons`,
  `animations`, `source`, `root_transform`, `warnings`. Main droite, Y vers le haut.
- `Mesh` : `positions`, `indices`, `normals`, `uvs`, `colors`, `joints`, `weights`, `morph_targets`, `material_index`.
  Les UV suivent la convention glTF (V vers le bas) : les importeurs OBJ et PLY retournent V.
- `Material` : couleur, métal, rugosité, émission, `alpha_mode`, `double_sided` et des `TextureRef` (`path` ou `data`
  embarquée) : **aucune image n'est décodée** à l'import.
- `Skeleton`, `AnimationClip` / `AnimationChannel` : voir `kut3d/model.py` (forme des valeurs documentée).
- Tout est `frozen`, les tableaux numpy sont en lecture seule. `Model.bounds()` tient compte des nœuds et de
  `root_transform` ; un maillage à peau est pris en pose de liaison.

## Normalisation

Elle remplit `root_transform` et **ne modifie jamais les sommets**, pour que squelette et animations restent valides.
Ordre : `up_axis="z"` (rotation de −90° autour de X), `unit_scale`, `target_size` (plus grande dimension de la boîte),
`center`.

## Avertissements et erreurs

- `Model.warnings` : MTL ou texture introuvable (matériau gris, texture absente), texture hors du dossier du modèle
  (les chemins absolus sont ramenés au nom de fichier dans ce dossier), primitive ignorée, extension optionnelle inconnue.
- `FileNotFoundError` : chemin absent. `UnsupportedFormatError` : extension inconnue (le message liste les formats).
  `ModelImportError` (un `ValueError`) : fichier illisible, tronqué ou démesuré ; le message nomme le fichier et la cause.
- Les entrées sont bornées avant allocation (taille de fichier, éléments, longueur de ligne) ; rien n'est exécuté.
- Les chemins de buffers et de textures restent dans le dossier du modèle.

Tests : `.venv/bin/python -m pytest tests/test_kut3d_*.py -q`.

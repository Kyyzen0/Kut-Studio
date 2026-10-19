# Kut-3D : rendre une scène (rendu de référence numpy)

Phase 2a d'[ADR-0009](adr/0009-kut3d-moteur-3d-scriptable.md) : le paquet `kut3d/` pose des modèles importés dans une
scène (caméra, lumières) et la rend en image, **sans Qt, sans fenêtre, de façon déterministe**. C'est la **référence** :
la phase 2b y alignera un moteur GPU. Voir aussi [l'import de modèles](kut3d-import.md).

## Utilisation

```python
from kut3d import (AmbientLight, Camera, DirectionalLight, Instance, PointLight, Scene,
                   load_model, render_frame, render_sequence)

perso = load_model("personnage.glb", target_size=1.8)
scene = Scene(
    instances=[Instance(perso, clip="marche", transform=translation)],     # clip = nom ou indice
    lights=[AmbientLight((1, 1, 1), 0.15), DirectionalLight((-0.4, -1, -0.6), intensity=2.0)],
    camera=Camera(position=(0, 1.2, 4), target=(0, 0.9, 0), fov_y_deg=40),
    background=None,                                                       # None = fond transparent
)
frame = render_frame(scene, t=0.5, size=(1280, 720), samples=2)            # Frame
paths = render_sequence(scene, fps=24, duration=2.0, size=(1280, 720), out_dir="images")  # frame_000001.png...
```

- `render_frame(scene, t=0.0, size=(640, 360), *, backend="numpy", samples=1) -> Frame`.
- `render_sequence(scene_at, *, fps, duration, size, out_dir, backend="numpy", samples=1) -> list[Path]` :
  `scene_at` est une `Scene` (ses clips avancent) ou une fonction `t -> Scene` ; PNG RGBA écrits de façon atomique.
- `register_backend(nom, fn)` / `available_backends()` : un moteur est `fn(scene, t, (w, h), samples) -> Frame`.
  Un nom inconnu lève `RenderError` en listant les moteurs disponibles.
- `evaluate_pose(model, clip, t, loop=False)` : pose animée (matrices monde, matrices d'articulation, poids de formes).

## Conventions

- Main droite, Y vers le haut, la caméra regarde vers son -Z. Matrices 4x4 « colonne », quaternions `(x, y, z, w)`.
- `DirectionalLight.direction` est le sens **dans lequel la lumière se propage**. `PointLight` : intensité mesurée à
  1 unité, atténuation en 1/d², fenêtrée par `range` (glTF). Intensité 1 : une surface blanche mate éclairée de face rend 1,0.
- Calculs en **espace linéaire** ; textures `base_color` et `emissive` lues en sRGB, les autres en linéaire ; sortie en sRGB.
  Facteurs du matériau multipliés aux textures ; matériau absent = gris 0,8 mat.
- `Frame.color` : (H, W, 4) uint8, **alpha droit** (non prémultiplié) ; `alpha` : (H, W) float32 ; `depth` : (H, W) float32,
  **profondeur de vue** (distance le long de l'axe de visée, pas distance euclidienne), `inf` là où il n'y a rien.
- `background` : `None` (alpha 0) ou RGBA de 4 flottants dans [0, 1] (sRGB, alpha droit).
- Ordre des déformations (glTF) : **formes de visage, puis os** (4 influences), puis `Instance.transform @ root_transform`.
  Un maillage à peau ignore la matrice de son propre nœud ; un miroir (déterminant < 0) inverse le sens des triangles.

## Qualité

PBR métal / rugosité (GGX, Smith, Schlick), textures de couleur, normales (tangentes calculées depuis les UV), métal-rugosité,
émission, occlusion (sur l'ambiance seulement) ; `alpha_mode` `mask` (seuil) et `blend` ; `samples` = 1, 2 ou 4 **par axe**
(moyenne en linéaire, alpha pondéré) ; découpe contre le plan proche ; UV et normales corrigées de la perspective ;
arêtes partagées exactes (règle top-left). Tonemapping : exposition 1,0 puis écrêtage.
PNG lu en natif (tous types, 1 à 16 bits, Adam7) ; JPEG et autres formats par Qt **seulement s'il est installé**
(`kut3d/render/images.py`, import paresseux, seul fichier autorisé à le faire).

## Bornes (vérifiées avant d'allouer, erreurs en français)

Côté d'image 8192 ; `largeur x hauteur x samples²` ≤ 16,7 millions (~32 octets par pixel sur-échantillonné) ; 2 millions de
triangles et 6 millions de sommets ; 100 000 triangles en blend ; 16 lumières ; 2048 instances ; texture 16384 par côté et
64 millions de pixels ; séquence de 100 000 images, 240 ips. Constantes : `kut3d/render/limits.py`, `kut3d/scene.py`.

## Limites connues (volontaires pour la phase 2a)

Pas d'ombres portées, de flou de mouvement, d'éclairage par image (IBL), de mipmaps ni de GPU (phase 2b), pas de mélange de
clips (phase 4), pas de façade `Film`, de clip dans la timeline ni de fichier `.kut3d` (phase 3). Le tri des triangles
`blend` est approximatif pour des surfaces transparentes qui s'interpénètrent, et elles n'écrivent pas la profondeur.
Les normales d'un maillage à peau sont mélangées avec les matrices d'articulation (exact pour rotations et échelle uniforme).
Le décodeur PNG est en Python pur pour les filtres « moyenne » et « Paeth » : lent sur de très grandes images.

Tests : `.venv/bin/python -m pytest tests/test_kut3d_*.py -q`.

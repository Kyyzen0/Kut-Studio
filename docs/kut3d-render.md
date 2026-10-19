# Kut-3D : rendre une scène (rendu de référence numpy)

Phase 2a d'[ADR-0009](adr/0009-kut3d-moteur-3d-scriptable.md) : le paquet `kut3d/` pose des modèles importés dans une
scène (caméra, lumières) et la rend en image, **sans Qt, sans fenêtre, de façon déterministe**. C'est la **référence** :
le moteur GPU « qrhi » de la phase 2b (section [Moteur GPU](#moteur-gpu-qrhi-phase-2b)) s'y aligne. Voir aussi [l'import de modèles](kut3d-import.md).

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

- `render_frame(scene, t=0.0, size=(640, 360), *, backend="numpy", samples=1) -> Frame` ; `backend` : `"numpy"` (référence),
  `"qrhi"` (GPU, voir plus bas) ou `"auto"`.
- `render_sequence(scene_at, *, fps, duration, size, out_dir, backend="numpy", samples=1) -> list[Path]` :
  `scene_at` est une `Scene` (ses clips avancent) ou une fonction `t -> Scene` ; PNG RGBA écrits de façon atomique.
- `register_backend(nom, fn, *, available=None)` / `available_backends()` / `backend_available(nom)` : un moteur est
  `fn(scene, t, (w, h), samples) -> Frame` ; `available` (facultatif) retourne `None` ou la raison de l'indisponibilité.
  Un nom inconnu lève `RenderError` en listant les moteurs disponibles.
- `evaluate_pose(model, clip, t, loop=False)` : pose animée (matrices monde, matrices d'articulation, poids de formes).

## Conventions

- Main droite, Y vers le haut, la caméra regarde vers son -Z. Matrices 4x4 « colonne », quaternions `(x, y, z, w)`.
- `DirectionalLight.direction` est le sens **dans lequel la lumière se propage**. `PointLight` : intensité mesurée à
  1 unité, atténuation en 1/d², fenêtrée par `range` (glTF). Intensité 1 : une surface blanche mate éclairée de face rend 1,0.
- Calculs en **espace linéaire** ; textures `base_color` et `emissive` lues en sRGB, les autres en linéaire ; sortie en sRGB.
  Facteurs du matériau multipliés aux textures ; matériau absent = gris 0,8 mat.
- **Échantillonnage** (`TextureRef.wrap_s` / `wrap_t` / `mag_filter` / `min_filter`, sampler glTF) : adressage
  `repeat`, `clamp` ou `mirror` par axe, filtre bilinéaire ou voisin le plus proche (défaut : répétition + bilinéaire).
  Pas de mipmaps. Le moteur numpy n'estime pas le niveau de détail : il applique `mag_filter` partout ; le GPU applique
  `min_filter` aux surfaces réduites (les deux concordent tant que les deux filtres sont égaux, cas courant).
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

Pas d'ombres portées, de flou de mouvement, d'éclairage par image (IBL) ni de mipmaps (ni pour numpy, ni pour le GPU), pas de mélange de
clips (phase 4), pas de façade `Film`, de clip dans la timeline ni de fichier `.kut3d` (phase 3). Le tri des triangles
`blend` est approximatif pour des surfaces transparentes qui s'interpénètrent, et elles n'écrivent pas la profondeur.
Les normales d'un maillage à peau sont mélangées avec les matrices d'articulation (exact pour rotations et échelle uniforme).
Le décodeur PNG est en Python pur pour les filtres « moyenne » et « Paeth » : lent sur de très grandes images.

Tests : `.venv/bin/python -m pytest tests/test_kut3d_*.py -q`.

## Moteur GPU « qrhi » (phase 2b)

`render_frame(scene, t, size, backend="qrhi", samples=...)` rend la même chose que `"numpy"` (tolérances ci-dessous), environ
**20 fois plus vite**, **sans jamais afficher de fenêtre**. Le moteur numpy reste la référence et l'oracle des tests.

```python
frame = render_frame(scene, 0.5, (1280, 720), backend="qrhi", samples=2)   # GPU, ou RenderBackendUnavailable
frame = render_frame(scene, 0.5, (1280, 720), backend="auto")              # qrhi si la sonde réussit, sinon numpy
kut3d.backend_available("qrhi")   # bool (sonde en sous-processus, mise en cache) ; kut3d.probe_qrhi() : ProbeResult détaillé
```

- **Comment** : PBR identique à numpy (mêmes formules, mêmes bornes de lumières, espace linéaire, sortie sRGB), toutes les
  textures (un `QRhiSampler` par réglage d'adressage / de filtre, mis en cache), `opaque` / `mask` / `blend`, culling et double face, profondeur, fond transparent ou opaque. Le rendu va dans des
  textures flottantes (couleur RGBA 32 bits linéaire prémultipliée + profondeur de vue R32F, deux cibles de couleur dans la
  même passe) relues par `readBackTexture` ; le mélange `blend` se fait en matériel, en linéaire, comme numpy. `samples`
  rend à `samples` fois la taille puis réduit **sur le CPU avec la fonction de numpy** (moyenne en linéaire, alpha pondéré),
  la conversion sRGB et le fond aussi. Pas de précision perdue sur la profondeur : elle est flottante, pas empaquetée.
- **Os dans le vertex shader** : tableau `mat4 joints[128]` (4 influences par sommet ; **limite : 127 os**, l'identité des
  sommets sans poids occupe la 128e). Au-delà, ou pour un maillage sans normales, ou pour une matière en `blend` (le tri a
  besoin des sommets déformés), le dessin est déformé côté CPU par `assemble.py` : plus lent, résultat identique, aucun
  avertissement à l'exécution. **Formes (morph targets)** : mélangées côté CPU avant le téléversement, dans l'ordre glTF
  (formes puis os). Les tangentes d'un maillage à peau sont calculées en pose de repos puis déformées avec les os.
- **Shaders** : `kut3d/gpu/shaders/kut3d.vert` et `kut3d.frag` (GLSL 440, une seule paire), `.qsb` COMMITÉS (SPIR-V, GLSL
  150 / 330 / 300 es, HLSL 5.0, MSL 1.2), `manifest.json` d'empreintes (sources + liste de cibles). Rien n'est compilé à
  l'exécution : un `.qsb` manquant est une erreur claire. Régénérer après toute modification (nécessite `pyside6-qsb`) :
  `python -m kut3d.gpu.build_shaders` (`--check` : code 1 si périmé ; un test échoue aussi). Le packaging de ces fichiers
  dans l'application (Kut-Studio.spec) relève de la **phase 3** : l'application empaquetée déclare le moteur indisponible.
- **Disponibilité et sécurité** : la sonde (`kut3d.gpu.probe_qrhi`, `python -m kut3d.gpu.probe_worker`) rend un triangle avec le
  vrai pipeline **dans un sous-processus, avec un délai de 30 s** : un plantage ou un blocage du pilote ne tue pas l'appelant.
  Le résultat est mis en cache par plateforme Qt. `backend="qrhi"` indisponible lève `RenderBackendUnavailable`
  (une `RenderError`) avec la **raison réelle** (PySide6 absent, plateforme Qt sans GPU, plantage du pilote, délai...).
  `"auto"` essaie le GPU puis retombe sur numpy (sonde échouée, autre fil, image trop grande pour les textures du GPU).
  `KUT3D_GPU=off` désactive le moteur ; `KUT3D_GPU_API=metal|d3d11|opengl|vulkan|null` force l'API graphique.
- **Fil et application Qt** : l'appel doit venir du **fil principal** (celui de l'application Qt), sinon `RenderError`. Si une
  `QApplication` existe (cas de Kut-Studio), elle est utilisée telle quelle, sans toucher à l'environnement ni à ses réglages ;
  sinon une est créée (nom neutre `kut3d-render`, aucun `QSettings`, rien n'est écrit chez l'utilisateur) ; sur macOS
  `-platform cocoa` est passé en argument si `QT_QPA_PLATFORM` n'est pas définie, et la politique d'activation est
  « accessoire » (pas d'icône dans le Dock). Les plugins `offscreen` et `minimal` n'ont pas de GPU : le moteur le dit au
  lieu d'échouer en silence. Le widget (`QRhiWidget` avec `WA_DontShowOnScreen`, jamais affiché : aucune fenêtre visible, mesuré)
  et ses ressources sont détruits par `kut3d.gpu.shutdown()` ou à la sortie du processus (`atexit`, `aboutToQuit` côté hôte).
- **Plateformes TESTÉES : macOS seulement** (Apple M4, Metal, PySide6 6.11.2, Python 3.14), plus la CI/Docker Linux pour la partie
  sans GPU (la sonde y échoue proprement : Qt retombe sur `offscreen`). **Windows (Direct3D 11) et Linux (OpenGL / Vulkan)
  ne sont PAS testés** : le code suit `isYUpInFramebuffer()` (retournement de la relecture) et `clipSpaceCorrMatrix()`, et
  repose sur `gl_FrontFacing` pour les faces arrière ; à vérifier sur ces systèmes avant de s'y fier.
- **Limites** : une image sur-échantillonnée plus large que la texture maximale du GPU (16 384 sur Metal) est refusée
  (`RenderBackendUnavailable`, donc `auto` retombe sur numpy) ; mémoire GPU ~ 28 octets par pixel sur-échantillonné ;
  jusqu'à 16 lumières ; un appel de dessin par groupe (matériau, peau) et par suite de triangles `blend` de même matériau
  (une scène de verre aux matériaux entrelacés fait beaucoup de dessins).

### Écarts mesurés avec numpy (macOS / Metal, 27 scènes de `tests/kut3d_gpu_cases.py`)

Quatre scènes de sampler (`sampler_*` : UV hors de [0, 1], `clamp`, `mirror`, `nearest`) ont été ajoutées ensuite ; elles passent aux mêmes tolérances (hors du tableau ci-dessous).

Cube damier, scène PBR (directionnelle + ponctuelle, métal et diélectrique), cartes de normales, métal-rugosité / émission /
occlusion, os (dont miroir et repli CPU), formes puis os, `mask`, `blend` trié, fond opaque et translucide, double face et
culling, miroir, couleurs de sommet, sans normales, `samples` 2 et 4, deux plans de profondeur, découpe du plan proche, et un
terrain de 20 000 triangles en 720p.

| Mesure | Résultat | Exigé par les tests |
|---|---|---|
| Couleur, pixels à +-6 niveaux | 99,7 % à 100 % (22 scènes sur 27 à 100 %, écart maximal 1 ou 2 niveaux à l'intérieur) | >= 99,5 % |
| Alpha, pixels à +-0,02 | idem ; arêtes à 4 échantillons : écart de couverture jusqu'à 0,25 | >= 99,5 % |
| Profondeur de vue | <= 1,6e-4 relatif (float32) ; terrain : 4 pixels d'arête sur 920 000 prennent le triangle voisin | 99,9 % des pixels à 1e-3 ; <= 0,5 % de vide / plein différents |

Les pixels qui diffèrent sont des **arêtes** : un centre de pixel exactement sur la diagonale d'un carré ou d'un maillage miroir
est tranché autrement en float32 (GPU) qu'en float64 (numpy). Le maillage à peau recalcule ses normales en pose de repos
quand il en a ; sans normales il passe par le CPU pour rester identique.

### Temps par image (2e image, après chauffe, relecture CPU et conversion sRGB comprises)

| Scène | numpy | qrhi (Metal) |
|---|---|---|
| 720p, ~20 000 triangles texturés, 1 lumière | ~550 ms | ~26 ms (dont ~20 ms de conversion sRGB sur CPU) |
| 640x360, même scène, `samples=2` | ~565 ms | ~31 ms |
| 640x360, `samples=4` | n. m. | ~76 ms |
| 160x120, cube | ~25 ms | ~1,2 ms |

La première image d'un processus coûte ~0,4 s (sonde en sous-processus ~0,27 s + création du pipeline).

Tests : `.venv/bin/python -m pytest tests/test_kut3d_gpu.py -q` (partout) et `tests/test_kut3d_gpu_render.py` (rendus réels, sautés
avec la raison réelle de la sonde quand aucun GPU n'est utilisable ; ils tournent dans un sous-processus à plateforme native
car la suite force `offscreen`).

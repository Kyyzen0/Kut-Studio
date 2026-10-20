# Kut-3D : un film par script Python

Phase 3 (tranche A) d'[ADR-0009](adr/0009-kut3d-moteur-3d-scriptable.md) : la façade `core.film.Film` relie le rendu 3D
([rendre une scène](kut3d-render.md)) au montage de Kut-Studio. Un script lancé à la main construit une scène, la rend en
vidéo **avec transparence**, la pose au-dessus d'un fond dans un projet ordinaire, ajoute de l'audio, sauve le `.kut` et
exporte un MP4. **Aucune fenêtre n'est ouverte** et le format `.kut` ne change pas.

## Le flux

```python
from core.film import Film
from kut3d import Scene, Camera, Instance, load_model

film = Film("out/mon_film.kut", size=(1920, 1080), fps=24)      # le dossier « out » est créé
film.background((0.05, 0.06, 0.10))                              # couleur RVB 0..1, ou chemin d'image / de vidéo
shot = film.render_shot(scene_or_fn, duration=4.0, name="plan1", samples=2)
film.place(shot, start=0.0, track=2)                             # V1 = le fond ; les plans vont à partir de V2
film.audio("musique.wav", start=0.0, gain_db=-6.0)               # piste audio A1
film.save()                                                      # écrit out/mon_film.kut
film.export("out/mon_film.mp4")                                  # MP4 H.264 ; preset="youtube" (ou un autre préréglage de Kut)
```

Exemple complet, sans fichier externe : `.venv/bin/python examples/kut3d_hello_film.py out/` (un cube texturé qui tourne 3 s).

## L'API

- `Film(path, *, size=(1920, 1080), fps=24.0)` : `path` finit par `.kut`. `size` : deux entiers **pairs** (jamais arrondis en silence).
- `background(couleur | chemin)` : couleur RVB (ou RVBA avec alpha 1), image (recadrée pour remplir le cadre) ou vidéo (au moins
  aussi longue que le film, sans aucune tolérance ; un fichier audio seul, illisible ou sans flux vidéo est refusé dès l'appel). Le fond est fabriqué à l'enregistrement, à la durée exacte du film.
- `render_shot(scene_or_fn, *, duration, name, samples=1, backend="auto", keep_frames=False, overwrite=False) -> Shot` :
  `scene_or_fn` est une `Scene` (ses clips avancent) ou une fonction `t -> Scene` ; `Shot` est immuable (`name`, `path`,
  `duration` = `frames / fps`, `frames`, `size`, `fps`). Un nom (lettres, chiffres, `_`) déjà pris est refusé sans `overwrite=True`.
- `place(shot, *, start=0.0, track=2) -> clip_id` : refuse un chevauchement sur la même piste et un plan d'une autre taille.
- `audio(chemin, *, start=0.0, track=1, gain_db=0.0, duration=None)` : fichier audio seul ; coupé à la fin des plans.
- `save() -> Path`, `export(sortie, *, preset=None) -> Path`, `project()` (le `Project` construit), `duration`.
- Toute erreur est une `FilmError` (une `ValueError`) en français ; les bornes sont vérifiées **avant** de rendre.

## `place_scene` : un vrai clip « scène 3D »

`render_shot` fabrique une **vidéo** (ProRes 4444). `place_scene` pose à la place un **clip natif** : la scène est écrite dans un
fichier `.kut3d` ([format](kut3d-scene.md)) et le projet reçoit un calque graphique « scène 3D » qui la référence. Le clip s'ouvre et se règle dans
l'application, se rend avec le reste du montage (aperçu fidèle, export), n'a aucun ProRes à stocker.

```python
film.place_scene(scene, duration=3.0, start=0.0, track=1, name="cube")   # -> "scene_cube_1"
```

- `scene` : une `kut3d.Scene`. **Une fonction `t -> Scene` n'est pas sérialisable** : elle est refusée, utilisez `render_shot`.
- `track` : numéro de la piste de **graphiques** (G1, G2...), au-dessus de toute la vidéo (défaut 1). Un chevauchement sur la même piste est refusé.
- `duration` est arrondie à l'image (`round(durée x fps)`, comme un plan) ; `name` (lettres, chiffres, `_`) ne peut pas être celui d'un plan
  ni d'une autre scène du film (casse ignorée) ; `overwrite=True` remplace les fichiers d'une exécution précédente.
- Fond transparent (celui que la scène déclare, sinon aucun), taille et cadence du film, vitesse fixe (pas de remappage de temps).
- Le projet s'écrit alors au format `.kut` **22** ; `export()` fonctionne tel quel (les scènes sont vérifiées avant l'export).
- **Modèles copiés** : un `.kut3d` ne référence que des fichiers **sous son propre dossier**. `place_scene` copie donc chaque modèle (OBJ, glTF,
  GLB, PLY, STL) avec ses dépendances (MTL, `.bin`, textures en fichier) dans `<projet>_assets/<nom>_models/<modèle>-<empreinte>/`, en gardant
  l'arborescence relative qui les relie ; une seule copie par fichier source ; la matrice `root_transform` de chaque modèle est conservée.
  Au plus 1 000 fichiers et 2 Gio par scène. Un modèle construit en code (sans fichier) est refusé : enregistrez-le d'abord.
  (Les importeurs confinent déjà MTL, `.bin` et textures au dossier du modèle.)
- Écrits : `scene_assets/<nom>.kut3d` et `scene_assets/<nom>_models/`. Une erreur ne laisse aucun fichier partiel.

Exemple complet : `.venv/bin/python examples/kut3d_scene_film.py out/` (cube texturé OBJ + MTL + PNG fabriqué par le script).
Un film peut mêler `render_shot` + `place` et `place_scene`.

## Ce qui est écrit sur disque

```
out/mon_film.kut               le projet
out/mon_film_assets/plan1.mov  le plan : ProRes 4444 avec canal alpha, matrice BT.709 étiquetée
out/mon_film_assets/background.mp4   le fond fabriqué (couleur ou image)
out/mon_film.mp4               l'export
```

Les PNG intermédiaires (`mon_film_assets/plan1/frame_000001.png`...) sont supprimés après l'encodage, sauf `keep_frames=True`.
Une erreur ou un Ctrl-C ne laisse aucun fichier partiel. Seuls les fichiers `frame_*.png` du dossier sont effacés.

## Limites

- Un plan de `render_shot` est un **média vidéo ordinaire** (pas de réglage de la scène depuis l'interface) ; pour un clip natif, voir `place_scene`.
  Le `.kut` ne contient aucun script ; il ne référence une scène que par le chemin de son `.kut3d`.
- Le format `.kut` n'est pas modifié : le film s'écrit dans la version que le contenu exige (comme un projet fait à la main).
- Le ProRes 4444 est lourd (de quelques Mo à plusieurs dizaines de Mo par seconde selon la scène, en 1080p).
- Un plan dure `round(durée x fps)` images ; durée d'un plan : 3600 s au plus, 100 000 images au plus.
- Le rendu `numpy` de référence est lent (mesure : 4 s en 720p ≈ 22 s) ; `backend="auto"` prend le GPU quand il est utilisable.
- La musique n'allonge pas le film. Pas de calques de texte ou de graphiques, de transitions ni de réglages d'export fins ici :
  le projet sauvé s'ouvre dans Kut-Studio pour les ajouter.
- Le script s'exécute avec le dépôt (`.venv/bin/python`) ; l'empaquetage de la façade dans l'application n'est pas traité.

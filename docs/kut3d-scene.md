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
caractères `<>:"|?*`, nom finissant par un point ou une espace, demi-paire de substitution UTF-16, et tout composant de plus de
**255 octets UTF-8** (limite usuelle de Linux et macOS ; contrôle sans accès disque, donc identique partout : `é` en compte 2,
un émoji 4). La longueur totale reste limitée à 1024 caractères. La casse est préservée. Après résolution, le fichier doit
rester sous le dossier du `.kut3d` : un lien symbolique qui s'en échappe est refusé. `allow_outside=True` (jamais le
défaut) autorise `..` et les liens qui sortent ; les chemins absolus restent refusés. `save_scene` applique les mêmes règles. Toute erreur du système de fichiers (permission, nom trop long, boucle de liens
symboliques, dossier en lecture seule) devient une `SceneFileError` courte, sans le chemin fautif ; jamais une `OSError`.

## Refus à la lecture (`SceneFileError`, en français)

Version plus récente que 1 (`SceneVersionError`) ; clé inconnue à n'importe quel niveau ; clé en double ; NaN, infini
ou nombre démesuré ; type faux (un booléen n'est pas un nombre) ; tableau de la mauvaise longueur ; valeur refusée par
`Camera`, les lumières ou `Instance` ; modèle introuvable ou illisible ; clip inexistant ; identifiant de modèle en double
(1 à 64 caractères parmi lettres ASCII, chiffres, `_`, `-`). `save_scene` refuse un modèle sans fichier source.

## Écriture aussi stricte que la lecture

Avant de remplacer la destination, `save_scene` relit la forme sérialisée du document avec les validateurs du lecteur
(`build_scene`, modèles non rechargés) : noms et clips ≤ 200 caractères, chemins, nombres finis, `root_transform`
non dégénérée, nombre de lumières / instances / modèles, taille ≤ 16 Mio. Un document que `load_scene` refuserait est donc
refusé à l'écriture (`SceneFileError`), la destination existante reste intacte et aucun temporaire ne traîne.

`DirectionalLight` conserve telle quelle une direction déjà unitaire (|‖v‖ − 1| ≤ 1e-12) et ne normalise que les autres :
la normalisation est idempotente, donc `save → load → save` redonne exactement les mêmes octets.

## Limites

Fichier ≤ 16 Mio, JSON de profondeur bornée, ≤ 16 lumières, ≤ 2048 instances, ≤ 256 modèles, noms ≤ 200 caractères,
chemins ≤ 1024. L'écriture est atomique (fichier temporaire puis remplacement), triée, en ASCII, terminée par `\n`.
`dependency_files(path)` retourne le `.kut3d` puis, triés et sans doublon, les fichiers de tout modèle **déclaré** (même
sans instance : `load_scene` l'exige), leurs `.mtl`, `.bin`
externes et textures (`Model.dependencies`).

## Un clip « scène 3D » dans un projet Kut-Studio (lots B2a : modèle et format, B2b : rendu)

Un clip 3D est un **calque graphique** (`GraphicType.SCENE3D`, `core/graphics.py`) dont `source_path` est le chemin d'un `.kut3d`.
Il vit sur une piste « graphiques », se crée par `add_graphic_clip` (qui lit la scène : `core.scene3d.validate`, sans rien
rendre) et se coupe, se duplique, se colle, se groupe, se verrouille et se met en solo comme les autres calques. Aucun champ n'est
ajouté à `Clip` : le temps de la scène est `source_in + (t - timeline_start)` (une coupe ou un rognage à gauche avancent
`source_in` : la seconde moitié reprend là où la première s'arrête).

* **Format `.kut`** : un projet qui contient un calque 3D (dans n'importe quelle séquence, groupe ou composition sérialisée) s'écrit
  en **version 22** (`SCENE3D_VERSION`, `payload_uses_scene3d`) et ne s'ouvre plus dans une version antérieure ; un projet sans
  calque 3D reste écrit octet pour octet comme avant (`tests/test_scene3d_format.py` en fige l'empreinte).
* **Refusé, explicitement** : toute vitesse, marche arrière, arrêt sur image ou courbe de vitesse (`core.scene3d.ensure_fixed_speed`,
  appelé par chaque fonction du modèle qui écrit `time_remapping` ; les scripts refusent déjà les pistes graphiques), l'enregistrement
  en preset (une autre version perdrait le calque en silence), l'entrée dans une composition (le nœud graphique ne porte pas
  `source_in`), le changement de fichier d'un clip existant. Stabilisation et transitions ne visent que les clips vidéo.
* **Rendu (lot B2b)** : le clip se rend à l'export et dans l'aperçu fidèle (pas encore dans l'aperçu en direct ni dans
  l'interface : lot B3). Le chemin de l'image est décrit dans [architecture.md](architecture.md#clip-scène-3d--chemin-de-limage).
  Taille de rendu = taille de la boîte du calque (par défaut celle du projet), fond transparent sauf `background` de la scène,
  **moteur numpy en dur** (les images du cache disque doivent être les mêmes partout ; le moteur entre dans la clé d'image),
  `samples=1`. Transformations, opacité, images clés, effets, masques et fusion s'appliquent comme à un calque image.
  Une scène **sans animation** (aucune instance n'a de `clip`) vaut toujours l'instant 0 : ses images identiques ne se rendent
  qu'**une** fois ; une scène animée se rend une fois par instant distinct.
* **Fichier manquant, illisible, modèle manquant** : à l'écran une carte « Scène 3D indisponible » prend la place du calque
  (l'aperçu ne lève jamais) ; à l'export le plan porte la scène dans `missing_media` et l'export est refusé (« Média introuvable :
  scène 3D … »). Une texture qui existe mais ne se décode pas n'est découverte qu'au rendu : l'image reste vide (journal
  `kut_studio.scene3d`), sans refus.
* **Coût** : numpy rend ≈ 550 ms par image en 720p pour une scène de ~20 000 triangles texturés (un cube : ≈ 140 ms ; 64x36 :
  ≈ 4 ms). Une scène animée de 10 s à 30 i/s en 720p coûte donc de l'ordre de 3 minutes de rendu au premier export ; le second
  lit les PNG du cache `mograph` (borné par `CacheManager`, éviction des moins récemment utilisés).
* `core.scene3d` : `scene_image(graphic, size, mode)` (le rendu ; `mode` `"exact"` par défaut, `"interactive"` réduit le plus grand
  côté à 480 px, réservé à l'aperçu en direct du lot B3), `scene_time_at`, `render_identity`, `load_scene` (au plus 4 scènes en
  mémoire, revalidées par la signature), `problem(path)`, `validate(path)`, `dependencies(path)` (fichiers de la scène, modèles confinés sous son dossier) et
  `scene_signature(path)` (empreinte bon marché pour les clés de cache : date, taille, début et fin de chaque fichier, mémorisée
  et revalidée par `stat` ; `"missing"` si le fichier manque, `"invalid:…"` si la scène ne se lit pas).

# Fichier de scène `.kut3d`

Un `.kut3d` est une scène 3D sérialisée (caméra, lumières, fond, modèles, instances). Un modèle y est une
**référence** vers un fichier OBJ, glTF/GLB, PLY ou STL (voir [kut3d-import.md](kut3d-import.md)) **ou une forme de base
paramétrique** (cube, sphère, plan, cylindre, cône, tore : [voir plus bas](#formes-de-base-format-2)), sans aucun fichier ;
le rendu est décrit dans [kut3d-render.md](kut3d-render.md). API : `kut3d.load_scene`, `save_scene`, `dependency_files`.

## Format (version 1, ou 2 avec des formes de base)

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

## Formes de base (format 2)

Une entrée de `models[]` peut porter **`primitive`** à la place de `path` : les PARAMÈTRES de la forme, jamais ses sommets (la
géométrie est générée à la lecture, de façon déterministe). Un fichier qui contient au moins une forme est écrit en
**version 2** ; un fichier sans forme reste écrit **octet pour octet comme en version 1**. Un lecteur plus ancien refuse un
fichier de version 2 (`SceneVersionError`) au lieu de l'ignorer ; un lecteur récent refuse une forme dans un fichier de version 1.

```json
{
  "format": "kut3d",
  "version": 2,
  "scene": {
    "camera": {"position": [0, 1, 5], "target": [0, 0, 0]},
    "lights": [{"type": "directional", "direction": [-0.4, -0.6, -0.7], "intensity": 2.5}],
    "models": [
      {"id": "sol", "primitive": {"kind": "plane", "width": 8.0, "depth": 8.0, "width_segments": 1, "depth_segments": 1},
       "material": {"base_color": [0.3, 0.3, 0.35, 1.0], "roughness": 0.9}},
      {"id": "boule", "primitive": {"kind": "sphere", "radius": 0.5, "segments": 32, "rings": 16},
       "material": {"base_color": [0.9, 0.2, 0.2, 1.0], "metallic": 0.1, "roughness": 0.4}}
    ],
    "instances": [
      {"model": "sol"},
      {"model": "boule", "transform": [1,0,0,0, 0,1,0,0.5, 0,0,1,0, 0,0,0,1]}
    ]
  }
}
```

| `kind` | paramètres (défaut) | géométrie |
|---|---|---|
| `cube` | `width` 1, `height` 1, `depth` 1 | boîte centrée, 24 sommets (normales plates), 12 triangles |
| `sphere` | `radius` 0.5, `segments` 32, `rings` 16 | normales lisses, un sommet par segment à chaque pôle |
| `plane` | `width` 1, `depth` 1, `width_segments` 1, `depth_segments` 1 | dans XZ à y = 0, normale +Y, surface OUVERTE |
| `cylinder` | `radius` 0.5, `height` 1, `segments` 32, `caps` true | axe Y ; bouchons plats si `caps` |
| `cone` | `radius` 0.5, `height` 1, `segments` 32, `cap` true | axe Y, base en bas, sommet en haut ; bouchon plat si `cap` |
| `torus` | `major_radius` 0.5, `minor_radius` 0.2, `segments` 32, `tube_segments` 16 | axe Y ; `minor_radius` < `major_radius` |

Toutes les formes sont centrées sur l'origine (main droite, Y vers le haut), leurs triangles sont en sens direct vu de
l'extérieur (un matériau à une seule face les montre correctement) et leurs UV suivent glTF (origine en haut à gauche,
V vers le bas) : une texture s'y pose à l'endroit, sans miroir, sur chaque face du cube, sur le plan et sur les bouchons.
Le plan, un cylindre sans `caps` et un cône sans `cap` sont des surfaces ouvertes : `"double_sided": true` pour les voir des
deux côtés.

**Matériau** (`material`, facultatif ; absent = le gris par défaut du rendu) : `name`, `base_color` (RGBA dans [0, 1]),
`metallic`, `roughness`, `alpha_cutoff` (dans [0, 1]), `emissive` (RVB dans [0, 16]), `alpha_mode` (`opaque`, `mask`, `blend`),
`double_sided`. Les **textures** d'une forme ne s'enregistrent pas encore : `save_scene` refuse un matériau avec texture
(`SceneFileError`) au lieu de la perdre ; elles marchent pour un rendu en mémoire. `root_transform` (16 nombres) est accepté,
`normalize` ne l'est pas. Un modèle de forme dont le maillage a été changé après coup (`dataclasses.replace`) est refusé à l'écriture.

**Bornes** (constantes de `kut3d.primitives`, vérifiées AVANT de générer quoi que ce soit, à la lecture comme à la construction) :
dimensions dans [1e-6, 1e6] (finies ; ni booléen ni texte), `segments` dans [3, 256], `rings` dans [2, 256], `width_segments` et
`depth_segments` dans [1, 256], `tube_segments` dans [3, 256], **entiers** (`32.0` est refusé). **65 536 triangles** au plus par forme
(`MAX_PRIMITIVE_TRIANGLES` ; une sphère 256 x 128 ou un plan 181 x 181 tiennent, un plan 256 x 256 non) et **500 000 triangles** au plus
pour l'ensemble des formes d'un fichier (`MAX_SCENE_PRIMITIVE_TRIANGLES`) : un fichier hostile n'alloue jamais plus.

En script : `kut3d.primitive_instance("sphere", radius=1.0, position=(0, 1, 0), material=Material(...))` (ou
`Film.primitive(...)`) retourne une `Instance` ; `kut3d.primitive_model(Sphere(radius=1.0))` retourne le `Model`.

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

Version plus récente que 2 (`SceneVersionError`) ; clé inconnue à n'importe quel niveau ; clé en double ; NaN, infini
ou nombre démesuré ; type faux (un booléen n'est pas un nombre) ; tableau de la mauvaise longueur ; valeur refusée par
`Camera`, les lumières ou `Instance` ; modèle introuvable ou illisible ; clip inexistant ; identifiant de modèle en double
(1 à 64 caractères parmi lettres ASCII, chiffres, `_`, `-`). `save_scene` refuse un modèle sans fichier source (géométrie construite en code), sauf une forme de base.

## Écriture aussi stricte que la lecture

Avant de remplacer la destination, `save_scene` relit la forme sérialisée du document avec les validateurs du lecteur
(`build_scene`, modèles non rechargés) : noms et clips ≤ 200 caractères, chemins, nombres finis, `root_transform`
non dégénérée, nombre de lumières / instances / modèles, taille ≤ 16 Mio. Un document que `load_scene` refuserait est donc
refusé à l'écriture (`SceneFileError`), la destination existante reste intacte et aucun temporaire ne traîne.

`DirectionalLight` conserve telle quelle une direction déjà unitaire (|‖v‖ − 1| ≤ 1e-12) et ne normalise que les autres :
la normalisation est idempotente, donc `save → load → save` redonne exactement les mêmes octets.

## Limites

Fichier ≤ 16 Mio, JSON de profondeur bornée, ≤ 16 lumières, ≤ 2048 instances, ≤ 256 modèles, noms ≤ 200 caractères,
chemins ≤ 1024, formes de base : voir plus haut. L'écriture est atomique (fichier temporaire puis remplacement), triée, en ASCII, terminée par `\n`.
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
* **Rendu (lot B2b)** : le clip se rend à l'export et dans l'aperçu fidèle ; l'interface (lot B3) est décrite plus bas. Le chemin de l'image est décrit dans [architecture.md](architecture.md#clip-scène-3d--chemin-de-limage).
  Taille de rendu = taille de la boîte du calque (par défaut celle du projet), fond transparent sauf `background` de la scène,
  **moteur numpy en dur** (les images du cache disque doivent être les mêmes partout ; le moteur entre dans la clé d'image),
  `samples=1`. Transformations, opacité, images clés, effets, masques et fusion s'appliquent comme à un calque image.
  Une scène **sans animation** (aucune instance n'a de `clip`) vaut toujours l'instant 0 : ses images identiques ne se rendent
  qu'**une** fois ; une scène animée se rend une fois par instant distinct.
* **Fichier manquant, illisible, modèle manquant** : à l'écran une carte « Scène 3D indisponible » prend la place du calque
  (l'aperçu ne lève jamais) ; à l'export le plan porte la scène dans `missing_media` et l'export est refusé (« Média introuvable :
  scène 3D … »). Une dépendance qui existe mais ne **se rend pas** (texture PNG tronquée,
  scène hors des bornes de `kut3d`) est **tolérée à l'écran** (la boîte reste vide, journal `kut_studio.scene3d`) et **refusée à
  l'export** : `check_export_request` (`core/scene3d_check.py`, point d'entrée commun de l'export de l'application, de
  `run_export` : ligne de commande, scripts, `Film.export`) décode les textures de chaque scène distincte et en fait un rendu d'essai
  de 16x16 pixels ; un échec refuse l'export en nommant la scène, le clip et la cause. Ce contrôle n'est PAS dans
  `build_render_plan` (rappelé à chaque rafraîchissement de l'aperçu). Pendant l'export, un échec imprévu (mémoire, texture modifiée
  en cours de route) fait échouer l'export (`core.scene3d.strict_rendering` : aucune sortie, pas de liste `.ffconcat`) ; les aperçus
  n'y entrent jamais. Coût du contrôle : ≈ 1 à 2 ms pour une scène de test (une lecture par texture distincte, un rendu 16x16).
* **Coût** : numpy rend ≈ 550 ms par image en 720p pour une scène de ~20 000 triangles texturés (un cube : ≈ 140 ms ; 64x36 :
  ≈ 4 ms). Une scène animée de 10 s à 30 i/s en 720p coûte donc de l'ordre de 3 minutes de rendu au premier export ; le second
  lit les PNG du cache `mograph` (borné par `CacheManager`, éviction des moins récemment utilisés).
* `core.scene3d` : `scene_image(graphic, size, mode)` (le rendu ; `mode` `"exact"` par défaut, `"interactive"` réduit le plus grand
  côté à 480 px, réservé au moniteur ; ``backend`` / ``side`` optionnels pour ce seul mode), `scene_time_at`, `render_identity`, `load_scene` (au plus 4 scènes en
  mémoire, revalidées par la signature), `problem(path)`, `validate(path)`, `dependencies(path)` (fichiers de la scène, modèles confinés sous son dossier) et
  `scene_signature(path)` (empreinte bon marché pour les clés de cache : date, taille, début et fin de chaque fichier, mémorisée
  et revalidée par `stat` ; `"missing"` si le fichier manque, `"invalid:…"` si la scène ne se lit pas).

## Dans l'interface (lot B3)

**Poser une scène.** Menu Calques > Ajouter > « Scène 3D… » (niveau Studio, sans raccourci) : sélecteur de fichier `.kut3d`, clip de
5 s à la tête de lecture sur une piste graphique, une entrée d'historique. Glisser-déposer d'un `.kut3d` sur la fenêtre : sur la
timeline, le clip se pose à l'instant et sur la piste graphique visés (une piste graphique verrouillée refuse, un autre type de piste
reçoit la scène sur une piste graphique à cet instant) ; ailleurs, à la tête de lecture. Un fichier illisible, introuvable ou d'une
autre extension est refusé par un message (boîte de dialogue pour le menu, barre d'état pour un dépôt) et rien n'est créé. Les autres
fichiers déposés restent importés comme des médias. Glisser un `.obj` / `.glb` pour créer une scène par défaut : pas encore.
(`ui/scene3d_actions.py`)

**Aperçu du moniteur : une image figée et approximative, jamais de 3D en direct.** `MographRenderer(scene3d_mode=...)` : `exact`
(défaut : export, segments d'aperçu fidèle, inchangé), `interactive` (moniteur à l'arrêt ou au défilement) et `frozen` (moniteur en
lecture). `core/scene3d_interactive.py` en porte les règles : en `interactive`, rendu sur le fil principal à 480 px au plus ; en
`frozen`, **aucun rendu** (l'image figée de cet instant, sinon la dernière image rendue pour ce fichier, sinon une carte « Scène 3D ») ;
petit cache LRU (24) des images figées ; un rendu qui échoue donne la carte « Scène 3D indisponible » et un avertissement
(`kut_studio.scene3d`) une seule fois par image, sans exception vers le moniteur. La clé d'image du moniteur (`content_key`,
`layer_key`) porte la taille réduite, le moteur et le mode : jamais prise pour l'image exacte. Une étiquette « Aperçu approximatif »
couvre le moniteur tant qu'un calque 3D y est dessiné par Qt ; quand un segment d'aperçu fidèle prend le relais (même graphe que
l'export), l'étiquette disparaît. **Moteur** : la sonde GPU de `kut3d` (jusqu'à 30 s, sous-processus) est lancée au premier clip 3D vu,
dans un fil Python ordinaire qui ne touche à aucun objet Qt (`ui/scene3d_preview.py` : `GpuProbe`, lue par un `QTimer`) ; en attendant,
numpy en 240 px ; ensuite `auto` (GPU si la sonde a réussi, sinon numpy). Ces images ne servent jamais l'export (le moteur est dans leur
clé de cache). Le moniteur n'a pas de moteur 3D natif : sur une machine sans GPU l'aperçu est plus lent (numpy) et reste figé en lecture.

**Inspecteur.** Un calque scène 3D montre son type, le nom du fichier (chemin complet en infobulle) et « Recharger » (niveau Studio),
qui oublie la signature mémoïsée (`core.scene3d.reload_scene` : le mémo ne se fie qu'à la date et à la taille) et les images figées,
puis redessine le moniteur ; il est désactivé avec le reste de l'éditeur sur un calque ou une piste verrouillés. Aucun contrôle de
texte, de police, de contour, de remplissage, de forme, de lueur, de lumière ni de taille n'est affiché (règle de visibilité par type de
`GraphicsEditor.set_graphic`) ; transformation, opacité, fusion, effets, masques, parent et flou de mouvement restent. La vitesse et le
remappage sont **grisés avec une infobulle** (le modèle les refuse : `ensure_fixed_speed`).

**Pas encore** : éditer la scène (caméra, lumières, modèles) dans l'application, créer une scène depuis un modèle `.obj` / `.glb`. Le fichier se modifie dans un autre outil puis se recharge.

# ADR-0014 : plug-ins déclaratifs (données seulement)

**Statut :** Proposé le 2026-10-29 (l'utilisateur le validera à la relecture de la PR). Étude et plan uniquement : **aucun code applicatif n'est modifié** par cette PR. Les décisions du § 10 sont **ouvertes** : chacune a une option recommandée, aucune n'est tranchée.
**Date :** 2026-10-29
**Décideurs :** mainteneur de Kut-Studio
**S'appuie sur :** [ADR-0005](0005-mode-script-frontiere-de-securite.md) (un fichier ne s'exécute jamais), [ADR-0008](0008-animation-etalonnage-et-effets.md) (registres d'animation, expressions produites par le moteur), [ADR-0011](0011-feuille-de-route-v1.md) (phase 5, lot 32 « Plug-ins déclaratifs »), [ADR-0013](0013-publication-mac-app-store.md) (§ 4 et § 9 : plug-ins déclaratifs seulement, pas de code téléchargé, bac à sable, signets).

## Légende

* **[V]** vérifié dans le code du dépôt à la date de l'ADR (fichier cité) ;
* **[S]** supposé : déduit, non essayé ;
* **[A]** à vérifier (règle Apple ou comportement non mesuré) avant d'en dépendre.

Cet ADR n'invente aucune exigence Apple : ce qui vient de l'App Store est repris d'ADR-0013 avec son étiquette.

## Résumé de la décision (à valider)

1. **Un plug-in est un paquet de données.** Il déclare des contenus que l'application sait déjà rendre (préréglages d'export, looks LUT, préréglages d'effets, modèles de texte, préréglages de transition, préréglages sociaux). Il ne contient **aucun code** : ni Python, ni Swift, ni C++, ni script, ni binaire, ni expression.
2. **Pas de format nouveau pour le contenu.** Un plug-in réutilise les schémas qui existent déjà (§ 1) : le manifeste ne fait qu'**emballer, nommer et borner**. Une seule couche nouvelle : le paquet (manifeste, limites, validation).
3. **Le moteur produit toutes les expressions.** Les expressions FFmpeg / `sendcmd` ne sont écrites que par le code de l'application, à partir de paramètres numériques bornés (§ 2.3).
4. **Tout ou rien.** Un paquet invalide est refusé en entier avec un code d'erreur et ne laisse aucune trace (porte de sortie 1 d'ADR-0011). Les chargeurs actuels de préréglages écartent en silence les entrées abîmées (§ 4) : c'est le bon comportement pour un fichier utilisateur corrompu, **pas** pour un paquet tiers.
5. **Aucun changement du format `.kut`.** Un projet ne référence jamais un plug-in (§ 3.5) : il en copie le résultat, comme il le fait déjà pour un préréglage d'effet ou une LUT.
6. **Aucune place de marché maintenant.** L'installation se fait par un fichier choisi par l'utilisateur (§ 3, § 6).

## Contexte : ce qui est déjà déclaratif

Inventaire fait par lecture du code le 2026-10-29. Colonne « Déclaratif ? » : le contenu est-il déjà une donnée validée, sans code ?

| Contenu | Où | Forme actuelle | Déclaratif ? |
| --- | --- | --- | --- |
| Préréglages d'effets | `core/effects_library.py` : `EffectPreset` (id, nom, description, `EffectCategory`, liste de `ClipEffect`), `_serialize_preset` / `_deserialize_preset`, fichier `effect_presets.json` (`USER_PRESETS_FILE`) | JSON : `{"id", "name", "description", "category", "effects": [{"type", "enabled", "params"}]}` ; les paramètres passent par `core.effects_model.validate_parameters` (inconnu, non numérique ou hors bornes : `ValueError`) | **Oui** [V] |
| Effets disponibles | `core/effects_model.py` : `EffectType` (9 types : correction colorimétrique, flou, netteté, vignette, noir et blanc, sépia, lueur, aberration chromatique, mirage thermique), `EFFECT_PARAMETER_SPECS` (nom, minimum, maximum, défaut) | enum fermé + bornes | **Oui**, mais **fermé** : un plug-in ne peut pas ajouter un `EffectType` [V] |
| Paramètres animables / rendus par expression | `core/effect_targets.py` : `EXPRESSION_EFFECT_PARAMS` (correction colorimétrique : luminosité, contraste, saturation ; vignette : intensité), `SENDCMD_EFFECT_PARAMS` (flou, lueur, aberration chromatique), `ANIMATABLE_EFFECT_PARAMS` | registres de **noms** ; le rendu est du code du moteur (`core/effect_expressions.py`, `core/effect_sendcmd.py`, `core/eq_lut.py`) | Les noms oui, le rendu **non** (il reste dans le moteur, c'est voulu) [V] |
| Looks par LUT | `core/lut_importer.py` : `parse_cube_lut` (`.cube` 3D, taille 2 à 65 : `MIN_LUT_SIZE`, `MAX_LUT_SIZE`), `ffmpeg_readable_lut` ; `core/color_grading.py::copy_lut_into_project` (la LUT est **copiée** dans le dossier du projet à l'enregistrement : `core/project_io.py::_materialize_project_luts`) | fichier texte `.cube`, validé à la lecture | **Oui** [V] |
| Préréglages d'export | `core/render_presets.py` : `RenderPresetSpec` (id, nom, conteneur, codec vidéo, codec audio, largeur, hauteur, fps, qualité, débit audio, encodeur, loudness, copie d'aperçu, couverture), `builtin_presets()`, `get_preset()` ; `__post_init__` valide la combinaison conteneur / codec (`export_format_for`), le codec audio (`SUPPORTED_AUDIO_CODECS = ("aac",)`), largeur et hauteur > 0, fps > 0, loudness −40 à −5 LUFS, encodeur (`coerce_hardware`) | dataclass figée, pas de fichier utilisateur aujourd'hui | **Presque** : forme prête, pas de chargeur de fichier [V] |
| Modèles de texte | `core/text_presets.py` : `TextPreset` (id, nom, description, `TextStyle`, texte par défaut), `_coerce_user_preset`, `TEXT_PRESETS_FILE = "text_presets.json"` ; `TextStyle.from_dict` valide le style | JSON | **Oui** [V] |
| Préréglages de motion / titres animés | `core/mograph_presets.py` : `PRESET_FORMAT = "kut-mograph-preset"`, `PRESET_VERSION = 1`, un fichier `.json` par préréglage dans `presets_directory()`, `clipboard_from_dict` ; **8 préréglages livrés sont des fonctions Python** (`_simple_title`, `_lower_third`, …, `_BUILTINS`) | JSON pour les préréglages utilisateur ; code pour les livrés | **Oui pour les utilisateur** [V], mais la charge utile est un presse-papiers de **calques complets** (clips), donc une surface large, voir § 4 |
| Préréglages de transition | `core/transition_presets.py` : `TransitionPreset` (type, durée par défaut entre `MIN_DURATION = 0.1` et `MAX_DURATION = 5.0`, catégorie), `core/transition_catalog.py::EXTENDED_TRANSITION_CATALOG` (25 types `xfade`) ; `core/transitions.py::TransitionType` | enum **fermé** : les types se relisent depuis la version 21 du `.kut` (`TRANSITION_CATALOG_VERSION`) | Préréglage oui ; **nouveau type de transition non** [V] |
| Préréglages couleur personnels | `Project.color_presets` (`core/project_model.py`, sérialisés par `core/project_io.py`) ; `core/color_grading.py`, `core/color_nodes.py` | embarqués dans le projet | **Oui** [V] |
| Formats sociaux, pistes | `core/social_formats.py` : `SOCIAL_FORMATS` (4 formats), `SOCIAL_FRAME_RATES = (30, 60)`, `TRACK_ROLES` ; `core/social_deliverables.py` | tuples figés | **Presque** (constantes) [V] |
| Modèles de projet | `core/project_templates.py` : `TEMPLATES` (4 modèles) construits par du **code** (`_night_race`, `_city_lights`, `_leaderboard`, `_cta_comments`) ; `core/template_slots.py` (emplacements photo) | fonctions Python | **Non** [V] : hors périmètre tant qu'il n'existe pas de description de modèle en données (ADR-0011 : « enregistrer mon projet comme modèle ») |
| Sons | `core/sfx_synth.py` (SFX **synthétisés** par du code : `whoosh`, `impact`, `riser`), bibliothèque SFX sous `user_data_dir()` (`core/platform_paths.py`) | code + fichiers audio | **Non** pour la synthèse ; les fichiers audio sont des données mais décodés par FFmpeg (§ 4) |

Constats qui pèsent sur la conception :

* **Aucun `eval`, `exec`, `pickle`, `importlib.import_module` ni `yaml.load` dans `core/`, `ui/`, `kut3d/` ni `main.py`** (grep du 2026-10-29 : seules occurrences, `np.load(..., allow_pickle=False)` dans `core/audio_sync_cache.py` et `core/flow_cache.py`, et des `menu.exec` Qt). Les formats existants sont donc déjà des données ; il faut que les plug-ins le restent [V].
* **Aucun préréglage n'est référencé par identifiant dans le `.kut`** : un clip stocke ses effets (type et paramètres), un projet embarque ses LUT et ses préréglages couleur (`core/project_io.py`, lignes `effects`, `color_presets`, `_materialize_project_luts`) [V]. Cela rend la dégradation propre (§ 3.5).
* **Un job de rendu stocke `preset_id`** (`core/render_job.py`, `RenderJob.preset_id`, rempli par `spec.id`) mais reconstruit son `ExportPreset` depuis ses propres champs (largeur, qualité…) : à confirmer qu'un job en attente survit à la disparition du préréglage d'origine **[A]** (à tester à l'étape 1).
* **Il existe déjà une règle d'identifiants d'effet avec points** : `core/effects_model.py::split_fx_property` découpe `fx.<id>.<paramètre>` par la droite et `is_effect_property` compare l'identifiant **exactement**, jamais par préfixe ; un identifiant d'effet peut donc contenir des points (« les effets `a` et `a.b` coexistent ») [V].

## 1. Ce qu'est un plug-in

**Définition.** Un plug-in est un paquet nommé, versionné et signé ou non (§ 3.4) qui contient un manifeste JSON et des fichiers de données, et qui ajoute des **entrées** à des catalogues existants. Il n'ajoute jamais de **capacité** : tout ce qu'il peut produire, l'application sait déjà le produire avec ses propres bornes.

**Types de contenu proposés pour la v1 du format**, du plus sûr au plus risqué (ordre du plan, § 9) :

| Type (`kind`) | Schéma réutilisé | Réutilise | Risque |
| --- | --- | --- | --- |
| `export_preset` | champs de `RenderPresetSpec` | `core/render_presets.py` (validation de `__post_init__`) **plus des bornes manquantes** (§ 4) | faible |
| `lut_look` | fichier `.cube` + nom / description | `core/lut_importer.py::parse_cube_lut` (plus un plafond d'octets, § 4) | faible à moyen (taille) |
| `effect_preset` | liste d'effets `{type, enabled, params}` | `core/effects_library.py::_deserialize_preset` en mode strict | faible |
| `text_preset` | `TextStyle.from_dict` + texte par défaut | `core/text_presets.py::_coerce_user_preset` | faible (polices : § 4) |
| `transition_preset` | `TransitionType` existant + durée | `core/transition_presets.py` (durée 0,1 à 5 s) | faible |
| `social_preset` | format + cadence + pistes parmi l'existant | `core/social_formats.py` | faible |
| `mograph_preset` | presse-papiers de calques | `core/mograph_presets.py::clipboard_from_dict` | **élevé** (charge utile = clips complets) : en dernier, ou refusé |
| `sound_pack` | fichiers audio | `core/sfx_*` | **élevé** (décodage de médias tiers) : décision D6 |

**Ce qu'un plug-in n'est pas** : un nouvel effet de rendu (`EffectType` est fermé, [V]), un nouveau type de transition (le `.kut` le figerait : `TRANSITION_CATALOG_VERSION`), un filtre FFmpeg, un modèle de projet construit par du code (`core/project_templates.py`), un script (ADR-0005).

## 2. Format du paquet

### 2.1 Forme

* **Un fichier `.kutplugin`** : archive ZIP, extension et type de document à confirmer (D2). Un **dossier** de même contenu est accepté en développement (commande de validation, étape 1) mais n'est pas un format de distribution.
* Contenu : `manifest.json` à la racine, puis des fichiers de données dans des sous-dossiers à plat (`presets/`, `luts/`). Pas de dossier caché, pas de lien.
* **Lecture sans extraction** : le chargeur lit chaque membre en mémoire avec un plafond d'octets, il n'écrit rien tant que tout n'est pas validé (porte de sortie : « ne laisse rien »). Python `zipfile` [S] : à tester pour les plafonds réels.

### 2.2 Manifeste (JSON, UTF-8, versionné)

```json
{
  "format": "kut-studio-plugin",
  "format_version": 1,
  "id": "com.exemple.looks-cine",
  "name": "Looks cinéma",
  "version": "1.2.0",
  "author": "Exemple",
  "license": "CC-BY-4.0",
  "min_app_version": "0.9.0",
  "contents": [
    {"kind": "lut_look", "id": "chaud", "name": "Chaud", "file": "luts/chaud.cube", "sha256": "…"},
    {"kind": "export_preset", "id": "web-1080", "name": "Web 1080p", "data": {"container": "mp4", "video_codec": "h264", "width": 1920, "height": 1080, "fps": null, "quality": 23}}
  ]
}
```

Choix de forme : `format` / `format_version` suivent `core/project_io.py::FORMAT_NAME = "kut-studio-project"` et `core/mograph_presets.py::PRESET_FORMAT` / `PRESET_VERSION` [V] (même style, même rôle). Le champ `data` d'un type reprend **tel quel** le schéma existant du type (§ 1) : pas de second dialecte à maintenir.

**Règles de validation du manifeste** (toutes obligatoires, échec = paquet refusé en entier, avec un code d'erreur stable, par exemple `PLUGIN_MANIFEST_TOO_LARGE`, `PLUGIN_PATH_OUTSIDE`, `PLUGIN_UNKNOWN_KIND`) :

| Règle | Valeur proposée | Pourquoi |
| --- | --- | --- |
| Taille du manifeste | ≤ 256 Kio | entrées démesurées |
| Profondeur JSON | ≤ 8 niveaux, vérifiée **avant** `json.loads` récursif complet ou par un analyseur borné [S : `json.loads` de Python plafonne par la récursion, ne pas s'y fier] | JSON bomb |
| Nombre d'entrées `contents` | ≤ 200 par paquet | listes sans maximum |
| Longueur des chaînes | `id` ≤ 64, `name` ≤ 80, `description` ≤ 400 caractères | message d'erreur qui ne recopie pas l'entrée (extrait tronqué, comme `core/lut_importer.py::_excerpt`) [V] |
| Clés inconnues | refus (pas d'ignorance silencieuse) | un paquet qui « marche » sur une version future et pas sur l'autre |
| Nombres | finis (pas de NaN / Inf), types stricts (`bool` n'est pas un nombre) | `validate_parameters` doit déjà refuser, le test le prouve |
| `format_version` | exactement connue ; supérieure : refus clair « mettez l'application à jour » | compatibilité (§ 3.5) |
| `min_app_version` | comparée à `core/app_version.py::APP_VERSION` | idem |

### 2.3 Aucune expression exécutable : le moteur écrit les expressions

C'est la règle centrale, calquée sur ADR-0005 et ADR-0008.

* Un plug-in ne transporte **aucune** chaîne destinée à FFmpeg : ni filtre, ni expression `eval`, ni ligne `sendcmd`, ni `-vf`. Les chaînes de filtres et les fichiers de commandes sont construits par `core/filter_graph.py`, `core/effect_expressions.py`, `core/effect_sendcmd.py`, `core/eq_lut.py` à partir de **nombres** déjà bornés par `EFFECT_PARAMETER_SPECS` [V].
* Une image clé d'effet dans un plug-in (si on les autorise un jour) n'est qu'une liste de `(temps, valeur)` pour un paramètre de `ANIMATABLE_EFFECT_PARAMS` ; le volume des commandes est borné par `core/effect_sendcmd.py::MAX_EFFECT_COMMANDS = 40_000` instructions et `MAX_COMMAND_BYTES = 32 Mio` : le plan **complet** est vérifié avant d'écrire le moindre fichier [V]. Un plug-in n'a donc aucune façon de dépasser ce budget : il ne fournit pas d'instructions, il fournit des images clés que le moteur étend, puis compte.
* **Valeurs de chaîne qui atteindraient quand même une ligne de commande** : `audio_bitrate` de `RenderPresetSpec` est une chaîne FFmpeg libre (`"192k"`) que `__post_init__` **ne valide pas** [V] ; un plug-in ne la fournit donc pas en texte libre mais comme **entier** (kbit/s, 64 à 512) ; le moteur formate lui-même `"<n>k"`. Même principe pour tout champ numérique transformé en texte.
* **Jamais de correspondance d'identifiants par préfixe** (leçon d'ADR-0008 : `split_fx_property` / `is_effect_property` comparent exactement, `core/effects_model.py`) : les identifiants de plug-ins sont comparés par **égalité**, sur des ensembles (`frozenset`, comme `builtin_preset_ids()` dans `core/effects_library.py`), jamais par `startswith`.

## 3. Installation, emplacement, mise à jour, identité

### 3.1 Emplacement

* Les paquets installés vivent sous **`user_data_dir()/plugins/<id-du-plug-in>/<version>/`** (`core/platform_paths.py::user_data_dir`) : dans l'application sandboxée, ce dossier est dans le **conteneur** (ADR-0013 : tout chemin hors conteneur est refusé en lecture et en écriture, mesuré) [V pour l'API, A pour le chemin final dans le conteneur].
* On y range le résultat **normalisé** (manifeste ré-écrit par l'application, fichiers recopiés), pas l'archive d'origine. Le dossier est **revalidé à chaque démarrage** par le même code : une copie installée n'est jamais crue sur parole (un utilisateur ou un autre programme peut l'avoir modifiée).
* Un paquet reste lisible quand l'utilisateur supprime l'original : l'installation **copie**, elle ne référence pas.

### 3.2 Installation par un fichier choisi

* L'utilisateur choisit le `.kutplugin` dans un panneau de fichiers (`QFileDialog`, qui passe par `NSOpenPanel` sur macOS : le droit `files.user-selected.read-write` d'ADR-0013 §1) [V pour l'usage de `QFileDialog` dans l'application, A pour le droit].
* Comme le paquet est **lu une fois puis copié dans le conteneur**, **aucun signet à portée de sécurité n'est nécessaire** pour les plug-ins (contrairement aux médias : ADR-0013 étape 5). C'est un avantage direct du choix « copie » ; il ne tient que si la lecture se fait pendant l'accès accordé par le panneau [A, à mesurer à l'étape 4].
* Double-clic dans le Finder : exige un type de document (`CFBundleDocumentTypes`, déjà présent pour `.kut`, `build.py::macos_info_overrides`, [V]) ; **hors v1**, décision D2.
* Confirmation avant installation : nom, auteur, version, liste des contenus, taille, « non signé / signé par … » (§ 3.4). Rien d'installé tant que l'utilisateur n'a pas confirmé.

### 3.3 Désinstallation et mise à jour

* **Désinstaller** : supprimer le dossier du plug-in (opération de l'application, jamais un `rm -rf` sur un chemin construit à partir du manifeste : le dossier se déduit de l'identifiant **validé**). Les projets déjà faits ne changent pas (§ 3.5).
* **Mise à jour manuelle** : réinstaller un paquet de même `id` et de version supérieure remplace l'ancien, après confirmation. **Aucune mise à jour automatique** : le build Store retire le canal (ADR-0013 § 3 : la mise à jour passe par l'App Store, pas par l'application) et un plug-in qui se met à jour seul serait précisément le « code téléchargé qui change les fonctions » que la règle 2.5.2 vise [A pour la règle, voir ADR-0013 § 6]. Même sans code, **ne pas rendre l'application capable de se modifier à l'insu de l'utilisateur** est le choix prudent.
* Version identique ou inférieure : refusée avec explication (pas de retour arrière silencieux).

### 3.4 Identité et signature (proposition, pas une décision : D4)

| Option | Ce qu'elle garantit | Coût | Réutilise |
| --- | --- | --- | --- |
| **A. Intégrité seule** | chaque fichier est vérifié par `sha256` listé au manifeste (un paquet tronqué ou abîmé est refusé) ; **pas d'auteur vérifié** | très faible | `hashlib` |
| **B. A + signature Ed25519 pour les paquets officiels** | l'application reconnaît « Livré par Kut-Studio » | moyen : gestion d'une clé | `core/ed25519.py`, `core/update_signature.py` (signature des mises à jour, ADR-0012) [V] |
| **C. B + clés d'auteurs (confiance à la première utilisation)** | « même auteur qu'avant » | élevé : révocation, perte de clé | idem |

**Recommandation : A pour la v1, B quand un premier paquet officiel existe, C seulement s'il y a un dépôt public (D7).** Dans tous les cas l'interface dit « non signé » en clair : l'intégrité n'est pas l'authenticité, et ni l'une ni l'autre ne remplace la validation de contenu du § 4 (un paquet signé par son auteur peut quand même être malveillant ou bogué).

### 3.5 Compatibilité avec le format `.kut` : ne pas y toucher

**Aucune modification du format `.kut`.** Conséquences, vérifiées :

* Un clip stocke les **effets résolus** (type et paramètres) ; une LUT est **copiée dans le dossier du projet** à l'enregistrement (`core/project_io.py::_materialize_project_luts`) ; les préréglages couleur sont embarqués (`Project.color_presets`) [V]. Ouvrir un projet fait avec un plug-in **absent** (autre machine, plug-in désinstallé) donne donc le **même rendu** : le plug-in n'a servi qu'à remplir ces champs.
* Une transition réutilise un `TransitionType` existant (sa version de fichier est déjà gérée : `TRANSITION_CATALOG_VERSION`) ; un plug-in ne crée pas de type.
* Un modèle de texte appliqué est un `TextStyle` copié dans le clip [S : à confirmer par le test de l'étape 3].
* Le seul identifiant de plug-in qui peut survivre quelque part est le `preset_id` d'un job de rendu en file (`core/render_job.py`). Règle : un job dont le préréglage a disparu **garde ses valeurs** ou est refusé avec un message clair, **jamais** réécrit en silence avec un autre préréglage [A, test à l'étape 1].
* Si un jour un projet devait mémoriser « fait avec le plug-in X » (affichage, provenance), ce serait un **changement de format**, donc une demande explicite de l'utilisateur et une version de fichier à part : **hors périmètre de cet ADR**.

### 3.6 Identifiants et collisions

* `id` de plug-in : minuscules, chiffres, `.` et `-`, notation de domaine inversé, ≤ 64 caractères. `id` de contenu : unique **dans** le plug-in.
* L'identifiant **effectif** d'un contenu est `<id-plug-in>/<id-contenu>`. Il ne peut jamais valoir (ni sembler valoir) un identifiant natif : les identifiants natifs sont `builtin_preset_ids()` (`core/effects_library.py`), `builtin_text_preset_ids()` (`core/text_presets.py`), `builtin_text_presets`, les `id` de `core/render_presets.py::builtin_presets()` (par exemple `h264_1080p`, `DEFAULT_PRESET_ID`), `core/social_formats.py::SOCIAL_FORMATS` (`vertical`, `portrait`, `square`, `landscape`). La comparaison est **exacte**, sur ensemble, avant installation, et un id de plug-in contenant `/` est impossible pour un natif (aucun id natif n'en contient : à garder par un test).
* Deux plug-ins d'`id` différents qui déclarent un contenu de même nom restent distincts (affichage « nom — plug-in »). Un nom affiché ne se déduplique jamais par suffixe (« Cinéma 2 ») : si un nom collisionne avec un natif, l'entrée s'affiche avec le nom du plug-in, ou le paquet est refusé (D5).
* Un plug-in ne peut **pas masquer ni remplacer** un préréglage natif.

## 4. Modèle de menace et validations

On suppose qu'un paquet peut être écrit par un adversaire (ou simplement par quelqu'un qui se trompe) et qu'il est ouvert par un utilisateur de bonne foi. Sont **hors modèle** : un attaquant qui contrôle déjà le disque de l'utilisateur ou l'application elle-même.

| Menace | Mécanisme | Défense |
| --- | --- | --- |
| **Bombe ZIP** (petite archive, énorme contenu) | un membre se décompresse à des Go | lecture bornée membre par membre ; plafonds : taille décompressée totale ≤ 64 Mio (D3), par membre ≤ 16 Mio (cf. `core/user_settings.py::MAX_FILE_BYTES = 16 * 1024 * 1024` [V]), nombre de membres ≤ 512, **rapport de compression** ≤ 100 ; l'archive compressée ≤ 32 Mio |
| **JSON bomb** (profondeur, tableaux immenses, nombres géants) | `json.loads` sur un manifeste de plusieurs Mo | taille du manifeste plafonnée **avant** lecture (≤ 256 Kio), profondeur ≤ 8, comptes bornés (§ 2.2) |
| **Chemins hors paquet** | `../`, chemin absolu, `\`, octet NUL, lien symbolique, chemin Unicode normalisé différemment (APFS ne distingue pas la casse), doublons à la casse près | refus de tout membre dont le chemin n'est pas relatif, plat, en ASCII sûr (`[a-z0-9._-]` + `/` entre dossiers fixes), refus des liens (attributs ZIP), comparaison après normalisation et mise en minuscules. Jamais d'extraction brute ; l'application écrit elle-même sous un dossier dérivé de l'`id` validé |
| **Usurpation d'identifiants natifs** | un paquet déclare `h264_1080p` ou un id d'effet qui ressemble à un natif | § 3.6 : id effectif préfixé, comparaison exacte sur ensembles, jamais par préfixe (leçon ADR-0008) ; tests : `a` et `a.b`, casse, espaces, Unicode |
| **LUT géante** | `parse_cube_lut` lit `file_path.read_bytes()` **sans plafond d'octets** [V `core/lut_importer.py`] ; seule la taille de grille (≤ 65, soit jusqu'à 274 625 lignes) est bornée, **après** la lecture | plafond d'octets **avant** lecture (≤ 16 Mio) ; ligne unique démesurée refusée (`_MAX_ROW_VALUES`, `_excerpt` tronque les messages [V]) ; la LUT est copiée dans le conteneur, jamais lue depuis un chemin du paquet resté ailleurs |
| **Préréglage d'export abusif** | `RenderPresetSpec.__post_init__` valide la combinaison et les signes, mais **ne borne ni largeur, ni hauteur, ni qualité, ni débit audio** [V `core/render_presets.py`] | bornes propres aux plug-ins, **avant** construction : largeur et hauteur de 16 à 8192 [S : plafond à arrêter à l'étape 1 selon ce que le moteur sait vraiment encoder], qualité dans la plage du codec, débit audio entier 64 à 512 kbit/s, fps 1 à 120 ; le préréglage doit en plus passer le contrôle de capacités d'encodeur du Store (`core/video_encoders.py`, `core/hardware_validation.py`) |
| **Valeurs du modèle ramenées en silence** | `_apply_property_values` ramène à la borne ; un préréglage qui « marche » avec une valeur écrêtée trompe l'auteur | un paramètre hors bornes d'`EFFECT_PARAMETER_SPECS` **refuse** le paquet (pas de correction) |
| **Paquet partiellement valide** | `_deserialize_preset` **garde** un préréglage dont certains effets sont invalides (il écarte les autres) et `load_user_presets` jette le fichier entier si le JSON est corrompu [V `core/effects_library.py`] : bon pour un fichier utilisateur, trompeur pour un paquet | chargeur strict pour les plug-ins : une seule erreur = paquet refusé, avec le code d'erreur et l'entrée en cause (extrait tronqué). Les fonctions existantes ne changent pas (comportement de l'interface inchangé) ; on ajoute un mode strict à valeur par défaut non strict |
| **Modèle de texte hostile** | texte par défaut ou nom de police très long, caractères de contrôle, texte qui sert plus tard dans un nom de fichier ou un fichier de sous-titres | longueurs bornées, caractères de contrôle refusés ; le texte n'est jamais interprété (ni FFmpeg `drawtext`, ni format) : à vérifier que le rendu texte passe par le rastériseur Qt, pas par `drawtext` [A, voir `docs/motion-graphics.md`] |
| **Police fournie par le paquet** | une police est un binaire lu par une bibliothèque (surface d'attaque connue) | **les plug-ins ne contiennent pas de polices** en v1 ; ils nomment une police déjà présente (Anton, Saira : `assets/fonts`, `core/bundled_fonts.py`, licence SIL OFL [V]) ou du système |
| **Préréglage de motion = clips complets** | `clipboard_from_dict` charge un presse-papiers de calques : chemins de médias, liens, suivis, scènes 3D | **exclu de la v1** (D6) ; si admis plus tard : liste blanche de champs, aucun chemin de média, aucune référence à un autre projet, profondeur de calques bornée (graphe : profondeur et cycles sur le graphe résultant, `core/graph_cycles.py`) |
| **Sons** | fichiers audio décodés par FFmpeg : surface d'un décodeur de médias tiers | **exclu de la v1** (D6) ; si admis : uniquement WAV PCM, durée et taille bornées, transcodés par l'application dans le conteneur |
| **Liens / réseau** | un champ `url`, une image distante, un « lien d'achat » | **aucun champ de type URL n'est lu ni suivi** ; le build Store n'a pas `network.client` (ADR-0013 § 1) ; un `homepage` éventuel est du texte affiché, non cliquable en v1 |
| **Identité usurpée** (« Livré par Kut-Studio ») | un champ `author` libre | la mention « officiel » n'est affichée que si la signature (§ 3.4 B) est valide ; `author` est du texte libre non fiable |
| **Collision de noms de dossier à l'installation** | deux plug-ins `a/b` vs `a.b`, ou un id qui se résout en `..` | id validé par motif strict, dossier = id + version validés, jamais d'id contenant `/`, `\`, `..` |
| **Plug-in lourd** | mille LUT de 8 Mio | quota global de plug-ins installés (D3, proposition : 256 Mio) et par plug-in (64 Mio) ; refus clair |
| **Entrées qui retombent sur l'interface** | liste cliquable de plug-ins dans un panneau, tri, recherche | les mêmes listes que le reste de l'interface (`filter_presets`, `filter_text_presets`) ; borne du nombre d'entrées affichées |

Tests exigés (règle du dépôt : un test de correction doit **échouer** sans le correctif) : un test par ligne du tableau avec un paquet fabriqué, à retirer la ligne de garde pour vérifier que le test casse ; un test « cas courant » (un paquet valide d'un préréglage d'export et d'une LUT) ; un test d'un paquet **absent** à l'ouverture d'un projet ; un test que le comportement **de l'interface existante** ne change pas (chargeurs actuels, mode non strict).

## 5. Ce que cela exclut explicitement

* **Aucun code fourni par un tiers, jamais, par ce mécanisme** : pas de Python (ni `.py`, ni `.pyc`, ni module importé), pas de Swift, pas de C / C++ / Rust, pas de bibliothèque dynamique (`.dylib`, `.so`), pas de script (shell, AppleScript, JavaScript, Lua), pas de binaire, pas d'expression FFmpeg ou `sendcmd`, pas de shader (même `.qsb` : les shaders sont des données du bundle, signés avec l'application, ADR-0013 § 4 [V]), pas d'outil externe.
* **Pas de lancement de processus** déclenché par un plug-in : `core/process_supervisor.py` reste le point unique et un plug-in n'y ajoute aucune commande (`tests/test_process_launch_guard.py` [V par ADR-0013]).
* **Pas de crochets de comportement** (ni « à l'ouverture du projet », ni « avant l'export ») : un plug-in est passif, il n'agit que quand l'utilisateur applique un de ses contenus.
* **Pas de plug-ins qui lisent le projet** : un plug-in ne reçoit jamais le projet de l'utilisateur.
* **Pas de mise à jour automatique, pas de téléchargement par l'application** (§ 3.3).

**Voie future éventuelle (hors périmètre de cet ADR, à ne pas préparer dans le code maintenant).** L'extensibilité par du code existe déjà **sous une autre forme** : le mode script (ADR-0005), où du Python ne s'exécute que parce que l'utilisateur l'a demandé et jamais à l'ouverture d'un fichier ; il n'est pas distribué dans le Store tel quel (ADR-0013 : CLI non livré). Si un jour des « extensions à code » étaient voulues, ce serait un ADR séparé qui devrait résoudre avant tout la règle 2.5.2 [A], le bac à sable (un interpréteur tiers n'a pas accès aux fichiers) et la signature de chaque binaire embarqué. Rien dans le format du paquet ne doit l'anticiper : le champ `format_version` suffit à laisser la porte fermée proprement.

## 6. Distribution

* **Maintenant : aucune place de marché, aucun compte, aucun catalogue en ligne.** L'utilisateur reçoit un `.kutplugin` par le moyen de son choix et l'installe par fichier (§ 3.2). C'est conforme à ADR-0011 (« Hors v1 : magasin en ligne, compte utilisateur »).
* **Contenus livrés avec l'application** (catalogue d'ADR-0011 : LUT livrées, transitions de créateur) : ce sont des plug-ins officiels embarqués dans le bundle (donc signés avec lui), chargés par le **même** code que les plug-ins installés. Un seul chemin de chargement = un seul chemin testé. Chaque contenu livré a sa licence listée (porte de sortie 3 d'ADR-0011) : champ `license` obligatoire, vérifié par un test.
* **Dépôt public de plug-ins** (idée à trancher, D7) : un dépôt de fichiers, sans serveur applicatif, où chacun propose un paquet par PR et où une CI exécute le **même validateur** (commande `validate`). Il n'impose aucun accès réseau à l'application (l'utilisateur télécharge dans son navigateur). Si on veut un jour un navigateur de plug-ins **dans** l'application, cela demande `com.apple.security.network.client` (ADR-0013 § 1, § 9), un examen des règles 2.5.2 et 5.1.2 [A] et un ADR à part.

## 7. Alternatives écartées

| Alternative | Pourquoi écartée |
| --- | --- |
| Plug-ins Python (ou en bibliothèque dynamique) | interdit par 2.5.2 pour du code téléchargé qui change les fonctions [A, ADR-0013] ; impossible dans un bac à sable sans accès aux fichiers de l'extension ; casse la règle « un fichier ne s'exécute jamais » (ADR-0005) |
| Langage d'expressions « sûr » dans le manifeste (filtres personnalisés, formules) | c'est du code, avec les mêmes risques (boucles, coûts, injection dans FFmpeg) ; le moteur borne déjà ses expressions (`MAX_EFFECT_COMMANDS`) *parce qu'il les écrit lui-même* |
| Ajouter des types d'effets et de transitions via plug-in | `EffectType` et `TransitionType` sont fermés, et les transitions se relisent par version du `.kut` : ce serait un changement de format |
| Un nouveau format de préréglage, spécifique aux plug-ins | double maintenance ; les schémas existants sont déjà validés et testés (§ 1) |
| Référencer le plug-in dans le projet | changement de format (interdit sans demande) ; les projets se dégradent mal si le plug-in manque ; l'état actuel (copie du résultat) est strictement meilleur |
| Mise à jour automatique des plug-ins | « code qui change les fonctions » (même sans code), pas de réseau dans le Store, coût de signature |

## 8. Conséquences

* **Positives** : extensibilité sans surface d'exécution ; un seul validateur pour les contenus livrés et tiers ; aucun changement du `.kut` ; conforme au Store ; un paquet invalide ne laisse rien.
* **Négatives** : les plug-ins ne peuvent pas ajouter de capacité de rendu (nouveaux effets, nouvelles transitions) ; le chargeur strict est un second chemin à côté des chargeurs tolérants ; plafonds à définir et à tester (zip, LUT, export) ; la valeur de l'écosystème dépend de l'existence de contenus (sans catalogue, peu d'incitation).
* **Pour les chantiers suivants** : tout nouveau type de contenu proposé en plug-in passe par la liste du § 4 avant d'être accepté ; tout nouveau champ de préréglage ajouté plus tard doit avoir ses bornes dans le même PR (leçon du préréglage d'export, § 4).

## 9. Plan par étapes (une étape = une PR)

Tailles : S ≈ 1 jour, M ≈ 2 à 4 jours, L > 4 jours. Chaque étape se termine par `python -m tools.i18n_audit`, `-k "modular or guard or ratchet"`, mypy et la suite complète, plus une ligne `CHANGELOG.md`.

| # | Étape | Taille | Contenu | Dépend de |
| --- | --- | --- | --- | --- |
| 1 | **Paquet et validateur, sans interface** : `core/plugin_package.py` (lecture ZIP bornée, manifeste, codes d'erreur, id effectif), sans Qt ; **premier type : `export_preset`** (bornes propres, § 4), enregistré dans un catalogue en mémoire ; commande de validation d'un paquet (dossier ou `.kutplugin`) dans le CLI hors Store | M | porte de sortie 1 (« invalide : refusé, ne laisse rien ») ; tests de menace du § 4 (zip, JSON, chemins, usurpation d'id) ; test d'un job de rendu dont le préréglage a disparu | — |
| 2 | **Premier look : `lut_look`** : plafond d'octets ajouté à `parse_cube_lut` par un paramètre optionnel à valeur par défaut `None` (l'interface ne change pas), copie dans `user_data_dir()/plugins/…`, utilisation comme LUT d'un clip (la copie dans le projet existe déjà) | S–M | test « ouvrir un projet fait avec un look dont le plug-in est absent » (rendu identique) | 1 |
| 3 | **Installation, liste, désinstallation** (panneau « Plug-ins » : choisir un fichier, confirmer, installer, retirer ; revalidation au démarrage ; textes fr / en / es) | M | aucune signature encore ; états de l'interface : paquet invalide, version trop récente, collision de nom | 1 |
| 4 | **Chemin Store** : lecture pendant l'accès accordé par `NSOpenPanel`, copie dans le conteneur, sans signet ; vérification sandbox à la main (journal, `Operation not permitted`) | S | mesure réelle sur le `.app` Store | 3, ADR-0013 étape 5 (couche de fichiers) |
| 5 | **Types texte et effet** : `text_preset`, `effect_preset` (chargeur strict à côté du chargeur tolérant), `transition_preset`, `social_preset` | M | tests « par préfixe » (`a` et `a.b`), bornes de paramètres | 1 |
| 6 | **Contenus livrés comme plug-ins officiels** (premier paquet de LUT et de transitions d'ADR-0011, licences listées) ; signature Ed25519 (option B du § 3.4) si D4 le décide | M | porte de sortie 3 d'ADR-0011 : un test vérifie la licence de chaque contenu | 2, 5 |
| 7 | **Au besoin : `mograph_preset`** (liste blanche de champs) et/ou `sound_pack` (WAV PCM) | L | seulement si D6 le décide | 5 |
| 8 | **Documentation** : `docs/plugins.md` (format, limites, exemple de paquet, codes d'erreur), mise à jour d'ADR-0011 (lot 32) | S | peut être faite avec l'étape 1 | 1 |

**Premier pas : étape 1** — charger un **préréglage d'export** depuis un paquet, puis l'étape 2 (un look LUT) : les deux sont déjà des données validées, avec le moins de surface.

## 10. Décisions ouvertes pour l'utilisateur

Aucune n'est tranchée. Chaque ligne donne les options et une recommandation.

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| **D1** | Quels types de contenu dans la v1 du format ? | (a) export + LUT seulement ; (b) a + effets, texte, transitions, sociaux ; (c) tout, y compris motion et sons | **(a) puis (b)** en étapes séparées (plan § 9) ; motion et sons à part (D6) |
| **D2** | Forme et nom du paquet | (a) ZIP `.kutplugin` + dossier en dev ; (b) dossier seul ; (c) JSON unique sans fichiers (pas de LUT) ; double-clic Finder en v1 ? | **(a)**, **sans** double-clic en v1 (installation par menu) |
| **D3** | Plafonds | proposition : manifeste 256 Kio, membre 16 Mio, paquet 32 Mio compressé / 64 Mio décompressé, 512 membres, 200 contenus, rapport de compression 100, quota global 256 Mio | **ces valeurs**, à ajuster après la mesure de l'étape 1 |
| **D4** | Identité des paquets | A intégrité seule ; B + signature des paquets officiels ; C + clés d'auteurs | **A maintenant**, **B** avec les contenus livrés (étape 6), C seulement avec un dépôt public |
| **D5** | Collision de nom d'affichage avec un natif | (a) refuser le paquet ; (b) afficher « nom — plug-in » ; (c) suffixe automatique | **(b)** ; **jamais (c)** (le modèle ne corrige pas en silence) ; l'identifiant effectif, lui, ne collisionne jamais |
| **D6** | Motion (calques complets) et sons dans les plug-ins | (a) exclus définitivement ; (b) exclus de la v1, ADR de suivi ; (c) inclus avec liste blanche | **(b)** : la surface est celle d'un projet complet (motion) ou d'un décodeur de médias tiers (sons) |
| **D7** | Distribution | (a) fichier à la main seulement ; (b) dépôt public de paquets avec CI de validation ; (c) navigateur intégré (réseau) | **(a) maintenant**, **(b)** quand il existe 5 à 10 paquets officiels ; (c) hors périmètre (réseau, 2.5.2 / 5.1.2 [A]) |
| **D8** | Un projet doit-il se souvenir du plug-in d'origine ? | (a) non (copie du résultat, format inchangé) ; (b) oui (nouveau champ, version de fichier) | **(a)** ; (b) est un changement de format, à demander explicitement |
| **D9** | Plug-ins dans le niveau d'usage (ADR-0006, `docs/usage-levels.md`) | visibles en Simple / Studio / Avancé | **Avancé** d'abord (le panneau d'installation), les contenus installés visibles partout où leur type l'est |
| **D10** | Build non Store (GitHub / développement) : mêmes règles ? | (a) mêmes règles, un seul chemin ; (b) dossiers libres en développement | **(a)** : un seul validateur, la commande `validate` du CLI sert aussi de test de plug-in |

## Références code citées

`core/effects_library.py`, `core/effects_model.py`, `core/effect_targets.py`, `core/effect_expressions.py`, `core/effect_sendcmd.py`, `core/eq_lut.py`, `core/lut_importer.py`, `core/color_grading.py`, `core/render_presets.py`, `core/render_job.py`, `core/text_presets.py`, `core/mograph_presets.py`, `core/transition_presets.py`, `core/transition_catalog.py`, `core/transitions.py`, `core/social_formats.py`, `core/project_templates.py`, `core/template_slots.py`, `core/sfx_synth.py`, `core/project_io.py`, `core/project_model.py`, `core/user_settings.py`, `core/platform_paths.py`, `core/process_supervisor.py`, `core/ed25519.py`, `core/update_signature.py`, `core/app_version.py`, `build.py`.

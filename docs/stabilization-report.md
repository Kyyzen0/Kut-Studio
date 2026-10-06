# Rapport de stabilisation

Phase de **fiabilité, cohérence et robustesse** de Kut-Studio. Aucune grosse fonctionnalité n'a été ajoutée
(ni multicam, ni optical flow, ni API de plugins, ni transcription, ni IA, ni 3D) : le but était de retrouver et de
fermer les défauts laissés par l'empilement des systèmes récents (keyframes, séquences imbriquées, motion graphics,
tracking, proxies, décodage et rendu GPU, file de rendu, encodage matériel, raccourcis, caches, historique, `.kut`).

Où chaque information vit, et quel test la garde : [architecture.md](architecture.md).

## En bref

* **Tout défaut corrigé l'a été avec un test qui échoue sans la correction** (vérifié en retirant la correction, puis en la
  remettant). Les tests de rendu lancent le vrai FFmpeg et relisent des pixels ; les anciens tests qui ne faisaient que
  comparer des chaînes de caractères, et qui figeaient parfois le défaut, ont été réécrits.
* **Critique : 6 trouvés, 6 corrigés.** Haute : 15 trouvés, 15 corrigés. Moyenne : 18 trouvés, 18 corrigés (le tracking
  après la coupe de sa source, d'abord mitigé par un message, est maintenant corrigé).
* **Une seconde phase a fermé les points ouverts** de la première (section [Phase 2](#phase-2)) : des 11 points ouverts
  (5 de gravité moyenne, 6 de gravité faible), **8 sont corrigés, 2 sont mitigés, 1 reste ouvert** ; elle a aussi trouvé et
  corrigé des défauts qui n'étaient pas dans la liste (sous-titres ASS, pannes absentes du journal, boutons par défaut des
  dialogues…). **Ce qui n'est pas réglé est listé plus bas**, sans l'atténuer (section [Points ouverts](#points-ouverts)).
* État de fin : `ruff` propre, `mypy` propre sur `core/` (dette ramenée de 41 à 17 modules, et verrouillée par un test),
  suite complète verte (3 445 réussis, 20 sautés), smoke test de l'interface et de l'application empaquetée, CI sur trois
  plateformes **et** job `macos-libass` verts sur les deux premières PR de la phase 2 — voir [CI](#ci) pour le détail.

## Méthode

1. **Cartographie d'abord** : sous-systèmes, sources de vérité multiples, chemins de données (`architecture.md`).
2. **Audit ciblé par domaine** : aperçu / export, séquences imbriquées, keyframes, motion graphics, tracking, GPU, décodage,
   caches et proxies, historique, `.kut`, mémoire, threads et processus, démarrage et fermeture, interface, CI.
3. **Reproduire avant de corriger** : chaque défaut est d'abord constaté par une mesure ou une exécution (une sonde, un
   rendu FFmpeg, un comptage), jamais par intuition. Certains constats de l'audit se sont révélés faux ou périmés au
   moment de les vérifier (les actions de la bibliothèque sont déjà dans l'historique ; « annuler » clôt déjà les saisies
   différées) : ils ne figurent pas ici.
4. **Un correctif, un test qui échoue sans lui** : les tests de parité exportent avec le vrai FFmpeg depuis des sources
   rouge / bleu / vert et lisent la couleur à des instants précis ; le `.kut` est muté champ par champ ; les threads sont
   mis en contention ; les fichiers tenus ouverts sont simulés.
5. **Honnêteté sur l'environnement** : un test qui dépend d'une capacité de la machine (libass, GPU) se saute avec une
   raison explicite au lieu d'échouer ou de passer à vide.

## Défauts corrigés

Chaque ligne cite le commit de la branche.

### Critique — l'application ne fait pas ce qu'elle annonce, ou perd le travail

| Défaut constaté | Mesure / conséquence | Commit |
| --- | --- | --- |
| **Export impossible** dès qu'un clip vidéo avait une vitesse ≠ 1 ou un arrêt sur image | FFmpeg refusait le graphe (`atempo`, un filtre audio, dans la chaîne vidéo ; `select=eq(n,…)` avec une virgule non échappée). Les tests comparaient des chaînes et figeaient l'erreur | `28d9c6f` |
| **Scopes totalement inopérants** (waveform, histogramme, vectorscope) | trois défauts à la suite : décodage PNG refusé par PySide6 (`QBuffer`), résultat jamais livré au thread Qt (`QTimer.singleShot` depuis un thread sans boucle), image extraite à un instant faux (`-ss` en temps de timeline : noir ou mauvaise couleur) | `1d101d8`, `3ba336f` |
| **Couleurs exportées décalées** | l'export convertissait RVB → YUV en BT.601 sans balise : le rouge `(221, 92, 29)` revenait `(229, 98, 20)` décodé comme tout lecteur le fait (+11 niveaux). Étape BT.709 + balises ; puis propriétés posées sur les images (`setparams`), car sous macOS et Windows seules la matrice sortait balisée | `cb75e4e`, `e5a6889` |
| **Pertes de données `.kut`** | automation audio effacée au premier réglage après réouverture ; faux « Enregistré » après annulation puis édition ; fichier abîmé laissant la fenêtre à moitié chargée ; `NaN`/`Infinity` acceptés puis projet impossible à enregistrer, sans message | `96ebeec` |
| **Travail perdu en silence** | fermer, créer un nouveau projet ou en ouvrir un autre écrasait un projet modifié sans rien demander ; `closeEvent` s'arrêtait à la première étape en échec (FFmpeg, autosave, threads survivants) | `e9b1e29` |
| **Démarrage cassé** par un cache matériel mal formé (`{"schema": 2, "encoders": 5}`) : la fenêtre ne s'ouvrait plus tant que le fichier n'était pas supprimé à la main | `TypeError` au démarrage | `97e3072` |

### Haute

| Défaut constaté | Conséquence | Commit |
| --- | --- | --- |
| Trims faux avec vitesse ≠ 1, lecture inversée ou arrêt sur image | ×2, trim gauche à 1 s : la fin glissait de 5 à 5,5 s ; reverse : la queue était coupée à la place de la tête ; freeze : le trim ne faisait rien | `7650dbd` |
| Empreinte des segments d'aperçu incomplète | pan, fondus, effets audio, automation, ducking, styles de sous-titres et fader maître oubliés : le cache (7 jours) resservait l'ancien son ou style. `RENDER_ENGINE_VERSION` ajouté | `ef8d217` |
| Apostrophes dans les chemins de filtres FFmpeg | un dossier « Jean d'Arc » ou « O'Brien » (profil Windows) cassait sous-titres, LUT et polices, en aperçu comme à l'export | `9317ee7` |
| Ids de découpe dupliqués ; ripple delete avec pistes verrouillées | couper deux fois la moitié gauche d'un clip échouait ; un clip verrouillé de 8 s passait à 4 s | `7a8aa98` |
| Pannes locales qui bloquent l'application | une tâche qui lève tuait le thread worker ; un job de tracking restait « running » pour toujours ; un FFmpeg d'aperçu survivait 120 s à la fermeture ; la file de rendu restait bloquée sur un job | `97e3072` |
| Cadence tronquée | un projet à 29,97 i/s était prévisualisé à 29 (l'empreinte, elle, utilisait la vraie valeur) | `9f68f3c` |
| Aperçu GPU : liaisons de ressources indexées par `id()` et non bornées | une texture neuve retombait sur la liaison d'une texture déjà détruite ; 303 liaisons après 300 images ; descripteur de passe détruit avec sa cible | `95fc5ed` |
| Décodage matériel : un décodeur sain banni pour un échec sans rapport | le matériel n'est blâmé que si le CPU réussit là où il a échoué | `03aaf80` |
| Adjustment layer dans une séquence imbriquée : masquait la piste parente | mesuré : coin `(0,0,0)` au lieu du bleu du parent | `fca3488` |
| Média relié : seul le chemin changeait | un fichier plus court, plus petit, d'une autre cadence ou sans audio gardait les valeurs de l'ancien (mesuré : 640×360, 30 i/s, 5 s, audio annoncés pour un fichier de 320×180, 25 i/s, 2 s, sans audio) | `a62320f` |
| Un worker de test plantait selon l'ordre des tests | une `QGuiApplication` nue créée par un test de rendu : plantage ou échec selon la répartition, donc selon le nombre de cœurs de la CI | `161612f` |
| Clip dont le média a disparu | seek, aperçu, annulation, plan d'export et **réouverture** levaient `KeyError` ; la réouverture laissait la fenêtre à moitié mise à jour. Tolérer à l'écran, refuser à l'export | `de75e3d` |
| Un fichier de cache impossible à supprimer était compté comme libéré | sous Windows (fichier tenu ouvert) le budget se croyait respecté alors que le disque ne l'était pas | `2300b47` |
| Course entre threads sur les caches de courbes et de tracking | 7 threads sur 8 mouraient en `KeyError` sous contention | `caea682` |
| Un glissement de fader créait une entrée d'historique par cran | 10 crans = 10 entrées et 10 copies du projet ; la pile de 100 chassait de vraies éditions | `78ceb8d` |

### Moyenne

| Défaut constaté | Commit |
| --- | --- |
| Keyframes incohérents après un changement de durée (aperçu : 0,000 ; export : 0,333 au même instant) | `c2f1345` |
| Dupliquer une séquence perdait parentage, groupes et liaisons de tracking (références vers l'original) | `6a21580` |
| Une sonde média en cache partageait un `MediaAsset` modifiable entre deux imports | `455abae` |
| Moniteur GPU : un segment déjà composé recevait le compositing du clip précédent | `58658fd` |
| Supprimer un clip (touche Suppr) laissait parent / groupe orphelins et le média technique du calque dans chaque `.kut` | `9b873a4` |
| Imbriquer une sélection qui sépare un calque de son parent, de son groupe ou de sa source de tracking cassait la hiérarchie sans le dire ; `clamp_nested_clips` ne propageait pas aux grands-parents | `252433e` |
| Mode de fusion dans une séquence imbriquée : un Produit sur du vide sortait noir (mesuré `(1,0,0)` au lieu de la couleur du calque) | `0556739` |
| Trois caches (`mograph`, `graphics`, `tracking`) dans `<tmp>/kut-studio-cache`, que l'OS vide, au lieu du dossier de cache natif | `066e348` |
| Moniteur GPU : une image non mappable le condamnait pour la session ; `releaseResources` le laissait noir en pause | `8a8dbd7` |
| Aucune protection contre un pilote qui fait tomber l'application à chaque démarrage | `81cd366` |
| Un segment d'aperçu vide était mis en cache sept jours | `aaa0a76` |
| Deux instances partageaient le même fichier `.partial` de proxy ; le nettoyage supprimait celui de l'autre | `9b97bee` |
| Deux instances qui terminent un même proxy en même temps : marqueur écrit via un temporaire au nom fixe, et l'instance en échec supprimait le proxy déjà promu par l'autre (trouvé par la CI Windows) | `e21bb36` |
| Windows refuse parfois `os.replace` quand deux processus promeuvent vers la même destination (`PermissionError` transitoire) : promotion réessayée (6 × 50 ms), proxy déjà produit toléré (trouvé par la CI Windows) | `51c4d06` |
| ≈ 45 refus d'opération signalés par un `print`, invisible dans l'application empaquetée | `e2559f8` |
| Aucune trace d'une exception dans l'application empaquetée (pas de console) | `5346171`, `907e06c` |
| Entrées de menu actives sans fonction (Couper / Copier / Coller, Ajouter un clip), boutons-icônes sans nom accessible | `0fc2778` |
| Infobulles de raccourcis fausses (« Exporter (⌘E) » : ce raccourci n'existe pas) ou figées | `1ddd1ef` |
| Boîtes « Enregistrer » : `~/Movies` créé sans garde (n'existe pas hors macOS), nom du projet injecté tel quel dans le chemin (`a/b`, `:`) | `84ddb43` |
| Couper la source d'un tracking : le suivi des clips liés s'arrête, sans un mot (**mitigé** : message ; voir points ouverts) | `1abef27` |

### Qualité du dépôt et de la CI

* **CI de `main` rouge depuis la PR #21** : trois causes établies par mesure, pas par réglage de seuils — des tolérances de
  parité calibrées sur un seul FFmpeg (l'erreur de conversion de swscale 6.1 / 7.1 est de 0,87 en moyenne, 3 au maximum,
  même sans effet), un faux FFmpeg qui coupait les chemins avec `/` (Windows), et une vraie course Qt sur une boîte de
  préférences déjà détruite. `d5931d7`.
* **Smoke test Windows creux** : l'exécutable `--windowed` n'était pas attendu par PowerShell (`&`) et son code de sortie
  était perdu ; il ne vérifiait rien. `Start-Process -Wait -PassThru` + `ExitCode`. `5f6fc11`.
* **Règles de lint** élargies aux défauts réels (`F841` variables inutilisées, `F541`, `B904`) ; une quarantaine de
  variables mortes retirées, dont des restes d'assertion rétablis. `5f6fc11`.
* **`mypy`** sur tout `core/`, avec un cliquet : 41 modules en dette connue listés dans `pyproject.toml` (la liste ne peut
  que diminuer, tout nouveau module est vérifié). `5f6fc11`.
* **`pytest-timeout`** : un test bloqué échoue au lieu de geler la CI.

## Performance

Mesures faites pendant la phase (la machine de mesure n'est pas celle de la CI : seules les proportions comptent) :

| Mesure | Résultat |
| --- | --- |
| Image des scopes à une tête de lecture à 10 minutes, 1080p | **66 s** avec un `trim` en sortie du graphe (coût O(T)) → quelques centaines de ms avec `project_at_playhead` (copie ramenée à l'origine), coût indépendant de la position |
| Erreur de conversion swscale `yuv420p → rgb24` | 0,87 en moyenne, 3 au maximum (FFmpeg 6.1 et 7.1) : budget de parité des tests GPU |
| Liaisons de ressources GPU (SRB) après 300 images à masque animé | 303 → bornées à 128 (LRU) |
| Entrées d'historique pour un glissement de fader de 10 crans | 10 → 1 |

### Benchmarks du dépôt : `main` contre la branche, sur la même machine

Les benchmarks du dépôt (`tools/perf/`) ont été rejoués **deux fois sur la même machine**, sans autre charge : `main`
(`docs/perf/main-before-stabilization.json`) puis la branche (`docs/perf/stabilization.json`). 223 mesures comparées
(projets synthétiques de 100, 1 000 et 10 000 clips : chargement, enregistrement, plan de rendu, empreinte, index,
timeline, fenêtre).

* **Moyenne géométrique des durées d'au moins 1 ms : 1,01 × `main`** (1,00 = identique). Aucune régression d'ensemble :
  chargement d'un projet de 10 000 clips 916 → 888 ms, enregistrement 1 106 → 1 106 ms, plan de rendu 216 → 182 ms.
* **Coût mesuré, voulu : l'empreinte des segments** (`fingerprint_ms`) est de 29 % à 69 % plus lente
  (10 000 clips : 132 → 171 ms ; 176 → 298 ms pour des clips courts sur une piste), parce qu'elle couvre maintenant
  le mixage, les styles de sous-titres et le fader maître. Elle est calculée sur la fenêtre d'un segment, pas sur tout
  le plan : le coût réel par segment est sans commune mesure.
* Les autres écarts de plus de 25 % portent sur des durées de 1 à 2 ms. Plan de rendu et chargement d'un projet de
  100 clips : du bruit à cette échelle (ils ne se retrouvent pas sur les gros projets). `layout_refresh_ms`
  (1,3 → 2,3 ms) se retrouve sur deux scénarios : cause probable, le nom accessible posé avec chaque infobulle
  (non investigué plus avant ; sans effet perceptible).
* **Ne comparez pas avec `docs/perf/after.json`** : il a été mesuré sur une autre machine, plus rapide ; sur celle-ci
  même les fonctions que cette phase n'a pas touchées (`active_at`, `index_build`) y paraissent deux fois plus lentes.

Pour rejouer : `QT_QPA_PLATFORM=offscreen python -m tools.perf.bench --out ma-mesure.json`, puis
`python -m tools.perf.bench --compare docs/perf/main-before-stabilization.json ma-mesure.json` (sur la machine qui a
produit les deux).

## Multi-plateforme et CI

<a id="ci"></a>

La CI (`.github/workflows/multiplatform.yml`) tourne sur macOS, Windows et Ubuntu : lint, `mypy` (Linux), suite complète
(`-n auto --timeout=600`), smoke test de l'interface, build PyInstaller natif, smoke test de l'application empaquetée.

Différences de plateforme rencontrées et traitées :

* **macOS / Windows** : FFmpeg récent ; seule la matrice sortait balisée « bt709 » (primaires et transfert « unknown »).
  Corrigé en posant les propriétés sur les images (`setparams`) ; un test retire les options de ligne de commande et exige
  le flux entier en BT.709.
* **macOS** : le FFmpeg Homebrew de la matrice n'a pas **libass** (filtre `subtitles` absent) ; l'application le détecte et
  refuse avec un message clair, et les tests de sous-titres s'y sautent avec la raison. **Un job dédié, `macOS / libass
  (sous-titres)`, installe `ffmpeg-full` (bouteille de homebrew-core)** et n'exécute que les tests marqués `libass` avec
  `KUT_STUDIO_REQUIRE_LIBASS=1` : le saut devient un échec, un job vert signifie « libass testé ». **Mesuré en CI**
  (run du commit `a7eca11`) : installation 19 s, `--enable-libass` vérifié, 12 tests réussis, 0 sauté. Les tests relisent les
  pixels d'un sous-titre rendu avec le graphe de l'application. Voir [ci-libass.md](ci-libass.md).
* **Windows** : fichiers tenus ouverts (lecteur, antivirus) non supprimables : comptabilité des caches corrigée ; chemins avec
  `\` dans les faux FFmpeg ; exécutable graphique sans `stderr` (`sys.stderr is None`) géré par le journal de diagnostic.
* **Linux** : bibliothèques graphiques (`libegl1`, `libgl1`) nécessaires aux tests Qt hors écran.
* **Aucun test GPU réel en CI** (plateforme `offscreen`) : l'exécuteur GPU est testé sur le backend QRhi *Null* (comptabilité
  des ressources, repli), le rendu réel sur machine avec GPU (`tools/perf/gpu_bench.py`).

## Interface et accessibilité

Corrigé : entrées sans fonction grisées avec explication, refus affichés dans la barre d'état, infobulles de raccourcis qui
suivent le raccourci réel, nom accessible des boutons-icônes et de la pastille de couleur (un test parcourt tous les
boutons de la fenêtre principale), invite d'enregistrement avant d'abandonner un travail.

Traité en phase 2 : tailles minimales et mises en page à 1440×900, 1280×720 et 1180×720, navigation clavier des dialogues,
de l'inspecteur et des menus, textes d'interface — voir [Phase 2](#phase-2) et
[ui-small-windows-and-keyboard.md](ui-small-windows-and-keyboard.md). Reste : voir les points ouverts.

<a id="phase-2"></a>

## Phase 2 : fermeture des points ouverts

Même règle que la première phase : aucune grosse fonctionnalité (ni multicam, ni optical flow, ni API de plugins, ni
transcription, ni IA, ni 3D), et **chaque défaut corrigé a un test qui échoue sans la correction** (vérifié en retirant la
correction puis en la remettant ; les rares exceptions sont dites dans la colonne « Preuve »). Les chantiers indépendants
ont été menés en parallèle dans des copies isolées du dépôt, puis fusionnés ; les tests des branches ont été relancés
ensemble après chaque fusion (3 incompatibilités entre branches trouvées et corrigées à ce moment-là, voir plus bas).

### État des points ouverts initiaux

| Point ouvert | État final | Ce qui a été fait | Preuve | Commits |
| --- | --- | --- | --- | --- |
| **Tracking après la coupe de sa source** (moyenne) | **corrigé** | les données de tracking sont en temps source et partagées sans copie par les deux moitiés ; la **liaison** d'un autre clip suit maintenant toutes les parties de sa source (`TrackLink.continuation_ids`), à chaque instant celle qui couvre l'instant. Coupe neutre pour ce que montrent les clips liés. Stabilisation : même zoom des deux moitiés (`shared_range`). Diagnostic unique `link_issues`, messages explicites. Politique : [tracking.md](tracking.md) | `test_tracking_split.py` (35 tests, 24 échouent sans le correctif, rendu réel aperçu + export), `test_cut_tracking_source.py` | `652dd3b`, `0a765f5`, `131f12c`, `ea7f724` |
| **FFmpeg orphelins après un arrêt brutal** (moyenne) | **corrigé** | `core/process_supervisor.py` est le **seul** point de lancement (garde AST) ; objet Job `KILL_ON_JOB_CLOSE` sous Windows ; gardien en Python pur sous macOS / Linux (l'application relancée avec `--kut-process-reaper`, valable gelée) ; registre par instance, identité vérifiée (heure de début + nom) avant tout arrêt, balayage au démarrage ; jamais un PID réutilisé, jamais une autre instance. Coût mesuré : ≈ 95 µs par lancement. [process-supervision.md](process-supervision.md) | `test_process_supervisor.py` (25), `test_process_launch_guard.py` (2) : `SIGKILL` réel d'un parent, plusieurs enfants, vrai FFmpeg, `QProcess` de l'export, deux instances, application gelée | `5b8e6cc`, `452c2bf`, `6d4b0ef`, `97a8f15`, `3ff7009`, `fbc49b4`, `c8aea8d`, `2f17da1`, `704c65e` |
| **Textes de l'interface** (≈ 200 chaînes) (moyenne) | **corrigé** pour `ui/` ; **encore ouvert** pour les erreurs de `core/` | 684 chaînes distinctes (765 occurrences) écrites en dur au départ → **0** ; 554 clés ajoutées (936 → 1 490) ; détecteur AST `tools/i18n_audit.py` + baseline cliquet **vide** (un texte neuf fait échouer) ; parité fr / en / es stricte, `translate_strict` sans repli ; changement de langue à chaud pour les panneaux principaux. [i18n.md](i18n.md) | `test_i18n_hardcoded.py` (74), `test_i18n_parity.py` (27), `test_i18n_migrated_zones.py` (46) | `53542e0`, `139ff33`, `2161f8b`, `cbf2dbb`, `1b45c7e`, `b97f25b`, `f014fc7`, `869bf1d`, `62bf1d7`, `756f002`, `8b32da7` |
| **Pas de couverture libass sur macOS en CI** (moyenne) | **corrigé, vérifié en CI** | job `macos-libass` (`ffmpeg-full`), garde partagée `tests/ffmpeg_caps.py`, marque `libass`, `KUT_STUDIO_REQUIRE_LIBASS=1` transforme le saut en échec. **Premier passage vert en CI** : 19 s d'installation, 12 tests, 0 sauté. [ci-libass.md](ci-libass.md) | `test_libass_subtitles.py`, `test_ffmpeg_caps.py` (21), `test_ci_workflow.py` (4) ; mutations vérifiées (filtre remplacé par `null`, `continue-on-error` ajouté…) | `abd4c7f`, `fae47b0`, `48ecb74`, `49c530e`, `8778cf7` |
| **Encodeurs matériels et balises de couleur** (moyenne) | **mitigé** | pour chaque encodeur **disponible** : vrai mini export, relecture `ffprobe` des quatre balises, couleurs décodées à ±6 niveaux ; sauté avec raison si absent, échec avec `KUT_STUDIO_REQUIRE_HARDWARE` (un filtre `--encoder` ne le cache pas). Outil `python -m tools.perf.hardware_validation`. **VideoToolbox vérifié** (Mac arm64) : `bt709/bt709/bt709/tv`, couleurs à ±2 niveaux. **NVENC, Quick Sync, AMF, VAAPI : tests prêts, non exécutés** faute de machine | `test_hardware_color_validation.py` (12, dont 8 sautés ici), `test_hardware_validation_report.py` (15) ; mutations de l'étape de couleur vérifiées (sans `setparams`, BT.601, plage pleine : échec) | `c99662c`, `b9bb147`, `52fc1f5`, `d8b174b`, `a7eca11` |
| Focus clavier (faible) | **corrigé là où il est nécessaire** | un seul bouton par défaut par dialogue (c'était « Retirer » ou « Choisir une couleur… »), boutons-icônes de dialogue focalisables et nommés, Tab atteint tout l'inspecteur dans l'ordre d'affichage **sans** qu'un clic vole les raccourcis (bug réel : Espace relançait le bouton cliqué), focus visible, mnémoniques de menu uniques par langue. Barre principale, timeline et bibliothèques gardent `NoFocus` : voulu | `test_ui_keyboard.py` (59), `test_theme_focus.py` (19) | `928baa1`, `9f9c9f9`, `25a5265`, `f627ba1` |
| Mises en page aux petites tailles (faible) | **corrigé**, un reste | la somme des minima de panneaux (864 px) dépassait les 720 px de la fenêtre ; contrôles coupés dans l'inspecteur dès 1440×900 ; onglets de catégorie de la bibliothèque écrasés à ≈ 30 px ; moniteur réduit à ≈ 100 px avec les scopes. Minima cohérents, rangées qui passent à la ligne, blocs qui défilent. Audit rejouable `python -m tools.ui_audit`. [ui-small-windows-and-keyboard.md](ui-small-windows-and-keyboard.md) | `test_ui_small_windows.py` (77, assertions structurelles, polices +20 % simulées), `test_adaptive_layout.py` (9) | `ded39ca`, `f8db14d`, `a788e53` |
| `Track.automation` à deux représentations (faible) | **corrigé** | forme canonique `TrackAutomation`, imposée par `Track.__setattr__` et fabriquée au chargement ; chemins à double forme supprimés ; format `.kut` inchangé. [track-automation.md](track-automation.md) | `test_track_automation.py` (34) | `bd4841a` |
| Code mort probable (faible) | **corrigé** | `core/timeline_model.py`, `core/effects.py`, `core/graphics_raster.py`, `mix_at` et aides, 7 fonctions privées, 3 adaptateurs de vue supprimés avec dossier de preuves ([dead-code-audit.md](dead-code-audit.md)) ; 205 symboles restent sans référence en production : **relevé, non instruit** | `test_dead_code_guard.py` (8 : aucun module inatteignable, aucune fonction privée sans usage, chemin moderne prouvé) | `f4f4a2b`, `40aa894`, `c7c5fd6`, `442fe99`, `0e1e4e6` |
| 41 modules de `core/` en dette `mypy` (faible) | **mitigé** | **41 → 17 modules** (23 nettoyés, dont tracking, rendu, GPU, animation, modèle de projet ; 1 supprimé avec son code mort) ; `Clip.graphic`, `.tracking`, `.compositing` ont leurs vrais types ; contrat typé du panneau Suivi. `ui/` toujours non vérifié | `test_typing_ratchet.py` (4 : liste identique à la baseline, rien n'entre, un module propre ne revient pas) | `8eb3b46`, `78c46a7`, `7acbfdb` |
| Fusion Produit / Incrustation dans une séquence imbriquée (faible) | **encore ouvert** | non traité (hors des priorités de la phase) | — | — |

### Défauts trouvés pendant la phase (hors de la liste initiale), tous corrigés

| Défaut | Mesure / conséquence | Commit |
| --- | --- | --- |
| **`force_style` écrasait le style des fichiers ASS** (aperçu fidèle **et** export) | un clip de sous-titres 48 pt jaune « haut centré » ressortait en minuscule, en bas (145 pixels touchés au lieu de plusieurs milliers). Trouvé par le rendu réel que le job `macos-libass` rend possible | `f497163` |
| Cinq pannes **absentes du fichier de journal** : analyse de tracking, état d'un clip impossible à dériver, entrée du cache de tracking illisible, segment d'aperçu impossible à supprimer, cache des capacités illisible | invisibles dans l'application empaquetée (pas de console), donc dans un rapport de bogue | `3c2d9f2` |
| Échecs FFmpeg **absents du journal** : export, tâche de la file de rendu, segment d'aperçu, proxy | un export en échec laissait un message à l'écran et aucune trace | `bfe7a41` |
| Backend exigé caché par un filtre : `KUT_STUDIO_REQUIRE_HARDWARE=nvenc … --encoder cpu` sortait en succès sans NVENC (relecture automatisée de la PR) | l'exigence « l'absence est une erreur » ne tenait pas | `a7eca11` |
| **Changer de langue renommait les calques** (introduit par la migration i18n, trouvé en relançant la suite) : réécrire l'info-bulle d'un calque faisait émettre `itemChanged`, lu comme un renommage au nom inchangé | une entrée d'historique « Renommer le calque » par calque et par changement de langue (2 → 5 entrées après trois changements), projet marqué modifié, arbre reconstruit pendant le parcours de l'itérateur : **plantage natif intermittent** d'un processus de test (environ une suite complète sur deux) | `5e2e577` |
| Classement « mémoire insuffisante » du GPU lu dans le **texte** du message ; qualité d'export « Custom » indexée par son **libellé affiché** | traduire aurait cassé ces comportements : indicateur explicite et identifiant interne | `2161f8b`, `62bf1d7` |

Trois **incompatibilités entre branches**, trouvées en relançant la suite complète après chaque fusion et corrigées : le garde « module atteignable depuis `main.py` » ne connaissait pas l'outil de validation matérielle ; `PropertiesPanel` a perdu un paramètre mort (code mort) alors que des tests d'autres branches l'appelaient encore ; des tests de parité i18n prenaient un titre de menu (devenu « &File » avec son mnémonique) comme exemple.

### Tests

Référence avant la phase 2 (`main`, exécution locale) : **2 970 réussis, 7 sautés**. État final (branche, exécution locale
complète, `-n 6`) : **3 445 réussis, 20 sautés, 0 échec** ; 3 465 tests collectés. Les 20 sautés ont tous leur raison
affichée : 12 tests `libass` (le FFmpeg local n'a pas libass ; exécutés ici avec un `ffmpeg-full` déjà installé : 12
réussis) et 8 backends matériels absents (NVENC, Quick Sync, AMF, VAAPI, MP4 et MOV). Vingt fichiers de tests ajoutés (ceux cités dans les tableaux ci-dessus, plus `test_dead_code_guard.py` (8),
`test_diagnostics_coverage.py` (8) et `test_ffmpeg_failure_diagnostics.py` (4)), 23 fichiers existants adaptés (dont
`test_audio_mixer.py` : 20 tests supprimés avec le code mort qu'ils exerçaient).

### Plateformes réellement testées

| Plateforme | Ce qui a tourné |
| --- | --- |
| **macOS arm64** (local, Python 3.14, Qt offscreen) | suite complète, `ruff`, `mypy`, smoke test source, **build PyInstaller natif + smoke test de l'application empaquetée** (code 0), `kill -9` réel d'un parent (supervision), validation matérielle VideoToolbox, rendu libass réel (`ffmpeg-full`), benchmarks |
| **CI : macOS, Windows, Ubuntu (Python 3.11)** | workflow `Multiplatform` **vert** sur les PR #23 (`a7eca11`) et #24 (`4aaa02c`) : lint, `mypy` (Linux), suite complète, smoke test source, build natif, smoke test empaqueté. La supervision des processus (objet Job, `/proc`) y a donc tourné pour de bon sous Windows et Linux |
| **CI : `macOS / libass (sous-titres)`** | vert sur les mêmes commits : 12 tests, 0 sauté, `--enable-libass` vérifié |
| **Non exécuté nulle part** | NVENC, Quick Sync, AMF, VAAPI (aucune machine) ; la CI de la branche « petites fenêtres et clavier » (#25) a été **rouge** : voir l'incident ci-dessous |

### Benchmarks (même machine, avant / après)

`python -m tools.perf.bench` rejoué sur la même machine, sans autre charge : référence prise **avant** toute modification
(`docs/perf/main-before-open-items.json`) puis état final (`docs/perf/open-items.json`).

* **Moyenne géométrique des durées d'au moins 1 ms : 0,985 × la référence** (102 mesures ; 1,00 = identique) : aucune
  régression d'ensemble.
* Tracking, scénario `bindings` (liaison d'un calque à un tracker de 10 min, 18 000 images), trois passes chacun :
  plan à froid 275–279 ms avant, 279–283 ms après (≈ +1 %) ; images-clés dérivées identiques (26 630) ; 5,24 octets par
  image dans le `.kut`, inchangé.
* Un écart reproduit sur deux passes : `select_all_marquee_ms` à 1 000 clips et 8 pistes, 1,9 → 2,8 ms (≈ +0,9 ms) ; absent
  à 10 000 clips (20,1 → 19,4 / 20,1 ms). Non investigué ; imperceptible. Les autres écarts de plus de 25 % portent sur des
  durées de 0,05 à 2 ms et ne se reproduisent pas (bruit).

Pour rejouer : `QT_QPA_PLATFORM=offscreen python -m tools.perf.bench --out ma-mesure.json`, puis
`python -m tools.perf.bench --compare docs/perf/main-before-open-items.json ma-mesure.json` (sur la machine qui a produit les deux).

### Dette restante

* **`mypy` : 17 modules de `core/` en dette** (liste dans `pyproject.toml`, identique à `tests/mypy_debt_baseline.txt`) :
  `audio_recorder`, `blend_modes`, `compositing`, `export_engine`, `graphics`, `hardware_cache`, `hardware_encoding`,
  `lut_importer`, `mograph_ffmpeg`, `mograph_layers`, `mograph_presets`, `mograph_raster`, `mograph_stream`,
  `render_queue`, `scopes`, `scopes_analyzer`, `visual_effects` (198 erreurs au dernier relevé, dont 81 dans
  `mograph_layers` et `mograph_raster`). `ui/` n'est pas vérifié.
* **i18n : 0 chaîne en dur dans `ui/`** (baseline vide) ; **502 messages d'erreur français dans `core/`** (`raise
  X("français…")`, ≈ 108 sites de `ui/` affichent `str(exc)` tel quel), non migrés (`python -m tools.i18n_audit --core`,
  mécanisme proposé dans [i18n.md](i18n.md)) ; valeurs créées par le cœur restées en français (« Projet sans titre »,
  « Séquence principale », « Sous-titre 01 ») ; éditeurs construits une fois, retraduits seulement à la réouverture
  (calque graphique, compositing, style de texte, transformation avancée, étalonnage, effets audio, gestionnaire de tags).

## Points ouverts

État **final** de ce qui n'est pas réglé, sans l'atténuer. Le détail des points fermés est dans la [Phase 2](#phase-2).

### Moyenne

| Point | État | Pourquoi pas fermé |
| --- | --- | --- |
| **Encodeurs matériels : NVENC, Quick Sync, AMF, VAAPI** | **mitigé** : tests et outil prêts, VideoToolbox vérifié ; les quatre autres jamais exécutés | exige les machines correspondantes (`KUT_STUDIO_REQUIRE_HARDWARE` rend l'absence bloquante sur une machine qui doit les avoir) |
| **Erreurs de `core/` affichées en français** | **encore ouvert** (502 messages) | volumineux ; mécanisme proposé dans [i18n.md](i18n.md), à décider avant de migrer |

### Faible

| Point | État |
| --- | --- |
| Fusion Produit / Incrustation dans une séquence imbriquée : formule W3C par pondération d'opacité ; les bords antialiasés ne sont validés que par un cas à cheval sur une arête | **encore ouvert**, mesuré sur ce cas |
| 17 modules de `core/` en dette `mypy` ; `ui/` non vérifié | **mitigé** (41 → 17), cliquet en place |
| Éditeur de courbes (Graph Editor) : minimum ≈ 730 px de large et pas de défilement ; onglet spécialisé de l'inspecteur tronqué dans « ••• » à 280 px ; info-bulles des tuiles d'alignement encore en anglais ; en-tête des transitions dense à 1180 px | **encore ouvert** (dans [ui-small-windows-and-keyboard.md](ui-small-windows-and-keyboard.md)) |
| Un `.kut` duplique les trackers à chaque coupe : en mémoire les octets sont partagés, **sur disque chaque moitié écrit sa copie** (couper N fois un plan très long multiplie la taille des trackers par N + 1) | **encore ouvert** : l'éviter demande un format de fichier (blocs partagés), donc une version du `.kut` |
| 205 symboles sans référence en production | **relevé, non instruit** (annexe de [dead-code-audit.md](dead-code-audit.md)) |

### Constats hors périmètre, relevés sans y toucher

* Le curseur « Audio > Volume » (0 à 200 %) ne règle que le volume du **moniteur** : ni sauvegardé, ni exporté ; Qt le borne à 1,0.
  *Depuis* : libellé « Volume du moniteur », infobulle qui le dit, bornes 0 à 100 %.
* `ui/main_window_mixins/audio.py` définit 7 gestionnaires d'automation, de rôle et de ducking qu'aucun signal ne déclenche :
  fonction à moitié branchée, pas du code à supprimer.
* **Mute contre solo** : le plan de rendu écarte d'abord une piste muette, solo ou non (la sourdine l'emporte) ; l'ancien
  `is_audible` supprimé disait l'inverse. Le test fige le comportement réel : à trancher si l'autre sémantique est voulue.
* Une source RVB (FFV1 `rgb`) sort de l'export CPU décalée de 3 à 4 niveaux (221,92,29 → 218,89,25) : dans le budget de
  conversion swscale déjà mesuré (3 niveaux au maximum pour `yuv420p → rgb24`), mais à regarder.
* Sous FFmpeg 9.0.2, `-color_primaries` / `-color_trc` seuls ne balisent ni primaires ni transfert (libx264 comme VideoToolbox) :
  `setparams` reste indispensable.
* Un segment d'aperçu tué laisse un `kut-preview-*.mp4` dans le dossier temporaire du système.
* Supervision des processus : fenêtre de course entre le lancement et l'enregistrement (≈ µs sous Windows, ≈ ms pour le
  `QProcess` sous POSIX) ; application **et** gardien tués ensemble : les enfants tournent jusqu'au prochain démarrage (balayage).

## Critères de fin

| Critère | État |
| --- | --- |
| `ruff` propre | oui |
| `mypy` propre | oui sur `core/` (17 modules en dette, listés, verrouillés par `test_typing_ratchet.py`) |
| `pytest` complet vert | oui : **3 445 réussis, 20 sautés, 0 échec** (exécution complète locale sur l'état final ; la CI l'exécute sur trois plateformes) |
| Smoke test de l'interface | oui (`python main.py --smoke-test`), source **et** application empaquetée |
| Build natif + smoke test empaqueté | oui en local (macOS) ; CI trois plateformes verte sur les PR #23 et #24 |
| Anciens `.kut` compatibles | oui : `SUPPORTED_VERSIONS` inchangé ; champs ajoutés (`continuation_ids`, `shared_range`) **optionnels**, écrits seulement s'ils diffèrent du défaut ; `Track.automation` migré au chargement sans changer le fichier ; aller-retour `save → load → save` idempotent |
| Aperçu / export cohérents | oui : mêmes graphe et étape de couleur ; tests de parité avec le vrai FFmpeg ; `force_style` corrigé pour les deux |
| Replis CPU / GPU | oui, et leurs pannes arrivent maintenant dans le fichier de journal |
| Aucun thread / process orphelin à la fermeture | oui, **y compris après un arrêt brutal** (objet Job, gardien, balayage), sauf la fenêtre de course documentée |
| Tests libass sur macOS | **oui, exécutés en CI** : job `macos-libass`, 12 réussis, 0 sauté |
| Validation matérielle conditionnelle | oui : VideoToolbox exécuté ; NVENC, Quick Sync, AMF, VAAPI sautés avec raison (jamais comptés comme réussis) |
| Benchmarks documentés | `docs/perf/` + procédure ci-dessus ; moyenne géométrique 0,985 × la référence |
| Aucun nouveau point critique ou haut introduit | oui, aucun relevé |

### Incident : CI rouge sur la PR #25 (petites fenêtres et clavier), puis corrigée

La PR #25 a été fusionnée avec une CI **rouge** : Ubuntu 2 échecs, Windows 61 échecs, macOS annulé au bout de 30 minutes
(délai du job) après la mort d'un worker (« node down »), alors que la suite était verte en local et que les deux PR
précédentes (#23, #24) avaient une CI verte. Rien n'a signalé l'échec avant la fusion (l'auto-correction n'était pas
active sur cette PR). Causes établies par mesure, pas par réglage de seuils :

| Cause | Mesure | Correctif |
| --- | --- | --- |
| Deux tests de police (`scale_fonts`) **modifiaient la feuille de style de l'application et les fenêtres partagées** (`window_at`, fixture de module) sans pouvoir les restaurer (le facteur 1,0 se réapplique sur des feuilles déjà agrandies) | exécutés avant les tests structurels dans le même processus, ceux-ci échouent (« onglet clip coupé à (1180, 720) ») : c'est la série de 60 échecs Windows, selon l'ordre qu'xdist donne au worker | ces scénarios tournent dans un **sous-processus** ; un garde fait échouer le test fautif, pas ses voisins ; les messages d'échec nomment chaque constat |
| Des fenêtres fermées par `qtbot` **restaient vivantes** (leur `deleteLater()` n'est jamais livré sans boucle d'événements), donc leurs panneaux restaient **abonnés à la langue** | 460 abonnés fantômes après une centaine de tests ; chaque `set_language` retraduisait toutes ces fenêtres (la retraduction est lourde depuis l'i18n) : un test de 2 s en prenait 70, la suite doublait sur Ubuntu (≈ 6 → 13 min) et dépassait 30 min sur macOS | fixture automatique de `conftest` qui livre les suppressions différées après chaque test (460 → 0 abonné, suite locale 328 → 114 s) ; un test garde le mécanisme |
| Le test du mnémonique Alt simulait la touche dans une fenêtre hors écran | échec Ubuntu et Windows (la plateforme `offscreen` ne livre pas Alt + lettre de façon fiable) | le test vérifie la **résolution** du titre en raccourci (`Alt + F`), identique sur toutes les plateformes (plus de saut sous macOS) |

Aucun de ces défauts n'est dans le code de l'application : ce sont des défauts de **la suite de tests**. L'incident montre
qu'un test « structurel » qui touche un état global (feuille de style, abonnés de langue) ne se valide que par la suite
complète, jamais fichier par fichier ; la CI affiche maintenant les 15 tests les plus lents (`--durations=15`).

### État de la CI de la PR

Le run du commit `2300b47` est **vert sur macOS, Windows et Ubuntu** : lint, `mypy` (Linux), suite complète, smoke test de
l'interface, build natif et smoke test de l'application empaquetée (y compris l'étape Windows qui attend réellement le
processus). Le run précédent (`252433e`) avait 4 échecs sous macOS et Windows (balises de couleur) et 5 sous macOS
(filtre `subtitles` absent) : ils sont à l'origine des deux correctifs décrits plus haut.

Les tests ajoutés ensuite sur les proxies partagés (`tests/test_proxy_shared_folder.py`) ont, eux, échoué **de façon
intermittente sous Windows** (jamais en local sous Linux) : les deux échecs observés sur `e8e5b52` étaient ce test. La
cause était réelle (deux lignes du tableau ci-dessus), pas un test instable : les deux correctifs sont `e21bb36` et
`51c4d06`. Un troisième échec du même fichier de tests (run de `cf3c7e0`, commit de documentation seul) n'était, lui, pas
un défaut du produit mais une assertion trop stricte : le test comptait les appels à `os.replace`, alors qu'une instance
qui réessaie après le refus transitoire de Windows rappelle `os.replace` avec son propre temporaire (trois appels, deux
noms distincts). L'assertion porte maintenant sur les noms distincts, et elle échoue toujours si le nom redevient fixe.
Le run de `51c4d06` est **vert sur macOS, Windows et Ubuntu** (suite complète, smoke test source, build natif,
smoke test empaqueté). Une exécution verte unique ne prouve pas la disparition d'une course ; les tests de nouvelle
tentative (`_refuse_replace`) la reproduisent de façon déterministe, et un échec intermittent de ces tests sous Windows
doit être traité comme une régression, jamais relancé jusqu'au vert.


## Chantier Multicam : un défaut d'export préexistant, trouvé et corrigé

Le chantier Multicam ([multicam.md](multicam.md)) n'est pas une phase de stabilisation : c'est une fonctionnalité, bâtie sur
les principes ci-dessus (une source de vérité, un seul moteur de rendu, interface → cœur). Il a toutefois mis au jour un
défaut qui n'avait rien de spécifique à Multicam :

* **Défaut** : à l'export, le graphe audio retardait un clip par `asetpts=PTS+début/TB`, or `amix` ignore les horodatages
  de ses entrées. Avec le FFmpeg local (**9.0.2**), deux clips bout à bout sur une même piste donnaient : le premier à sa
  place, puis le second **joué depuis l'instant 0** (mélangé au premier) et **silence** à l'endroit où il devait être
  entendu. Tout export de plus d'un clip audio perdait donc son son après le premier. Le défaut n'a rien de propre à
  Multicam ; il est apparu parce qu'un enregistreur placé après l'origine l'a rendu audible. **Non établi** : le
  comportement des anciennes versions de FFmpeg (aucune n'a été essayée ici) ; les tests de filtres existants ne
  vérifiaient que le texte du graphe, jamais le son produit.
* **Correctif** : `core/export_engine.py` (`_build_audio_filter`) place désormais le clip par `adelay` (millisecondes à
  trois décimales) quand `timeline_start > 0`. `RENDER_ENGINE_VERSION` passe à **3** : les rendus mis en cache avec
  l'ancien filtre ne sont plus réutilisés.
* **Test** : `tests/test_export_audio_timing.py` rend de vrais fichiers avec FFmpeg et relit le **ton** entendu à chaque instant (clips bout à bout, trou entre deux clips, séquence imbriquée placée tard) ;
  les rendus de `tests/test_multicam_export.py` mesurent en plus le son des trois politiques audio.
* **Non vérifié** : la forme à millisecondes fractionnaires de `adelay` n'a pas été essayée sur FFmpeg 6.1 (Ubuntu en CI) ;
  elle suit la documentation. Un échec de ces tests sur cette plateforme est à traiter comme une régression du filtre.

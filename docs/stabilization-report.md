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
* **Critique : 6 trouvés, 6 corrigés.** Haute : 15 trouvés, 15 corrigés. Moyenne : 18 trouvés, 17 corrigés, 1 mitigé
  (le tracking après la coupe de sa source). Points ouverts : 5 de gravité moyenne, 6 de gravité faible.
* **Ce qui n'est pas réglé est listé plus bas**, sans l'atténuer (section [Points ouverts](#points-ouverts)).
* État de fin : `ruff` propre, `mypy` propre sur `core/` (avec une dette listée et décroissante), suite complète verte,
  smoke test de l'interface et de l'application empaquetée, CI sur trois plateformes — voir [CI](#ci) pour le détail.

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
* **macOS** : le FFmpeg du runner n'a pas **libass** : le filtre `subtitles` n'existe pas. L'application détecte ce cas et
  refuse avec un message clair ; les tests qui lisent un sous-titre avec ce filtre se sautent avec la raison. **Conséquence
  à connaître : la lecture des sous-titres par libass n'est pas couverte sur macOS par la CI.**
* **Windows** : fichiers tenus ouverts (lecteur, antivirus) non supprimables : comptabilité des caches corrigée ; chemins avec
  `\` dans les faux FFmpeg ; exécutable graphique sans `stderr` (`sys.stderr is None`) géré par le journal de diagnostic.
* **Linux** : bibliothèques graphiques (`libegl1`, `libgl1`) nécessaires aux tests Qt hors écran.
* **Aucun test GPU réel en CI** (plateforme `offscreen`) : l'exécuteur GPU est testé sur le backend QRhi *Null* (comptabilité
  des ressources, repli), le rendu réel sur machine avec GPU (`tools/perf/gpu_bench.py`).

## Interface et accessibilité

Corrigé : entrées sans fonction grisées avec explication, refus affichés dans la barre d'état, infobulles de raccourcis qui
suivent le raccourci réel, nom accessible des boutons-icônes et de la pastille de couleur (un test parcourt tous les
boutons de la fenêtre principale), invite d'enregistrement avant d'abandonner un travail.

Non traité : voir les points ouverts (focus clavier, tailles minimales, textes non traduits).

## Points ouverts

Ce qui suit n'est **pas** corrigé. Classé par gravité ; chaque point dit s'il a été mesuré ou seulement relevé à la lecture
du code.

### Moyenne

| Point | État | Pourquoi pas corrigé |
| --- | --- | --- |
| **Tracking après la coupe de sa source** : les clips qui la suivent restent liés à la moitié gauche, leur mouvement reste figé après la coupe | constaté à la lecture du code (`LinkMotion.at` borne le temps à la durée du clip source) ; **mitigé par un message** dans la barre d'état | rattacher chaque moitié (liaison par plage de temps) est une nouvelle fonctionnalité |
| **FFmpeg orphelins après un arrêt brutal** (`kill -9`, plantage) : l'aperçu fidèle, les proxies et l'export lancent FFmpeg en processus enfant ; sans passer par `closeEvent` ils continuent (jusqu'à 120 s pour un segment) | relevé à la lecture du code, non reproduit | exige un mécanisme par plateforme (`PR_SET_PDEATHSIG`, objet Job Windows, `kqueue`) à tester sur chaque système |
| **Textes de l'interface** : une grande partie est écrite en dur en français (≈ 200 chaînes) malgré l'i18n fr / en / es | relevé par recherche dans le code | volumineux et sans risque de régression à traiter par morceaux ; un test de parité des clés existe pour ce qui est traduit |
| **Pas de couverture libass sur macOS en CI** (voir ci-dessus) | mesuré | demande un FFmpeg avec libass sur le runner (formule Homebrew dédiée) |
| **Encodeurs matériels et balises de couleur** : `-colorspace` / `setparams` ne sont pas testés avec VideoToolbox, NVENC, Quick Sync, AMF, VAAPI | non testable en CI | exige les machines correspondantes |

### Faible

| Point | État |
| --- | --- |
| Focus clavier : les boutons-icônes n'acceptent pas le focus (`NoFocus`, voulu pour ne pas voler les raccourcis) ; navigation à la souris seulement | relevé |
| Mises en page aux petites tailles de fenêtre (panneaux rognés) | relevé |
| `Track.automation` a deux représentations (liste de points ou `TrackAutomation`) ; la normalisation est faite à l'usage plutôt qu'au chargement | relevé |
| Code mort probable (`core/timeline_model.py`, `core/effects.py`, certaines fonctions `mix_*` d'`audio_mixer`) | relevé ; non supprimé faute de test qui le garantisse |
| 41 modules de `core/` en dette `mypy` ; `ui/` non vérifié | mesuré |
| Fusion Produit / Incrustation dans une séquence imbriquée : formule W3C implémentée par pondération d'opacité ; les bords antialiasés ne sont validés que par un cas à cheval sur une arête | mesuré sur ce cas |

## Critères de fin

| Critère | État |
| --- | --- |
| `ruff` propre | oui |
| `mypy` propre | oui sur `core/` (dette listée) |
| `pytest` complet vert | oui : 2 912 réussis, 4 sautés (dernière exécution complète locale ; la CI l'exécute sur trois plateformes) |
| Smoke test de l'interface | oui (`python main.py --smoke-test`) |
| Build natif + smoke test empaqueté | par la CI sur trois plateformes, voir ci-dessous |
| Anciens `.kut` compatibles | oui : `SUPPORTED_VERSIONS` inchangé, aller-retour `save → load → save` idempotent sur un projet synthétique complet, mutation champ par champ |
| Aperçu / export cohérents | oui : mêmes graphe et étape de couleur, tests de parité avec le vrai FFmpeg |
| Replis CPU / GPU | oui : image non mappable, perte du périphérique, plantage du pilote, GPU indisponible |
| Aucun thread / process orphelin à la fermeture | oui pour la fermeture normale (`_shutdown_steps`, étapes isolées) ; **non garanti après un arrêt brutal** (point ouvert) |
| Benchmarks documentés | `docs/perf/` + procédure ci-dessus |

### État de la CI de la PR

Le run du commit `2300b47` est **vert sur macOS, Windows et Ubuntu** : lint, `mypy` (Linux), suite complète, smoke test de
l'interface, build natif et smoke test de l'application empaquetée (y compris l'étape Windows qui attend réellement le
processus). Le run précédent (`252433e`) avait 4 échecs sous macOS et Windows (balises de couleur) et 5 sous macOS
(filtre `subtitles` absent) : ils sont à l'origine des deux correctifs décrits plus haut.


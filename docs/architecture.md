# Architecture et sources de vérité

Ce document répond à une question : **quand deux endroits du code savent la même chose, lequel fait foi ?**
Il a été écrit pendant la phase de stabilisation (voir [stabilization-report.md](stabilization-report.md)),
qui a retrouvé la plupart de ses défauts exactement là où deux sources se contredisaient : un aperçu qui ne
rendait pas comme l'export, un cache dont l'empreinte oubliait un paramètre, une durée recopiée à deux endroits.

La règle de fond : **une information a une seule source ; tous les autres endroits la lisent, ne la recopient pas.**
Chaque ligne du tableau ci-dessous dit où est cette source et quel test la garde.

## Couches

```
 ui/                       PySide6 : fenêtre, panneaux, thème, i18n. Ne contient aucune règle de montage.
   main_window.py          assemble des mixins (ui/main_window_mixins/*) : un mixin = un domaine de la fenêtre
 core/                     logique pure : modèle, édition, plan de rendu, graphe FFmpeg, caches, tracking…
   (aucun import de ui/)   testable sans fenêtre ; Qt n'y entre que pour QProcess, signaux et le rasteriseur
 main.py                   point d'entrée : journal de diagnostic, QApplication, --smoke-test
```

Le sens est unique : `ui` importe `core`, jamais l'inverse. **Textes** : tout texte affiché passe par
`ui.i18n.translate(clé)` (module principal et modules de domaine `ui/i18n_*.py`, fusionnés, doublons refusés) ; aucun
texte d'interface en dur dans `ui/` : la baseline vide et le test AST (`tools/i18n_audit.py`) l'imposent, exception
documentée `# i18n-ignore: raison` ([i18n.md](i18n.md)). Une table de libellés porte des clés et se traduit à
l'affichage ; un libellé ne sert jamais d'identifiant. Une règle d'édition (« ce clip peut-il être
coupé ? ») vit dans `core` ; le mixin de la fenêtre l'appelle, attrape son refus et l'affiche
(`_report_edit_refused` : barre d'état + journal).

## Le chemin d'une image

```
 Project (.kut)  ──▶  RenderPlan  ──▶  graphe FFmpeg ──▶ export (QProcess)
 core/project_model   core/render_plan   core/export_engine     core/export_engine, core/render_queue
                           │                  │
                           │                  ├──▶ aperçu fidèle : segments mis en cache (core/preview_*)
                           │                  └──▶ image des scopes (core/playhead_snapshot + build_frame_command)
                           └──▶ moniteur temps réel (QMediaPlayer + QGraphicsVideoItem, ou GPU : ui/gpu_preview)
```

* `build_render_plan(project, window=…)` est la **seule** lecture du projet pour le rendu. Export, aperçu fidèle
  et scopes consomment ce plan ; aucun ne relit les pistes du projet pour son compte.
* Le graphe vidéo est construit une fois (`ExportEngine._build_filter_complex`) et réutilisé tel quel par
  l'aperçu fidèle (`build_preview_command`), y compris sa dernière étape de couleur
  (`with_output_color_stage` : conversion BT.709, plage limitée, propriétés posées sur les images).
* Le **moniteur temps réel** est un approximatif assumé (il doit tenir la cadence) : l'image qui fait foi est
  celle de l'aperçu fidèle, puis celle de l'export.

## Sources de vérité

| Information | Source unique | Les autres la lisent | Garde-fou |
| --- | --- | --- | --- |
| Contenu du montage | `Project` / `Sequence` / `Track` / `Clip` (`core/project_model.py`) ; `project.tracks` = pistes de la séquence **active**, `project.all_tracks()` = toutes | panneaux, plan de rendu, historique | `test_kut_integrity.py` |
| Ce qui est rendu | `RenderPlan` (`core/render_plan.py`), immuable, avec `warnings` et `missing_media` | export, aperçu fidèle, scopes, empreintes | `test_render_plan.py`, `test_preview_parity.py` |
| Temps source ↔ temps timeline | `timeline_to_source_time` et `TimeRemapping` (`core/time_remapping.py`) | trims, export, aperçu, tracking | `test_time_remapping.py`, `test_trim_with_remapping.py`, `test_export_remapping_real.py` |
| Temps d'un keyframe | **temps local du clip** (0 = début du clip sur la timeline) ; invariant : aucun keyframe après la fin du clip | animation, graph editor, tracking | `test_keyframes_after_duration_change.py` |
| Durée d'un clip | `source_out − source_in`, divisée par la vitesse (`Clip.duration`), jamais stockée à part | timeline, plan de rendu | `test_timeline_operations.py` |
| Couleur de sortie | BT.709 partout, y compris en SD : étape `OUTPUT_COLOR_STAGE` + balises `OUTPUT_COLOR_TAGS` | export, aperçu fidèle | `test_export_color.py`, `test_gpu_pipeline.py`, `test_hardware_color_validation.py` (chaque encodeur **disponible**, relu avec ffprobe) |
| Image de la tête de lecture (scopes) | `project_at_playhead` (`core/playhead_snapshot.py`) puis le même graphe | panneau de scopes | `test_scopes_real.py`, `test_playhead_frame.py` |
| Empreinte d'un segment d'aperçu | `fingerprint_plan` (`core/filter_graph.py`) + `RENDER_ENGINE_VERSION` | cache disque des segments | `test_segment_fingerprint.py` |
| Raccourcis | `core/shortcuts.py` (table des commandes) ; le gestionnaire (`ui/shortcut_manager.py`) l'applique ; les infobulles lisent `hint()` | menus, boutons, éditeur de raccourcis | `test_shortcuts*.py`, `test_ui_honesty.py` |
| Dossier de cache | `user_cache_dir()` (`core/platform_paths.py`, surcharge : `KUT_STUDIO_CACHE_DIR`) | aperçu, proxies, tracking, images de calques, capacités | `test_cache_roots.py` |
| Capacités matérielles | `CapabilityService` (`core/hardware_cache.py`), une détection pour encodeurs **et** décodeurs ; `DecodeHealth` pour les bannissements | préférences, export, décodage, validation matérielle (`core/hardware_validation.py`) | `test_hardware_*.py`, `test_decode_policy.py` |
| Tracking d'un clip | `Clip.tracking` (`ClipTracking`) ; les résultats d'analyse en cache sont **recalculables**. Données en temps source, **partagées** par les moitiés d'un clip coupé ; la liaison d'un autre clip suit toutes les parties de sa source (`TrackLink.continuation_ids`, diagnostic `link_issues`) | liaisons, stabilisation, rendu | `test_tracking*.py`, `test_tracking_split.py` |
| Automation d'une piste | `Track.automation` est **toujours** une `TrackAutomation` : `Track.__setattr__` normalise l'ancienne forme (liste de points), `project_io` produit la forme canonique au chargement ; le `.kut` garde une liste de points | mixage (`build_render_plan`, `export_engine`), séquences | `test_track_automation.py`, `test_kut_integrity.py` |
| Processus enfants (FFmpeg, ffprobe) | `core/process_supervisor.py` : **seul** point de lancement ; registre par instance, identité vérifiée avant tout arrêt | aperçu, proxies, export, file de rendu, tracking, détection matérielle, scopes, miniatures | `test_process_supervisor.py`, `test_process_launch_guard.py` (AST) |
| Textes de l'interface | `ui/i18n*.py` (fr / en / es) ; `translate_strict` pour les tests | tous les widgets de `ui/` | `test_i18n_hardcoded.py` (baseline vide), `test_i18n_parity.py`, `test_i18n_migrated_zones.py` |
| Dette de typage | liste `ignore_errors` de `pyproject.toml`, **identique** à `tests/mypy_debt_baseline.txt` | CI (`mypy`) | `test_typing_ratchet.py` (aucun module n'y entre, un module propre n'y revient pas) |
| Préférences utilisateur | `core/user_settings.py`, **hors** du `.kut` | fenêtre, préférences | `test_user_settings.py` |
| Annuler / rétablir | `ProjectHistory` (`core/edit_history.py`) : instantanés du projet, `_saved_index` pour l'état « enregistré » | barre du haut, titre, fermeture | `test_edit_history.py`, `test_unsaved_changes_prompt.py` |
| Journal d'erreurs | `core/diagnostics_log.py` (`user_log_dir()`) | `main.py`, `sys.excepthook`, threads | `test_diagnostics_log.py` |

## Politique d'erreurs

La même question se pose partout : *que fait-on d'une donnée incohérente ?* Réponse, par contexte :

| Contexte | Politique | Pourquoi |
| --- | --- | --- |
| **À l'écran** (timeline, aperçu, moniteur) | **tolérer** : un clip dont le média a disparu est ignoré, le plan le signale (`missing_media`) | l'utilisateur doit pouvoir ouvrir son projet et réparer |
| **À l'export** | **refuser** avec un message qui nomme le média (« Média introuvable : … ») | un export silencieusement incomplet est pire qu'un refus |
| **Édition refusée** (piste verrouillée, clip trop court…) | `ValueError` / `KeyError` dans `core`, la fenêtre l'affiche dans la barre d'état **et** le journalise | jamais un `print` (invisible dans l'application empaquetée) |
| **Lecture d'un `.kut`** | **strict** : une valeur non finie (`NaN`, `Infinity`), une structure abîmée ou une récursion excessive donnent un `ValueError` clair ; aucun fichier n'est modifié | un projet corrompu ne doit pas s'ouvrir « à moitié » |
| **Écriture d'un `.kut`** | écriture atomique ; une valeur non finie est refusée explicitement | pas de fichier que l'on ne saurait plus relire |
| **Cache** (disque, mémoire) | **jeter et recalculer** : une entrée illisible vaut une entrée absente | un cache n'est jamais une source de vérité |
| **Capacité matérielle** (GPU, décodeur, encodeur) | **repli CPU** ; on n'incrimine le matériel que si le CPU réussit là où il a échoué (`decode_policy`) | un échec sans rapport ne doit pas bannir un décodeur sain |
| **Panne que l'utilisateur ne voit pas** (repli GPU / matériel, échec FFmpeg, analyse de tracking, cache) | une ligne dans le **fichier** de journal (`kut_studio.*`), jamais un `print` | l'application empaquetée n'a pas de console ; `test_diagnostics_coverage.py` et `test_ffmpeg_failure_diagnostics.py` relisent le fichier |
| **Lien de tracking dont la source est coupée, supprimée ou raccourcie** | la liaison suit les parties restantes ; ce qui n'est plus couvert est **nommé** (`link_issues`), jamais figé en silence | un mouvement qui s'arrête sans un mot est le pire défaut d'un montage |

## Caches et empreintes

* Tout est sous `user_cache_dir()` : `preview/`, `proxies/`, `mograph/`, `graphics/`, `tracking/`, capacités matérielles.
  `CacheManager` (`core/cache_manager.py`) en tient le budget (LRU, purge, statistiques).
* **Empreinte d'un segment d'aperçu** : tout ce qui change l'image doit y figurer (calques, effets, fader maître,
  styles de sous-titres, fps, version du moteur…). Si vous changez ce que le graphe produit, **incrémentez
  `RENDER_ENGINE_VERSION`** : les anciens segments sont alors ignorés au lieu d'être resservis.
* Un cache n'est jamais lu sans validation (taille, version, structure) : `TrackingCache`, `CapabilityService`
  et le cache de segments traitent une entrée mal formée comme absente.

## Threads et processus

* Les travaux longs ne tournent jamais dans le thread de l'interface : `StudioRuntime` (`core/studio_runtime.py`)
  possède deux `QueueWorker` sur des `TaskQueue` (`core/task_queue.py`), l'un pour l'aperçu, l'autre pour les
  analyses ; `ProxyManager` a ses propres threads ; les exports passent par un `QProcess`
  (`ExportEngine`, `RenderQueue`).
* Un thread de travail ne touche pas aux widgets : il émet un signal (file d'attente automatique de Qt) vers un
  objet du thread principal (exemple : `_ScopeEvents` dans `ui/main_window_mixins/scopes.py`).
* **Fermeture** : `MainWindow.closeEvent` demande d'abord d'enregistrer, puis exécute les étapes de
  `_shutdown_steps()` **chacune isolée** (une étape qui échoue est journalisée, les suivantes s'exécutent).
  Contrat : aucun thread de travail et aucun FFmpeg ne survivent à la fenêtre.
* Une tâche qui lève une exception ne bloque pas la file : `TaskQueue.pump` l'isole.
* **Processus enfants** : jamais `subprocess` ni `QProcess` directement. On utilise `supervised_run` /
  `supervised_popen` (`core/process_supervisor.py`), ou `register_pid` dès `started` pour un `QProcess`. L'enfant
  meurt avec l'application, même tuée brutalement (objet Job sous Windows, gardien sous macOS et Linux, balayage au
  démarrage suivant) ; un test AST l'impose. Dernière étape de `_shutdown_steps` : « processus enfants ». Détails :
  [process-supervision.md](process-supervision.md).

## Aperçu : trois niveaux, un graphe

| Niveau | Rôle | Exactitude |
| --- | --- | --- |
| Moniteur temps réel (CPU `QGraphicsVideoItem`, ou GPU `QRhiWidget`) | scrub, lecture | approximatif, tient la cadence |
| Aperçu fidèle (segments FFmpeg en cache) | ce que l'on regarde pour juger | **même graphe que l'export**, résolution réduite |
| Export | le résultat | décodage **toujours CPU** (déterministe) |

Le GPU ne change que le moniteur temps réel ; il a un repli CPU complet. Détails dans
[gpu-preview.md](gpu-preview.md). Une image GPU illisible est convertie par Qt (copie CPU) au lieu de condamner
le moniteur ; si Qt libère les ressources du widget (masqué, détaché), la dernière image de chaque source est
renvoyée à la recréation.

## Format `.kut`

* Version courante : `CURRENT_VERSION` (`core/project_io.py`) ; `SUPPORTED_VERSIONS` liste les versions lisibles.
  Les anciens fichiers se chargent toujours (migration à la lecture, jamais à l'insu de l'utilisateur).
* Ne contient **que** des données du montage : jamais un chemin de cache, un résultat d'analyse recalculable
  ni une préférence de l'interface.
* Ajouter un champ : lui donner une valeur par défaut à la lecture (un vieux fichier n'a pas le champ),
  l'écrire seulement s'il diffère du défaut si possible, et ajouter un aller-retour dans `tests/rich_project.py`
  (le projet « riche » sert à `test_kut_integrity.py`, qui mute le JSON champ par champ).

## Ajouter une fonctionnalité sans casser le reste

1. **Une source** : où vit la donnée ? Si la réponse est « à deux endroits », corriger cela d'abord.
2. **Rendu** : passe-t-elle par le `RenderPlan` ? Sinon l'aperçu et l'export divergeront. Ajouter un test de
   parité qui rend une image avec le vrai FFmpeg et compare les pixels.
3. **Empreinte** : change-t-elle l'image d'un segment ? Alors elle entre dans `fingerprint_plan`.
4. **Temps** : en secondes de timeline (clip-local pour les keyframes) ; convertir seulement par `time_remapping`.
5. **Édition** : une opération `core` qui lève sur un cas refusé, une entrée d'historique, un test d'annulation.
6. **Refus** : le dire à l'écran (`_report_edit_refused`) ; jamais `print` (un test l'interdit dans `ui/` et `core/`).
   Un texte affiché passe par `translate(clé)` avec fr / en / es (un test de baseline refuse un texte en dur).
7. **Fermeture** : un thread ou un processus ? Il s'arrête dans `_shutdown_steps()` ; un processus est lancé par
   `core/process_supervisor.py`, jamais directement.
8. **Plateformes** : pas de chemin en dur, pas de `os.name` ; passer par `core/platform_paths.py`. La CI tourne
   sous macOS, Windows et Linux (voir [stabilization-report.md](stabilization-report.md#ci)).

## Vérifications de développement

```bash
python -m ruff check .                 # F841, F541, B904 et erreurs de syntaxe/noms (voir pyproject.toml)
python -m mypy                         # core/ ; la dette connue est listée dans pyproject.toml
QT_QPA_PLATFORM=offscreen python -m pytest -q -n auto --timeout=600
QT_QPA_PLATFORM=offscreen python main.py --smoke-test
python -m tools.i18n_audit --summary   # textes en dur dans ui/ (la baseline est vide)
python -m tools.perf.hardware_validation   # vos encodeurs matériels : mini export relu avec ffprobe
```

`mypy` est un **cliquet** : les modules en dette sont listés en `ignore_errors` dans `pyproject.toml`
(`tests/mypy_debt_baseline.txt` en est le double, `test_typing_ratchet.py` les compare).
On retire un module de la liste dès qu'il est propre ; on n'en ajoute jamais ; un module nouveau est vérifié.

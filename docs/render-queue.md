# Render Queue

Kut-Studio exporte via une **file de rendu persistante** construite autour
du moteur d'export existant (`core/export_engine.py`), qui n'a pas été
réécrit. Parcours simple : *Projet → Export → preset → « Lancer l'export »*
(une étape). On peut aussi « Ajouter à la file » plusieurs exports, les
réordonner puis tout lancer.

```
ExportPanel ──▶ RenderQueue ──▶ ExportEngine (QProcess) ──▶ FFmpeg
 (preset)        │  ▲                 ▲
                 │  └ RenderJob ──────┘  job.to_request(plan)
                 ▼
         RenderQueueStore  (<config>/render_queue/)
```

| Module | Rôle |
| --- | --- |
| `core/render_presets.py` | Presets (`RenderPresetSpec`) : **descriptions**, sans logique FFmpeg. |
| `core/render_job.py` | `RenderJob`, `JobStatus`, `RenderResult` : état sérialisable d'un export. |
| `core/render_queue_store.py` | JSON atomique + instantanés de projet. |
| `core/render_queue.py` | `RenderQueue` : exécution séquentielle, reprise, fermeture. |
| `core/video_encoders.py` | Choix de l'encodeur (point d'extension matériel). |
| `ui/render_queue_panel.py`, `ui/export_panel.py` | Interface. |

## Presets

`builtin_presets()` : **H.264 1080p / 1440p / 4K**, **YouTube**,
**TikTok / Vertical** (1080×1920), **ProRes Master** ; **Custom** se construit
avec `custom_preset(...)` (validé). Un preset produit sa configuration pour le
moteur via `spec.export_parts()` → `(ExportFormat, ExportPreset, fps)` ;
`quality` est un CRF (H.264) ou un profil (ProRes). Pour ajouter un preset,
ajouter une ligne à `_BUILTIN` et ses libellés `render.preset.<id>` /
`render.preset.desc.<id>` dans `ui/i18n.py`.

## RenderJob

Contient : `id`, `name`, `project_name`, `snapshot_path`, `output_path`
(`file_name`), `container`, `video_codec`, `audio_codec`, `width`/`height`,
`fps`, `quality`, `audio_bitrate`, `preset_id`, `hardware`, `created_at`,
`started_at`, `finished_at`, `progress`, `status`, `error_message`,
`error_kind`, `result` (taille, durée de rendu, encodeur utilisé…).

```
WAITING ──▶ RENDERING ──▶ COMPLETED | FAILED | CANCELLED
   ▲                              │
   └──────────── retry ───────────┘
```

Le projet n'est **pas** stocké dans la file : à l'ajout, une copie du projet est
écrite dans `render_queue/jobs/<id>/project.kut` (supprimée avec le job). Le job
rend donc le montage tel qu'il était à l'ajout, même si on continue à monter ou
si l'on ferme le projet ; `queue.json` reste de quelques centaines d'octets par
job. Les médias sont référencés, pas copiés.

**Pas d'état `Paused`** : FFmpeg ne reprend pas un encodage, et suspendre le
processus n'est pas faisable de façon fiable avec `QProcess` sur les trois
plateformes. Annuler puis relancer est le comportement honnête.

## Garanties

- **Interface fluide** : FFmpeg tourne dans un `QProcess` (événementiel) ; un
  seul rendu à la fois ; l'enchaînement est différé d'un tour de boucle Qt.
- **Fichier final atomique** : rendu dans `.<nom>.<id>.partial.<ext>`, renommé
  après succès. Échec ou annulation : aucun fichier partiel, et un ancien fichier
  de même nom est préservé.
- **Interruption** : un job `RENDERING` relu au démarrage devient `FAILED`
  (`error_kind == "interrupted"`), jamais `COMPLETED`. Les jobs en attente sont
  restaurés ; ceux dont l'instantané a disparu échouent proprement.
- **Fermeture** : confirmation si un rendu est en cours (refuser garde
  l'application ouverte) ; sinon `RenderQueue.shutdown()` tue FFmpeg et attend sa
  fin. Limite : après un arrêt brutal (`SIGKILL`, coupure de courant) FFmpeg peut
  finir seul son fichier `.partial`, supprimé au prochain démarrage.
- **FFmpeg absent** : bannière dans la file, boutons de lancement grisés ; un job
  lancé quand même échoue avec `ffmpeg_missing` et peut être relancé une fois
  FFmpeg installé.

## API de `RenderQueue`

`enqueue(project, spec, output_path, ...)`, `start_all()`, `start_job(id)`,
`stop()`, `cancel(id)`, `remove(id)`, `retry(id)`, `clear_finished()`,
`move(id, offset)`, `restore()`, `shutdown()`, `overall_progress()`.
Signaux : `jobs_changed`, `job_updated(id)`, `overall_progress_changed`,
`run_state_changed`, `run_finished(summary)`.

## Accélération matérielle (préparée, non implémentée)

`HardwareEncoder` (`cpu`, `auto`, `videotoolbox`, `nvenc`, `qsv`, `amf`, `vaapi`)
est déjà accepté et sérialisé par `RenderPresetSpec.hardware`,
`RenderJob.hardware` et `ExportRequest.hardware`. Toute valeur autre que CPU
retombe aujourd'hui sur `libx264` / `prores_ks`, avec la raison dans
`RenderResult.fallback_reason`.

Pour brancher un encodeur, **un seul fichier** : `core/video_encoders.py`.

1. écrire un constructeur `_build_<encodeur>(codec, speed_preset, quality)` ;
2. l'enregistrer dans `_BUILDERS[(codec, HardwareEncoder.X)]` ;
3. déclarer sa disponibilité (`AVAILABLE_BY_DEFAULT` ou détection via
   `ffmpeg -encoders` dans `is_available`) ;
4. traiter `AUTO` dans `_auto_choice`.

L'interface (sélecteur d'encodeur dans les presets) viendra avec ce chantier.

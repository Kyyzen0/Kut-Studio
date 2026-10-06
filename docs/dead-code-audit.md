# Audit du code mort

Point ouvert du rapport de stabilisation : « code mort probable (`core/timeline_model.py`, `core/effects.py`, certaines
fonctions `mix_*` d'`audio_mixer`) ; non supprimé faute de test qui le garantisse ». Cette page est le dossier de preuves.

**Règle appliquée.** Rien n'est supprimé sur intuition. Un fichier ou une fonction part seulement si, à la fois :
aucun import statique (absolu, relatif, `from core import x`, imports paresseux dans une fonction), aucun import
dynamique, aucun usage réel depuis `ui/` ou `main.py`, aucun test légitime (un test qui n'exerce que le code mort est une
preuve d'abandon, il part avec lui) et aucune compatibilité de projet qui en dépende. Chaque suppression laisse un test qui
prouve que le chemin moderne est celui qu'utilise l'application.

## Méthode et limites

* `grep -rn` sur `core/`, `ui/`, `tools/`, `tests/`, `docs/`, `main.py`, `build.py` pour chaque nom (module, fonction,
  constante), y compris `from core import x`, `import core.x`, `from . import x`, `from .x import y`.
* **Références dynamiques** : aucun `importlib`, `__import__`, `pkgutil` ni entry point dans `core/` et `ui/` (le seul
  `importlib.util.find_spec("numpy")` teste une dépendance). `build.py` n'a ni `hiddenimports` ni `collect_submodules`, et il
  n'y a pas de fichier `.spec` : PyInstaller part de `main.py` et suit les imports statiques, donc un module que personne
  n'importe n'était de toute façon pas dans l'exécutable.
* **Compatibilité des projets** : `core/project_io.py` n'importe aucun des modules supprimés, et aucun n'écrit ni ne lit un
  champ du `.kut` (le format est défini par `project_io`). Un `.kut` ancien ne peut donc pas en dépendre.
* **Balayage mécanique** (appendice) : graphe d'imports par `ast` depuis `main.py`, et liste des fonctions et classes dont le
  nom n'est cité nulle part dans le code de production. Il ne voit pas les noms construits dynamiquement (`getattr(obj, f"on_{x}")`) :
  `core/` et `ui/` n'en ont pas pour des fonctions, mais la liste de l'appendice est un relevé, pas une preuve. Au départ le
  balayage trouvait 217 symboles sans référence ; après les suppressions ci-dessous il en reste 205 (69 fonctions et classes, 136
  méthodes). Côté modules : 211 modules de `core/` et `ui/`, un seul inatteignable au départ (`core/timeline_model.py`), puis
  `core/effects.py` et `core/graphics_raster.py` une fois leurs derniers appelants retirés ; il en reste 208, tous atteignables.

Deux gardes génériques sont désormais dans la suite (`tests/test_dead_code_guard.py`) : **tout module de `core/` et `ui/` est
atteignable depuis `main.py`**, et **aucune fonction privée de `core/` ou `ui/` n'est définie sans être citée quelque part**.

## Dossier

| Candidat | Preuves | Décision | Garde-fou |
|---|---|---|---|
| `core/timeline_model.py` (ancien modèle de timeline en dictionnaires : `default_clips`, `move_clip`, `trim_clip`, `cut_clip`, `delete_clip`, `v1_transition_pairs`…) | Importé par rien dans `core/`, `ui/`, `tools/`, `main.py`, `build.py` ; seul `tests/test_timeline_model.py` (4 tests qui n'exerçaient que lui) l'importait. Les homonymes (`cut_clip`, `move_clip`, `v1_transition_pairs`) vivent dans `core.timeline_operations` et `core.timeline_view_model`. Un test d'interface (`test_ui_does_not_import_legacy_timeline_model`) le disait « provisoirement gardé pour ses tests » mais ne lisait que `ui/*.py` (pas les sous-dossiers). L'éditeur coupe avec `core.timeline_operations.cut_clip` sur un `Project`. | **Supprimé** (module, son test, le test d'interface devenu inutile) | `test_every_core_and_ui_module_is_reachable_from_the_entry_point` ; `test_the_legacy_timeline_model_is_gone_and_the_editor_cuts_through_timeline_operations` (la fenêtre appelle `timeline_operations.cut_clip` avec un `Project` et produit `intro-split-2`, l'ancien module aurait produit `Intro_2`). Les deux échouent si le module revient. |
| `core/effects.py` · `set_volume` | **Utilisé** : le curseur « Audio > Volume » de l'inspecteur appelle `MainWindow.update_volume`. Une ligne : `audio_output.setVolume(valeur / 100)`. | Inliné dans `update_volume` ; le module n'avait plus de raison d'être (il importait `QtWidgets` depuis `core/`) | `test_core_effects_is_gone_and_its_one_live_use_is_wired_directly` (curseur à 50 : texte « 50 % » et volume du moniteur à 0,5) |
| `core/effects.py` · `apply_color_effect` | Appelé par `MainWindow.update_color_effect`, qui **retourne dès que** `getattr(self.preview_panel, "color_effect", None)` **vaut `None`**, avant tout appel. `color_effect` n'est affecté nulle part (ni `setattr`, ni test) : le chemin ne s'exécutait jamais. L'étalonnage se voit par l'invalidation et la resynchronisation de l'aperçu (`_refresh_color_monitor`). | **Supprimé**, avec `update_color_effect`, son appel dans `_refresh_color_monitor` et le paramètre `update_color_effect` du constructeur de `PropertiesPanel` (stocké, jamais lu) | `test_a_colour_change_reaches_the_monitor_through_the_preview_pipeline` |
| `core/effects.py` · `play_crossfade_preview` | Appelé par `offer_transition`, branché sur `timeline_panel.transition_clicked`, signal **jamais émis** (une seule définition, une seule connexion, aucun `.emit`). Une transition se règle dans l'inspecteur (`transition_selected`). | **Supprimé**, avec `offer_transition`, le signal, `MainWindow.transition_seconds`, la pastille `preview_transition_overlay` de l'aperçu (cachée en permanence) | `test_a_transition_picked_on_the_timeline_goes_to_the_inspector` |
| `core.audio_mixer` · `mix_at`, `mix_range`, `MixSpec`, `MixEntry` et leurs aides (`fade_envelope`, `db_to_linear`, `linear_to_db`, `audio_tracks`, `has_solo`, `is_audible`, `audible_track_ids`, `clip_gain_db_at`) | Aucun appel dans `core/`, `ui/`, `tools/`, `main.py` : seulement `tests/test_audio_mixer.py` (20 tests) et un test de prise audio. La docstring annonçait des consommateurs (interface, `render_plan`, `export_engine`) qui ne l'appellent pas. **Divergence** avec le chemin réel : `is_audible` disait « le solo l'emporte sur le mute », alors que `render_plan` écarte d'abord une piste muette, solo ou non. `docs/nested-sequences.md` précisait que `mix_at` ignorait les clips imbriqués. Le mixage réel (sourdine, solo, gains, pan, fondus, effets, automation, ducking, séquences imbriquées) est `build_render_plan` + `export_engine._build_audio_filter`. | **Supprimé** | `test_mute_and_solo_decide_which_audio_tracks_reach_the_render_plan` (fixe le comportement réel, y compris sourdine prioritaire) ; tests existants du plan et du filtre d'export ; `test_every_public_function_of_the_audio_mixer_is_used_by_the_export` |
| `core.audio_mixer` · `pan_to_gains`, `pan_needs_filter` | Utilisés par `export_engine` (filtre de panoramique). | **Conservés** | tests de panoramique et de filtre d'export |
| `core.audio_mixer` · `MAX_GAIN_DB`, `MIN_GAIN_DB` | `export_engine` les importe depuis `audio_mixer` (deux imports paresseux). | **Conservés** (ré-exportés, commentaire dans le module) ; l'import devrait un jour viser `project_model` | — |

### Autres candidats évidents, même rigueur

| Candidat | Preuves | Décision | Garde-fou |
|---|---|---|---|
| `export_engine._build_graphic_layer_filter` (71 lignes), `_graphic_input_path`, `_ffmpeg_graphic_color`, `_build_graphic_opacity_expr`, `_compute_colorbalance_highlights_shadows` | Un seul `grep` positif par nom pour `_build_graphic_layer_filter`, `_graphic_input_path` et `_compute_colorbalance_highlights_shadows` : la définition. Ni test, ni doc. L'export des calques graphiques passe par `core.mograph_ffmpeg`. `_ffmpeg_graphic_color` et `_build_graphic_opacity_expr` n'étaient cités que par `_build_graphic_layer_filter` (cascade vérifiée en relançant le balayage après la première suppression). | **Supprimées** | `test_no_private_module_function_is_defined_without_a_single_use` |
| `core/graphics_raster.py` (`rasterize_text_graphic`, titres en PNG) | Après la suppression de `_graphic_input_path`, son seul appelant, plus aucun import en production ; seul `test_cache_roots` l'exerçait (emplacement du cache « graphics »). Ce module ne lit ni n'écrit de champ du `.kut`. | **Supprimé** (et le test de cache correspondant) | accessibilité depuis `main.py` |
| `render_plan._subtitle_cues_for_export`, `track_operations._is_valid_track_id`, `audio_automation._require_point`, `ui/theme._compat_stylesheet` | Un seul `grep` positif : la définition. | **Supprimées** | `test_no_private_module_function_is_defined_without_a_single_use` |
| `timeline_view_model.build_export_clips`, `transition_gap_pixels`, `v1_transition_pairs` | Aucun appelant hors de leurs tests. `build_export_clips` était importé par `ui/main_window.py` sans servir ; sa docstring citait `core.effects.save_subtitles`, qui n'existe pas. L'export (vidéo et `.srt`) consomme `RenderPlan`. | **Supprimés** (avec 4 tests qui ne les exerçaient qu'eux) | `test_the_view_model_keeps_only_what_the_timeline_uses` |

Jetons de thème `transition_overlay` / `transition_overlay_bg` (`ui/theme.py`) : désormais sans lecteur, **conservés** (valeurs de palette
déclarées pour chaque thème ; les retirer touche les tests de thème sans autre gain).

## Observation hors périmètre

Le curseur « Audio > Volume » de l'inspecteur (0 à 200 %) ne règle que le volume de lecture du moniteur : la valeur n'est ni
enregistrée dans le projet ni utilisée à l'export, et `QAudioOutput` borne à 1,0 (au-delà de 100 % le curseur ne change rien
d'audible). C'est le seul usage vivant de l'ancien `core/effects.py` ; à trancher (supprimer le curseur, ou lui donner un vrai
effet) plutôt que de le laisser promettre ce qu'il ne fait pas. *Depuis* : il s'appelle « Volume du moniteur », son
infobulle précise qu'il n'est ni enregistré ni exporté, et il est borné à 0–100 %.

## Appendice : relevé mécanique non instruit

Fonctions et classes publiques de `core/` et `ui/` dont le nom n'apparaît nulle part dans le code de production (hors définition) à
la fin de l'audit. **Ce n'est pas une liste de suppressions** : beaucoup ont des tests, certaines sont une API déclarée ou un point
d'extension, d'autres des gestionnaires d'interface jamais branchés. À instruire au cas par cas avec la même grille.

* **Sans aucun appelant, sans test** : `core.animation_targets.all_targets`, `core.canvas_guides.remove_guide`,
  `core.filter_graph.build_input_list`, `core.gpu_effects.gaussian_weights` et `effect_support`,
  `core.mograph_layers.move_layer` et `offset_animation`, `core.mograph_presets.delete_user_preset`,
  `core.mograph_stream.still_playlist`, `core.sequences.clip_media_type`, `nested_overflow` et `reachable_sequences`,
  `core.timeline_editing.marker_near`, `core.timeline_navigation.format_clock`, `core.timeline_operations.apply_clip_transform_on_move`
  et `duplicate_clip_preserving_transform`, `core.tracking_bindings.curve_for`, `core.tracking_ops.remove_correction`
  et `remove_stabilization`, `core.transitions.transition_pairs`, `ui.icons.available_icons`, `ui.theme.colors_dict`,
  les classes d'erreur `AudioAutomationCycleError` et `ColorGradingCycleError`.
* **Appelées par leurs seuls tests** (le test est la preuve d'abandon à examiner) : `core.scopes.compute_histogram`, `compute_waveform`,
  `compute_parade`, `compute_vectorscope`, `compute_alerts`, `rgb_to_yuv709`, `skin_tone_reference` ; `core.subtitle_io.format_ass` ;
  `core.proxy_profiles.register_codec`, `register_profile`, `builtin_profiles` ; `core.track_operations.is_track_editable`,
  `collect_track_ids`, `visible_video_track_ids` ; `core.text_presets.get_builtin_preset`, `builtin_text_preset_ids` ;
  `core.transitions.transition_family` ; `core.video_encoders.crf_for_intent` ; `core.library_organization.filter_assets_present` ;
  `core.color_grading.filter_color_presets` ; `core.preview_render.render_quality_label` ; `core.timeline_editing.snap_edit_position` ;
  `core.tracking_motion.robust_fit` ; `core.transition_presets.apply_transition_preset` ; dix helpers de `core/time_remapping.py`
  (`source_to_timeline_time`, `compute_timeline_duration`, `compute_source_duration`, `set_speed`, `set_reverse`,
  `toggle_reverse`, `remove_freeze_frame`, `can_be_frozen`, `get_speed_from_preset`, `estimate_ffmpeg_memory_usage`) ;
  `enabled_effects`, `clip_effects` et `clip_audio_effects` (`core/effects_model.py`, `core/audio_effects_model.py`), `effect_category`,
  `builtin_preset_ids` et `combined_library` (`core/effects_library.py`).
* **Gestionnaires d'interface jamais connectés** (méthodes) : `ui/main_window_mixins/audio.py` définit
  `on_track_role_changed`, `on_track_automation_point_added/removed/updated`, `on_ducking_sidechain_added/removed` et
  `on_ducking_config_changed`, qu'aucun signal du mixeur ne déclenche. Ce n'est pas du code mort à supprimer mais une fonction
  à moitié branchée (le service `AudioAutomationService`, le plan de rendu et l'export de l'automation et du ducking, eux, sont
  vivants et testés) : l'interface ne permet pas encore d'éditer l'automation ni le ducking.
* 136 méthodes de classe sont dans le même cas (accesseurs `ui/project_panel.py`, `ui/workspace/manager.py`, etc.), non détaillées ici.

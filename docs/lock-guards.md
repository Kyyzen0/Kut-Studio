# Verrous : qui garde quoi

Une seule règle, `core/compositing_guard.py` : `clip_edit_refusal(project, clip_id, *, visual_only, track_types)` donne le code du
refus (piste verrouillée, calque graphique verrouillé, clip d'un mauvais genre de piste) ; `require_clip_editable` la lève en
`ClipEditRefused` (un `ValueError`) pour le cœur. L'interface passe par `ClipEditGuardMixin._clip_edit_refused` (message court
`edit.refused.*` dans la barre d'état, rien d'écrit, rien dans l'historique). Les champs de l'inspecteur sont aussi désactivés.

## Gardé (testé dans `tests/test_lock_guards_entries.py`, `tests/test_inspector_lock_guards.py`, `tests/test_graph_editor_lock_guards.py`)

- **Cœur** : tous les mutateurs de `core/keyframe_editing.py` (garde `_guard`, vérifiée avant la première écriture, genre de la propriété compris) ;
  `set_clip_transform`, `set_transform_keyframe`, `remove_transform_keyframe`, `reset_clip_transform` (via `_require_video_clip`) ;
  `add_transition`, `update_transition`, `remove_transition` ; `add_subtitle_clip` ; `apply_impact_on_cuts`.
- **Menu Réseaux sociaux** : Remplir le cadre, Recadrer en suivant le tracker, Ken Burns, zoom d'impact, secousse (une sélection mixte est refusée en entier ;
  les clips audio et de sous-titres sont ignorés : ils n'ont ni cadrage ni mouvement), animation de texte, synchro sur la voix.
- **Sous-titres** : saisie, style, remise à zéro, modèles de texte (piste de sous-titres exigée), groupe de l'inspecteur désactivé.
- **Transitions** : préréglages de la bibliothèque, type et durée, suppression (groupe de l'inspecteur désactivé).
- **Inspecteur, moniteur, images-clés, Vitesse, effets, éditeur de courbes, préréglages d'effets, scripts** : voir les PR de garde précédentes.

## Volontairement non gardé

- **Annuler / rétablir, chargement de projet, rendu** : instantanés ou lectures du projet, ils n'appellent aucun mutateur gardé ; un clip verrouillé
  revient à l'état de l'étape (testé).
- **Constructeurs qui prennent un `Clip` seul** (`apply_ken_burns`, `apply_impact_zoom`, `apply_camera_shake`, `apply_follow_reframe`,
  `apply_text_animation`) : appelés sur des clips que l'on vient de créer (modèles de projet, sous-titres automatiques). Leurs entrées
  d'interface sont gardées (ci-dessus) ; un nouvel appelant sur un clip existant doit le faire aussi.
- **Création de clips** (graphique, flash, sous-titre automatique) : elle choisit une piste libre ou refuse une piste verrouillée.

## Non audité ici

Les gestionnaires de la bibliothèque, des séquences, des pistes et du Multicam gardent leurs propres refus dans le cœur (`_ensure_track_editable`,
`sequences`, `multicam_ops`) ; cette PR ne les a pas réexaminés un par un.

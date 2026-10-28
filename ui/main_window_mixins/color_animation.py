"""Méthodes de ``MainWindow`` regroupées : images clés de l'étalonnage (ADR-0008, étape 8).

Le **câblage** entre l'inspecteur (sept réglages), la page Couleur (quatre roues) et :mod:`core.grade_keyframing` /
:mod:`core.keyframe_editing`. La règle est celle des effets : un réglage sans image clé écrit sa valeur de **base** ; animé, la
saisie écrit (ou met à jour) l'image clé à la tête de lecture. La fenêtre décide seule de la destination (le curseur et la roue
émettent toujours le même signal), de sorte qu'un signal qui atteint le modèle suit la même règle que le geste de l'interface.

* losange : retire les images clés sous la tête, sinon en pose (une étape d'historique) ;
* saisie / roue d'un réglage animé : une rafale = une étape (la session d'historique de l'étalonnage, 400 ms) ;
* verrous : piste ou calque verrouillé, piste d'un autre genre : refus **avant** toute écriture (:meth:`_clip_edit_refused`) ;
* un étalonnage découpé en nœuds ne s'anime pas : ses valeurs saisies restent des valeurs de base (le rendu ignore les clés) ;
* « Réinitialiser » retire aussi les images clés (même étape d'historique, annulable) : voir :meth:`_reset_grade_keys`.
"""

from __future__ import annotations

import logging

from core.color_grading import Wheel
from core.grade_keyframing import (
    clear_grade_animation,
    grade_animation_info,
    grade_keyframes,
    live_grade_ids,
    set_grade_keys,
    toggle_grade_keys,
    wheel_property_ids,
)
from core.grade_targets import SCALAR_NAMES, WHEEL_CHANNELS, is_animatable_grade, scalar_property_id
from core.keyframe_editing import find_clip, next_keyframe_time, previous_keyframe_time
from ui import i18n

LOGGER = logging.getLogger(__name__)


class ColorAnimationMixin:
    """Mixin de ``MainWindow`` : losanges, saisie et réinitialisation de l'étalonnage animé."""

    _grade_widgets_animated = False        # les widgets montrent une animation d'étalonnage (sinon la tête de lecture ne les touche pas)

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def _grade_clip(self, clip_id: str | None):
        if not clip_id:
            return None
        try:
            return find_clip(self.project, clip_id)
        except KeyError:
            return None

    # ------------------------------------------------------------------
    # Rafraîchissement des vues (inspecteur et page Couleur)
    # ------------------------------------------------------------------

    def _refresh_grade_keyframes(self, clip_id: str) -> None:
        """Curseurs animés, losanges et roues du clip affiché, relus du modèle (après toute modification d'images clés)."""
        clip = self._grade_clip(clip_id)
        if clip is None:
            return
        panel = self.properties_panel
        if getattr(getattr(panel, "selected_clip", None), "id", None) == clip_id:
            panel.refresh_color_keyframes(grade_keyframes(clip), self.playhead_seconds)
        self._refresh_color_animation(clip)

    def _refresh_color_animation(self, clip) -> None:
        """Roues et losanges de la page Couleur, si c'est elle qui montre ce clip."""
        from shiboken6 import isValid

        panel = getattr(self, "color_panel", None)
        if panel is None or not isValid(panel) or not panel.isVisible() or clip.id != self._color_clip_id():
            return
        panel.set_grade_animation(grade_animation_info(clip, self._local_playhead(clip)))

    def _refresh_grade_widgets(self, clip) -> None:
        """La tête de lecture a bougé : un clip à étalonnage animé remet à jour ses vues ; sans animation, rien (chemin d'avant)."""
        animated = bool(grade_keyframes(clip))
        if not animated and not self._grade_widgets_animated:
            return
        self._grade_widgets_animated = animated
        self._refresh_grade_keyframes(clip.id)

    # ------------------------------------------------------------------
    # Losanges
    # ------------------------------------------------------------------

    def on_color_keys_toggled(self, clip_id: str, property_ids) -> None:
        """Losange d'un réglage (ou d'une roue) : retire les images clés sous la tête, sinon en pose ; une étape d'historique."""
        clip = self._grade_clip(clip_id)
        ids = list(property_ids)
        if clip is None or not ids:
            return
        self._finalize_color_history()
        if self._clip_edit_refused(clip_id):
            return
        if not self._playhead_in_clip(clip):
            self._refresh_grade_keyframes(clip_id)
            return
        try:
            toggle_grade_keys(self.project, clip_id, ids, self._local_playhead(clip))
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            self._refresh_grade_keyframes(clip_id)
            return
        self.set_active_animation_property(ids[0])
        self._after_animation_edit(clip_id, i18n.translate("history.color.keyframe"))

    def on_color_wheel_keys_toggled(self, wheel: str) -> None:
        clip_id = self._color_clip_id()
        if clip_id is not None:
            self.on_color_keys_toggled(clip_id, wheel_property_ids(wheel))

    def on_color_quick_keys_toggled(self, field: str) -> None:
        """Losange d'un réglage rapide de la page Couleur : la commande du losange de l'inspecteur."""
        clip_id = self._color_clip_id()
        if clip_id is not None and field in SCALAR_NAMES:
            self.on_color_keys_toggled(clip_id, [scalar_property_id(field)])

    def on_color_quick_graph_requested(self, field: str) -> None:
        """Éditeur de courbes sur un réglage rapide (sans animation possible, l'éditeur s'ouvre sans propriété)."""
        clip = self._grade_clip(self._color_clip_id())
        if clip is not None and field in SCALAR_NAMES:
            self.open_graph_editor(scalar_property_id(field) if is_animatable_grade(getattr(clip, "color_grade", None)) else None)

    def on_color_wheel_keys_navigated(self, wheel: str, direction: int) -> None:
        """Flèches ‹ › d'une roue : la tête va à l'image clé précédente / suivante de l'un de ses quatre canaux."""
        clip = self._grade_clip(self._color_clip_id())
        if clip is None or not is_animatable_grade(getattr(clip, "color_grade", None)):
            return
        local = float(self.playhead_seconds) - float(clip.timeline_start)
        ids = wheel_property_ids(wheel)
        target = (next_keyframe_time if direction > 0 else previous_keyframe_time)(clip, ids, local)
        if target is not None:
            self.set_active_animation_property(ids[0])
            self.seek_to_position(float(clip.timeline_start) + target)

    def on_color_wheel_graph_requested(self, wheel: str) -> None:
        """Éditeur de courbes sur la roue : son premier canal animé, sinon le premier."""
        clip = self._grade_clip(self._color_clip_id())
        if clip is None:
            return
        ids = wheel_property_ids(wheel)
        animated = live_grade_ids(clip, ids)
        self.open_graph_editor((animated or ids)[0] if is_animatable_grade(getattr(clip, "color_grade", None)) else None)

    def on_color_animation_cleared(self, clip_id: str | None) -> None:
        """« Supprimer l'animation de l'étalonnage » : toutes les courbes ``color.*``, chaque réglage garde la valeur montrée à la
        tête ; une seule étape d'historique."""
        clip_id = clip_id or self._color_clip_id()
        clip = self._grade_clip(clip_id)
        if clip is None or clip_id is None:
            return
        self._finalize_color_history()
        if self._clip_edit_refused(clip_id):
            return
        try:
            cleared = clear_grade_animation(self.project, clip_id, self._local_playhead(clip))
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        if cleared:
            self._after_animation_edit(clip_id, i18n.translate("history.color.clear_animation"))

    # ------------------------------------------------------------------
    # Saisie : base ou image clé
    # ------------------------------------------------------------------

    def _write_grade_keys(self, clip_id: str, values: dict[str, float], label_key: str) -> bool:
        """Écrit ``values`` (``color.*`` → valeur) en images clés à la tête de lecture, rafale regroupée en une étape.

        Retourne ``False`` si rien n'a été écrit : clip introuvable, ou refus (verrou, genre de piste) et erreur, déjà dits à
        l'utilisateur (l'inspecteur reprend les valeurs du modèle)."""
        clip = self._grade_clip(clip_id)
        if clip is None or self._clip_edit_refused(clip_id):
            return False
        try:
            set_grade_keys(self.project, clip_id, values, self._local_playhead(clip))
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            self._refresh_grade_keyframes(clip_id)
            return False
        self.set_active_animation_property(next(iter(values)))
        self._schedule_color_history(i18n.translate(label_key), clip_id)
        self._refresh_grade_keyframes(clip_id)
        self._refresh_color_monitor(clip_id)
        return True

    def _route_grade_field(self, clip_id: str, field: str, value: float) -> bool:
        """Réglage de l'inspecteur : ``True`` si le réglage est animé (la valeur est partie dans l'image clé de la tête)."""
        if field not in SCALAR_NAMES:
            return False
        property_id = scalar_property_id(field)
        if not live_grade_ids(self._grade_clip(clip_id), [property_id]):
            return False
        self._write_grade_keys(clip_id, {property_id: float(value)}, "history.color.keyframe")
        return True

    def _route_grade_wheel(self, clip_id: str, name: str, wheel: Wheel, base: Wheel) -> Wheel | None:
        """Roue de la page Couleur. Canaux animés : images clés de la tête (la roue entière). Les autres canaux : valeur de base.

        Retourne la roue à écrire en base (``wheel`` si aucun canal n'est animé, ``base`` pour les canaux animés), ou ``None``
        si tout est déjà géré (quatre canaux animés, ou refus)."""
        ids = wheel_property_ids(name)
        animated = live_grade_ids(self._grade_clip(clip_id), ids)
        if not animated:
            return wheel
        values = {wheel_property_id: float(getattr(wheel, channel))
                  for wheel_property_id, channel in zip(ids, WHEEL_CHANNELS) if wheel_property_id in animated}
        if not self._write_grade_keys(clip_id, values, "history.color.keyframe") or len(animated) == len(ids):
            return None
        return Wheel(**{channel: (getattr(base, channel) if pid in animated else getattr(wheel, channel))
                        for pid, channel in zip(ids, WHEEL_CHANNELS)})

    # ------------------------------------------------------------------
    # Réinitialisation
    # ------------------------------------------------------------------

    def _reset_grade_keys(self, clip_id: str) -> int:
        """Réinitialiser l'étalonnage retire **aussi** ses images clés (décision de l'étape 8, voir l'ADR) : sans cela elles
        continueraient d'animer un réglage neutre. À appeler **avant** d'enregistrer l'étape d'historique (annuler les rétablit).
        Un étalonnage découpé en nœuds ne perd rien : ses images clés sont ignorées au rendu et gardent leur bouton de retrait.
        Retourne le nombre de réglages animés retirés."""
        clip = self._grade_clip(clip_id)
        if clip is None or not is_animatable_grade(getattr(clip, "color_grade", None)):
            return 0
        try:
            return clear_grade_animation(self.project, clip_id)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return 0

    def _announce_reset_keys(self, removed: int) -> None:
        if removed:
            self.statusBar().showMessage(i18n.translate("status.color.reset_keys", count=removed), 6000)

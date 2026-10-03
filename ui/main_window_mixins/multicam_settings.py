"""Méthodes de ``MainWindow`` regroupées : réglages d'une source Multicam (angles, audio, relink, resynchronisation).

La boîte :class:`~ui.multicam_settings.MulticamSettingsDialog` émet un signal par réglage ; chaque méthode ci-dessous applique
**une** opération de :mod:`core.multicam_ops`, enregistre **une** entrée d'historique et relit la boîte. Un réglage refusé est
dit dans la barre d'état et ne modifie rien. Le décalage d'un angle se corrige à la main ici (valeur saisie), ou en
déplaçant ses clips dans la source ouverte : c'est la même donnée.
"""

from __future__ import annotations

import logging

from core.multicam import is_multicam_clip
from core.multicam_model import MulticamAudio, SyncMethod, SyncStatus
from core.multicam_ops import (
    AngleSpec,
    MulticamError,
    SyncOutcome,
    add_angle,
    angle_usages,
    apply_sync,
    remove_angle,
    rename_angle,
    set_angle_color,
    set_angle_offset,
    set_audio_policy,
)
from core.sequences import find_sequence
from core.timeline_operations import find_clip
from ui import i18n
from ui.main_window_mixins.multicam_creation import _Source
from ui.multicam_dialogs import SourceRow
from ui.multicam_settings import MulticamSettingsDialog
from ui.theme import active_palette

LOGGER = logging.getLogger("kut_studio.multicam")


class MulticamSettingsMixin:
    """Mixin de ``MainWindow`` (réglages Multicam)."""

    def _init_multicam_settings(self) -> None:
        self._multicam_settings: MulticamSettingsDialog | None = None
        self._multicam_settings_source = ""

    # ------------------------------------------------------------------
    # Ouverture
    # ------------------------------------------------------------------

    def _multicam_source_for_settings(self, source_id: str | None) -> str | None:
        """Source à régler : celle demandée, celle du segment sélectionné ou sous la tête de lecture, ou l'unique du projet."""
        if source_id:
            return source_id
        from core.multicam_ops import multicam_segment_at

        selected = None
        if self.timeline_panel.selected_clip_id:
            try:
                selected = find_clip(self.project, self.timeline_panel.selected_clip_id)
            except KeyError:
                selected = None
        segment = selected if selected is not None and is_multicam_clip(self.project, selected) else multicam_segment_at(
            self.project, self.playhead_seconds
        )
        if segment is not None:
            return segment.sequence_id
        if self.project.active_sequence.multicam is not None:
            return self.project.active_sequence_id
        sources = [sequence.id for sequence in self.project.sequences if sequence.multicam is not None]
        return sources[0] if len(sources) == 1 else None

    def show_multicam_settings(self, source_id: str | None = None) -> None:
        """« Réglages Multicam… » : angles, décalages, politique audio de la source du segment courant."""
        target = self._multicam_source_for_settings(source_id)
        if target is None:
            self._report_edit_refused(i18n.translate("multicam.message.no_source"))
            return
        if self._multicam_settings is None:
            dialog = MulticamSettingsDialog(self)
            dialog.rename_requested.connect(self.rename_multicam_angle)
            dialog.color_requested.connect(self.set_multicam_angle_color)
            dialog.offset_requested.connect(self.set_multicam_angle_offset)
            dialog.audio_requested.connect(self.set_multicam_audio)
            dialog.add_requested.connect(self.add_multicam_angle)
            dialog.remove_requested.connect(self.remove_multicam_angle)
            dialog.resync_requested.connect(self.resync_multicam_source)
            dialog.relink_requested.connect(self._relink_multicam_media)
            self._multicam_settings = dialog
        self._multicam_settings_source = target
        self._refresh_multicam_settings()
        self._multicam_settings.show()
        self._multicam_settings.raise_()

    def _refresh_multicam_settings(self) -> None:
        dialog = self._multicam_settings
        if dialog is not None:
            dialog.set_state(self.project, self._multicam_settings_source)

    def _close_multicam_settings(self) -> None:
        dialog = getattr(self, "_multicam_settings", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            self._multicam_settings = None

    # ------------------------------------------------------------------
    # Une opération = une entrée d'historique
    # ------------------------------------------------------------------

    def _apply_multicam_setting(self, label_key: str, action, **fields) -> bool:
        try:
            action()
        except (KeyError, MulticamError, ValueError) as error:
            self._report_edit_refused(error)
            self._refresh_multicam_settings()           # la boîte retrouve la valeur réelle (saisie refusée)
            return False
        self._record_history(i18n.translate(label_key, **fields))
        self._after_multicam_edit()
        self._refresh_multicam_settings()
        return True

    def _angle_name(self, angle_id: str) -> str:
        source = find_sequence(self.project, self._multicam_settings_source).multicam
        angle = source.angle(angle_id) if source is not None else None
        return angle.name if angle is not None else angle_id

    def rename_multicam_angle(self, angle_id: str, name: str):
        source_id = self._multicam_settings_source
        if not name.strip() or name.strip() == self._angle_name(angle_id):
            self._refresh_multicam_settings()
            return None
        return self._apply_multicam_setting(
            "multicam.history.rename_angle", lambda: rename_angle(self.project, source_id, angle_id, name),
            name=self._angle_name(angle_id),
        )

    def set_multicam_angle_color(self, angle_id: str, index: int) -> bool:
        source_id = self._multicam_settings_source
        count = len(active_palette().angle_colors)
        return self._apply_multicam_setting(
            "multicam.history.angle_color",
            lambda: set_angle_color(self.project, source_id, angle_id, index % count), name=self._angle_name(angle_id),
        )

    def set_multicam_angle_offset(self, angle_id: str, seconds: float) -> bool:
        source_id = self._multicam_settings_source
        name = self._angle_name(angle_id)
        return self._apply_multicam_setting(
            "multicam.history.offset", lambda: set_angle_offset(self.project, source_id, angle_id, seconds), name=name,
        )

    def set_multicam_audio(self, audio: MulticamAudio) -> bool:
        source_id = self._multicam_settings_source
        return self._apply_multicam_setting(
            "multicam.history.audio_policy",
            lambda: set_audio_policy(self.project, source_id, audio.mode, audio.angle_ids),
        )

    def add_multicam_angle(self, asset_id: str) -> bool:
        source_id = self._multicam_settings_source
        asset = next((a for a in self.project.media_assets if a.id == asset_id), None)
        name = asset.name if asset is not None else asset_id
        return self._apply_multicam_setting(
            "multicam.history.add_angle",
            lambda: add_angle(self.project, source_id, AngleSpec(asset_id=asset_id, sync_method=SyncMethod.MANUAL)),
            name=name,
        )

    def remove_multicam_angle(self, angle_id: str, *, replace_with: str | None = None) -> bool:
        source_id = self._multicam_settings_source
        name = self._angle_name(angle_id)
        source = find_sequence(self.project, source_id).multicam
        usages = angle_usages(self.project, source_id, angle_id)
        replacement = replace_with or ""
        if usages and not replacement and source is not None:
            replacement = self._ask_replacement_angle(name, [a for a in source.angles if a.id != angle_id]) or ""
            if not replacement:
                return False                            # l'utilisateur renonce : rien n'est supprimé
        return self._apply_multicam_setting(
            "multicam.history.remove_angle",
            lambda: remove_angle(self.project, source_id, angle_id, replace_with=replacement), name=name,
        )

    def _ask_replacement_angle(self, name: str, candidates) -> str | None:
        """Angle qui remplacera celui qu'on supprime dans les segments qui le montrent."""
        from PySide6.QtWidgets import QInputDialog

        labels = [f"{index + 1}. {angle.name}" for index, angle in enumerate(candidates)]
        choice, accepted = QInputDialog.getItem(
            self, i18n.translate("multicam.settings.replace_title"),
            i18n.translate("multicam.settings.replace_label", name=name), labels, 0, False,
        )
        if not accepted:
            return None
        return candidates[labels.index(choice)].id

    def _relink_multicam_media(self, asset_id: str) -> None:
        handler = getattr(self, "_on_asset_relink_requested", None)
        if handler is not None:
            handler(asset_id)
            self._after_multicam_edit()
            self._refresh_multicam_settings()

    # ------------------------------------------------------------------
    # Resynchronisation par le son
    # ------------------------------------------------------------------

    def resync_multicam_source(self):
        """Relance la synchronisation par le son des angles de la source (un résultat = une entrée d'historique)."""
        source_id = self._multicam_settings_source
        sequence = find_sequence(self.project, source_id)
        source = sequence.multicam
        assets = {asset.id: asset for asset in self.project.media_assets}
        sources: list[_Source] = []
        for angle in (source.angles if source is not None else []):
            track = next((t for t in sequence.tracks if t.id == angle.track_id), None)
            clip = track.clips[0] if track is not None and track.clips else None
            asset = assets.get(clip.asset_id) if clip is not None else None
            if clip is None or asset is None or clip.is_nested:
                continue
            row = SourceRow(key=angle.id, name=angle.name, kind="audio" if track.type == "audio" else "video",
                            has_audio=bool(asset.has_audio))
            sources.append(_Source(row, asset=asset, clip=clip, track_type=track.type))
        if len(sources) < 2:
            self._report_edit_refused(i18n.translate("multicam.message.need_two"))
            return None
        return self._start_multicam_sync(sources, lambda result: self._apply_resync(source_id, result))

    def _apply_resync(self, source_id: str, result) -> None:
        outcomes = {
            item.key: SyncOutcome(item.offset, SyncMethod.AUDIO, item.status, item.confidence if item.offset is not None else None)
            for item in result.sources
        }
        name = find_sequence(self.project, source_id).name
        ok = self._apply_multicam_setting("multicam.history.sync", lambda: apply_sync(self.project, source_id, outcomes),
                                          name=name)
        failed = [key for key, o in outcomes.items() if o.status in {SyncStatus.FAILED, SyncStatus.UNCERTAIN}]
        if ok and failed:
            self._show_sequence_status(i18n.translate("multicam.message.resync_partial", count=len(failed)))

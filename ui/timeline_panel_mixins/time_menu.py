"""Sous-menu « Vitesse » du clic droit sur un clip : de « 25 % » à « Flux optique » en deux clics.

Le menu n'exécute rien : il retourne ``{action: (commande, argument)}`` et le panneau émet
``time_command_requested(clip, commande, argument)``. La fenêtre applique la commande par
:func:`core.time_commands.apply_time_command`, le même chemin que l'inspecteur, les raccourcis et le Graph Editor.
"""

from __future__ import annotations

from core.time_commands import SPEED_CHOICES, TimeCommand
from core.time_presets import TimePreset
from core.time_remapping import FlowQuality, FreezeFrameMode, TimeInterpolation
from ui.i18n import translate

_PRESET_ORDER = (
    TimePreset.SLOW_50, TimePreset.SLOW_25, TimePreset.FAST_2, TimePreset.FAST_4, TimePreset.RAMP_IN, TimePreset.RAMP_OUT,
    TimePreset.FREEZE,
)
Choice = tuple[str, object]


class TimeMenuMixin:
    """Mixin de ``TimelinePanel`` : entrées « Vitesse » du menu d'un clip."""

    time_ripple_timeline: bool = False
    """Réglage lu par le menu (posé par la fenêtre) : une édition de vitesse garde la durée sur la timeline."""

    def _clip_of_view(self, clip_id: str):
        project = getattr(self, "project", None)
        if project is None:
            return None
        for track in project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track.type
        return None

    def _add_time_menu(self, menu, clip_id: str) -> dict[object, Choice]:
        """Ajoute « Vitesse ▸ » à ``menu`` ; retourne ``{action: (commande, argument)}`` (vide si le clip n'a pas de vitesse)."""
        found = self._clip_of_view(clip_id)
        if found is None:
            return {}
        clip, track_type = found
        remapping = clip.time_remapping
        frozen = remapping.freeze_mode == FreezeFrameMode.FREEZE
        if track_type not in {"video", "audio"} or frozen:
            return {}
        actions: dict[object, Choice] = {}
        submenu = menu.addMenu(translate("time.menu.speed"))
        constant = not clip.has_speed_curve
        for speed in SPEED_CHOICES:
            action = submenu.addAction(f"{round(speed * 100)} %")
            action.setCheckable(True)
            action.setChecked(constant and abs(remapping.speed - speed) < 1e-9)
            actions[action] = (TimeCommand.SPEED.value, speed)
        submenu.addSeparator()
        reverse = submenu.addAction(translate("time.menu.reverse"))
        reverse.setCheckable(True)
        reverse.setChecked(remapping.reverse)
        actions[reverse] = (TimeCommand.REVERSE.value, not remapping.reverse)
        if track_type == "video":
            self._add_interpolation_entries(submenu, clip, actions)
        curve = submenu.addMenu(translate("time.menu.curve"))
        actions[curve.addAction(translate("time.menu.add_point"))] = (TimeCommand.ADD_POINT.value, None)
        actions[curve.addAction(translate("time.menu.hold"))] = (TimeCommand.HOLD.value, None)
        presets = curve.addMenu(translate("time.menu.presets"))
        for preset in _PRESET_ORDER:
            actions[presets.addAction(translate(f"time.preset.{preset.value}"))] = (TimeCommand.PRESET.value, preset.value)
        clear = curve.addAction(translate("time.menu.clear_curve"))
        clear.setEnabled(clip.has_speed_curve)
        actions[clear] = (TimeCommand.CLEAR_CURVE.value, None)
        audio = submenu.addMenu(translate("time.menu.audio"))
        pitch = audio.addAction(translate("time.menu.preserve_pitch"))
        pitch.setCheckable(True)
        pitch.setChecked(remapping.preserve_pitch)
        actions[pitch] = (TimeCommand.PRESERVE_PITCH.value, not remapping.preserve_pitch)
        follow = audio.addAction(translate("time.menu.remap_audio"))
        follow.setCheckable(True)
        follow.setChecked(remapping.remap_audio)
        actions[follow] = (TimeCommand.REMAP_AUDIO.value, not remapping.remap_audio)
        submenu.addSeparator()
        ripple = submenu.addAction(translate("time.menu.ripple"))
        ripple.setCheckable(True)
        ripple.setChecked(self.time_ripple_timeline)
        actions[ripple] = ("ripple", not self.time_ripple_timeline)
        actions[submenu.addAction(translate("time.menu.copy"))] = ("copy", None)
        actions[submenu.addAction(translate("time.menu.paste"))] = ("paste", None)
        actions[submenu.addAction(translate("time.menu.reset"))] = (TimeCommand.RESET.value, None)
        return actions

    @staticmethod
    def _add_interpolation_entries(submenu, clip, actions: dict[object, Choice]) -> None:
        remapping = clip.time_remapping
        group = submenu.addMenu(translate("time.menu.interpolation"))
        for mode in TimeInterpolation:
            action = group.addAction(translate(f"time.interpolation.{mode.value}"))
            action.setCheckable(True)
            action.setChecked(remapping.interpolation is mode)
            action.setToolTip(translate(f"time.interpolation.{mode.value}.tip"))
            action.setEnabled(not (clip.is_nested and mode is not TimeInterpolation.SAMPLING))
            actions[action] = (TimeCommand.INTERPOLATION.value, mode.value)
        flowing = remapping.interpolation is TimeInterpolation.OPTICAL_FLOW
        quality = submenu.addMenu(translate("time.menu.quality"))
        quality.setEnabled(flowing)
        for level in FlowQuality:
            action = quality.addAction(translate(f"time.quality.{level.value}"))
            action.setCheckable(True)
            action.setChecked(remapping.flow_quality is level)
            actions[action] = (TimeCommand.QUALITY.value, level.value)
        analyze = submenu.addAction(translate("time.menu.analyze"))
        analyze.setEnabled(flowing)
        actions[analyze] = ("analyze", None)

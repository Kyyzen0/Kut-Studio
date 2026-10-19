"""Audio d'un clip : gain, panoramique, fondus, effets audio du clip, ducking de la musique sous la voix (lot 2 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe fait ce que l'inspecteur audio fait (``ui/main_window_mixins/audio.py``).

- **fondus** : ``Clip.set_fade_in`` / ``set_fade_out`` / ``reset_fades``. Le modèle borne un fondu à ce qui reste de la durée du clip (la durée
  moins l'autre fondu) ; un script reçoit un fondu plus long **refusé** ;
- **gain et panoramique** : l'interface écrit ``Clip.gain_db`` / ``Clip.pan`` directement (le modèle n'a pas de mutateur) après avoir borné son
  curseur à ``MIN_GAIN_DB``…``MAX_GAIN_DB`` et −1…1 ; le verbe refuse une valeur hors de ces bornes, puis écrit le champ de la même façon ;
- **effets audio du clip** : :mod:`core.audio_effects_model` (``add_audio_effect_to_clip``…). Les paramètres sont validés par
  ``validate_parameters`` (``AUDIO_EFFECT_PARAMETER_SPECS``) qui *refuse* l'inconnu et le hors bornes ; ``move_clip_audio_effect`` refuse un
  déplacement qui sortirait de la chaîne (le modèle le bornerait) ;
- **ducking** : :func:`core.audio_automation.duck_music_under_voice`. Le verbe refuse si une piste « musique » (celle qui est atténuée) est
  verrouillée, ce que la commande de menu ne regarde pas.

Verrous : piste verrouillée (verbe puis modèle). Seuls les clips des pistes *audio* ont gain, panoramique et fondus (le groupe audio de
l'inspecteur n'existe que pour elles) ; les effets audio vont aussi sur les clips d'une piste *vidéo* (leur rack est éditable pour les deux).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..audio_automation import DUCKING_PRESETS, duck_music_under_voice
from ..audio_effects_model import (
    AudioEffectType, add_audio_effect_to_clip, clip_audio_effects, move_clip_audio_effect, remove_audio_effect_from_clip,
    set_clip_audio_effect_enabled, update_clip_audio_effect_parameters,
)
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import MAX_GAIN_DB, MAX_PAN, MIN_GAIN_DB, MIN_PAN, Project
from ..timeline_operations import find_clip
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _editable_clip, _pick, _require_unlocked

_MAX_DELTA = 1000
_TOLERANCE = 1e-9
_EFFECT = Param("effect", Kind.SELECTOR, "L'effet audio du clip, par identifiant.", of="audio_effect")
_PARAMS = Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres) ; ceux du modèle (noms et bornes) les valident.")


def _audio_clip(project: Project, selector: str) -> str:
    return _editable_clip(project, selector, track_types=("audio",))


def _effect_clip(project: Project, selector: str) -> str:
    return _editable_clip(project, selector, track_types=("audio", "video"))


# --- Gain, panoramique, fondus ------------------------------------------------------------------------------------------


def _set_clip_gain(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip = find_clip(project, _audio_clip(project, a["clip"]))
        clip.gain_db = float(a["gain_db"])                  # comme ``on_clip_gain_changed`` ; la borne est celle du paramètre
        return clip

    return operation


def _set_clip_pan(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip = find_clip(project, _audio_clip(project, a["clip"]))
        clip.pan = float(a["pan"])
        return clip

    return operation


def _fade(which: str):
    def build(a: Mapping[str, Any]) -> Operation:
        def operation(project: Project) -> Any:
            clip = find_clip(project, _audio_clip(project, a["clip"]))
            seconds = float(a["seconds"])
            room = max(0.0, float(clip.duration) - (clip.fade_out if which == "in" else clip.fade_in))
            if seconds > room + _TOLERANCE:                  # le modèle le réduirait à ``room`` : un script reçoit ce qu'il a écrit ou un refus
                other = "de sortie" if which == "in" else "d'entrée"
                raise ValueError(f"Un fondu de {seconds:g} s ne tient pas : au plus {room:g} s (la durée du clip moins le fondu {other}).")
            if which == "in":
                clip.set_fade_in(seconds)
            else:
                clip.set_fade_out(seconds)
            return clip

        return operation

    return build


def _reset_clip_fades(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip = find_clip(project, _audio_clip(project, a["clip"]))
        clip.reset_fades()
        return clip

    return operation


# --- Effets audio du clip ----------------------------------------------------------------------------------------------


def _audio_effect_id(project: Project, clip_id: str, selector: str) -> str:
    return str(_pick(clip_audio_effects(project, clip_id), selector, "Effet audio", names=False).id)


def _add_clip_audio_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _effect_clip(project, a["clip"])
        requested = a.get("id")
        if requested is not None:
            try:
                requested = resolve_object_id(requested, prefix="afx", kind="effet audio",
                                              taken={item.id for item in clip_audio_effects(project, clip_id)})
            except InvalidObjectId as error:
                raise ValueError(str(error)) from None
        return add_audio_effect_to_clip(
            project, clip_id, a["type"], effect_id=requested, params=a.get("params"), enabled=bool(a.get("enabled", True)),
        )

    return operation


def _remove_clip_audio_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _effect_clip(project, a["clip"])
        return remove_audio_effect_from_clip(project, clip_id, _audio_effect_id(project, clip_id, a["effect"]))

    return operation


def _set_clip_audio_effect_enabled(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _effect_clip(project, a["clip"])
        return set_clip_audio_effect_enabled(project, clip_id, _audio_effect_id(project, clip_id, a["effect"]), bool(a["enabled"]))

    return operation


def _update_clip_audio_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _effect_clip(project, a["clip"])
        if not a["params"]:
            raise ValueError("update_clip_audio_effect : donnez au moins un paramètre à modifier.")
        return update_clip_audio_effect_parameters(project, clip_id, _audio_effect_id(project, clip_id, a["effect"]), a["params"])

    return operation


def _move_clip_audio_effect(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _effect_clip(project, a["clip"])
        effect_id = _audio_effect_id(project, clip_id, a["effect"])
        chain = clip_audio_effects(project, clip_id)
        delta = int(a["delta"])
        target = next(index for index, item in enumerate(chain) if item.id == effect_id) + delta
        if delta == 0 or not 0 <= target < len(chain):
            raise ValueError(f"move_clip_audio_effect : déplacement impossible (la chaîne compte {len(chain)} effet(s)) ; le modèle le borne, un script le refuse.")
        return move_clip_audio_effect(project, clip_id, effect_id, delta)

    return operation


# --- Ducking -----------------------------------------------------------------------------------------------------------


def _duck_music_under_voice(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        for track in project.tracks:
            if track.type == "audio" and str(getattr(track.audio_role, "value", track.audio_role)) == "music":
                _require_unlocked(track)                     # l'atténuation change le son de cette piste
        return duck_music_under_voice(project, a.get("preset", "voice_over_music"))

    return operation


CLIP_AUDIO_VERBS: tuple[Verb, ...] = (
    Verb("set_clip_gain", "Règle le gain d'un clip d'une piste audio.",
         (_CLIP, Param("gain_db", Kind.NUMBER, "Gain en dB.", minimum=MIN_GAIN_DB, maximum=MAX_GAIN_DB)), _set_clip_gain, group="audio"),
    Verb("set_clip_pan", "Règle le panoramique d'un clip d'une piste audio.",
         (_CLIP, Param("pan", Kind.NUMBER, "Panoramique, de −1 (gauche) à 1 (droite).", minimum=MIN_PAN, maximum=MAX_PAN)), _set_clip_pan,
         group="audio"),
    Verb("set_clip_fade_in", "Règle le fondu d'entrée d'un clip audio ; plus long que ce qui reste de la durée du clip, il est refusé.",
         (_CLIP, Param("seconds", Kind.NUMBER, "Durée du fondu en secondes (0 : aucun).", minimum=0.0, maximum=MAX_TIME)),
         _fade("in"), group="audio"),
    Verb("set_clip_fade_out", "Règle le fondu de sortie d'un clip audio ; plus long que ce qui reste de la durée du clip, il est refusé.",
         (_CLIP, Param("seconds", Kind.NUMBER, "Durée du fondu en secondes (0 : aucun).", minimum=0.0, maximum=MAX_TIME)),
         _fade("out"), group="audio"),
    Verb("reset_clip_fades", "Retire les deux fondus d'un clip audio.", (_CLIP,), _reset_clip_fades, group="audio"),
    Verb("add_clip_audio_effect", "Ajoute un effet audio à la fin de la chaîne d'un clip audio ou vidéo ; paramètres absents : valeurs par défaut du modèle.",
         (_CLIP, Param("type", Kind.ENUM, "Le type d'effet.", choices=tuple(item.value for item in AudioEffectType)),
          Param("params", Kind.OBJECT, "Paramètres de l'effet (nombres) ; absents, les valeurs par défaut du modèle.", required=False),
          Param("enabled", Kind.BOOLEAN, "Actif ou non (défaut : actif).", required=False)),
         _add_clip_audio_effect, creates="audio_effect", creates_scope="clip", group="audio"),
    Verb("remove_clip_audio_effect", "Retire un effet audio d'un clip.", (_CLIP, _EFFECT), _remove_clip_audio_effect, group="audio"),
    Verb("set_clip_audio_effect_enabled", "Active ou désactive un effet audio d'un clip.",
         (_CLIP, _EFFECT, Param("enabled", Kind.BOOLEAN, "Actif ou non.")), _set_clip_audio_effect_enabled, group="audio"),
    Verb("update_clip_audio_effect", "Modifie des paramètres d'un effet audio de clip (noms et bornes du modèle, rien n'est écrêté) ; au moins un.",
         (_CLIP, _EFFECT, _PARAMS), _update_clip_audio_effect, group="audio"),
    Verb("move_clip_audio_effect", "Déplace un effet audio dans la chaîne du clip (-1 : monter, 1 : descendre) ; refusé s'il sortirait de la chaîne.",
         (_CLIP, _EFFECT, Param("delta", Kind.INTEGER, "Nombre de crans (non nul).", minimum=-_MAX_DELTA, maximum=_MAX_DELTA)),
         _move_clip_audio_effect, group="audio"),
    Verb("duck_music_under_voice", "Chaque piste audio de rôle « musique » baisse sous chaque piste de rôle « voix » (les rôles sont ceux du projet) ; "
                                   "une association qui existe déjà reçoit le nouveau réglage.",
         (Param("preset", Kind.ENUM, "Réglage du ducking (défaut : voice_over_music).", required=False, choices=tuple(DUCKING_PRESETS)),),
         _duck_music_under_voice, group="audio"),
)

__all__ = ["CLIP_AUDIO_VERBS"]

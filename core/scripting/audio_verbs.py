"""Audio avancé : rôle des pistes, courbe de volume, ducking, ordre des inserts (lot 7 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction que l'interface appelle (``ui/main_window_mixins/audio.py`` : menu ⋯ d'une piste audio,
bande de courbe de volume, boîtes d'inserts). Les règles communes sont dans :mod:`core.scripting.audio_rules`.

- **rôle** : ``AudioAutomationService.set_track_role`` ; piste audio déverrouillée, comme le menu ⋯ ;
- **courbe de volume** : un point se désigne par son **instant** (``at``, secondes de la timeline : la description n'a pas d'identifiant de point).
  Le modèle remplace en silence un point posé au même instant, et ne borne pas le gain : le verbe refuse l'un (``update_automation_point`` modifie)
  et borne l'autre à la plage de la bande (−24 à 12 dB, ``DEFAULT_GAIN_MIN_DB`` / ``MAX_DB``), que l'interface ne dépasse pas non plus ;
- **ducking** : ``add_ducking_sidechain`` crée l'association (identifiant ``id`` / ``as``, comme les autres créations), comme le menu
  « Baisser sous… » (réglage par défaut du modèle, deux pistes audio déverrouillées). Les rôles de piste ne sont pas exigés : l'interface ne les
  regarde pas et l'export les ignore. Une boucle (A sous B, B sous A…) est refusée. Retirer, activer, régler une association exige la piste
  « musique » (celle qui est baissée) déverrouillée, comme le menu de la bande de cette piste ;
- **ordre des inserts** : ``move_track_insert`` / ``move_bus_insert`` ; le modèle bornerait un déplacement qui sort de la chaîne, le verbe le refuse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..audio_automation import (
    DEFAULT_GAIN_MAX_DB, DEFAULT_GAIN_MIN_DB, DUCKING_PRESETS, MAX_DUCKING_ATTACK_S, MAX_DUCKING_REDUCTION_DB, MAX_DUCKING_RELEASE_S,
    MAX_DUCKING_THRESHOLD_DB, MAX_POINT_FADE_SECONDS, MIN_DUCKING_ATTACK_S, MIN_DUCKING_REDUCTION_DB, MIN_DUCKING_RELEASE_S,
    MIN_DUCKING_THRESHOLD_DB, MIN_POINT_FADE_SECONDS, AudioAutomationService, DuckingConfig, TrackRole,
)
from ..audio_buses import move_bus_insert
from ..audio_effects_model import move_track_insert
from ..object_ids import InvalidObjectId, resolve_object_id
from ..project_model import Project
from .audio_rules import (
    MAX_AUTOMATION_POINTS, audio_track, point_time, require_free_instant, require_no_ducking_loop, require_point_room,
    require_sidechain_room, sidechain_ids,
)
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _BUS, _INSERT, _TRACK, _bus_id, _insert_id, _pick, _require_unlocked, _track_insert

_MAX_DELTA = 1000
_TOLERANCE = 1e-9
_ROLES = tuple(item.value for item in TrackRole)

_AT = Param("at", Kind.NUMBER, "Instant du point sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME)
_GAIN = Param("gain_db", Kind.NUMBER, f"Gain du point en dB ({DEFAULT_GAIN_MIN_DB:g} à {DEFAULT_GAIN_MAX_DB:g}, la plage de la bande de courbe).",
              minimum=DEFAULT_GAIN_MIN_DB, maximum=DEFAULT_GAIN_MAX_DB)
_FADE = Param("fade_seconds", Kind.NUMBER, f"Maintien du gain précédent avant la rampe, en secondes ({MIN_POINT_FADE_SECONDS:g} à {MAX_POINT_FADE_SECONDS:g} ; 0 : ligne droite).",
              required=False, minimum=MIN_POINT_FADE_SECONDS, maximum=MAX_POINT_FADE_SECONDS)
_MUSIC = Param("music", Kind.SELECTOR, "La piste audio qui est baissée (« musique ») : identifiant ou nom exact (unique).", of="track")
_VOICE = Param("voice", Kind.SELECTOR, "La piste audio qui la baisse (« voix ») : identifiant ou nom exact (unique).", of="track")
_DUCKING = Param("ducking", Kind.SELECTOR, "L'association de ducking, par son identifiant.", of="ducking")
_TRACK_INSERT = Param("insert", Kind.SELECTOR, "L'insert de la piste, par son identifiant.", of="insert")
_DELTA = Param("delta", Kind.INTEGER, "Nombre de crans (non nul ; −1 : monter, 1 : descendre).", minimum=-_MAX_DELTA, maximum=_MAX_DELTA)
_CONFIG = (
    Param("threshold_db", Kind.NUMBER, f"Seuil de déclenchement en dB ({MIN_DUCKING_THRESHOLD_DB:g} à {MAX_DUCKING_THRESHOLD_DB:g}).",
          required=False, minimum=MIN_DUCKING_THRESHOLD_DB, maximum=MAX_DUCKING_THRESHOLD_DB),
    Param("reduction_db", Kind.NUMBER, f"Atténuation maximale en dB, positive ({MIN_DUCKING_REDUCTION_DB:g} à {MAX_DUCKING_REDUCTION_DB:g}).",
          required=False, minimum=MIN_DUCKING_REDUCTION_DB, maximum=MAX_DUCKING_REDUCTION_DB),
    Param("attack_seconds", Kind.NUMBER, f"Attaque en secondes ({MIN_DUCKING_ATTACK_S:g} à {MAX_DUCKING_ATTACK_S:g}).",
          required=False, minimum=MIN_DUCKING_ATTACK_S, maximum=MAX_DUCKING_ATTACK_S),
    Param("release_seconds", Kind.NUMBER, f"Relâchement en secondes ({MIN_DUCKING_RELEASE_S:g} à {MAX_DUCKING_RELEASE_S:g}).",
          required=False, minimum=MIN_DUCKING_RELEASE_S, maximum=MAX_DUCKING_RELEASE_S),
)
_CONFIG_NAMES = tuple(param.name for param in _CONFIG)


def _service() -> AudioAutomationService:
    return AudioAutomationService()


# --- Rôle -------------------------------------------------------------------------------------------------------------


def _set_track_role(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        return _service().set_track_role(project, track.id, a["role"])

    return operation


# --- Courbe de volume -------------------------------------------------------------------------------------------------


def _add_automation_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        require_free_instant(track, float(a["at"]))
        require_point_room(track, 1)
        return _service().add_automation_point(project, track.id, float(a["at"]), float(a["gain_db"]), float(a.get("fade_seconds", 0.0)))

    return operation


def _update_automation_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        if "gain_db" not in a and "fade_seconds" not in a:
            raise ValueError("update_automation_point : donnez le gain, le maintien, ou les deux.")
        instant = point_time(track, float(a["at"]))
        return _service().update_automation_point(
            project, track.id, instant,
            gain_db=None if "gain_db" not in a else float(a["gain_db"]),
            fade_seconds=None if "fade_seconds" not in a else float(a["fade_seconds"]),
        )

    return operation


def _move_automation_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        instant = point_time(track, float(a["at"]))
        target = float(a["to"])
        require_free_instant(track, target, ignoring=instant)
        current = next(point for point in track.automation.points if point.time_seconds == instant)
        gain = float(current.gain_db) if "gain_db" not in a else float(a["gain_db"])
        return _service().move_automation_point(project, track.id, instant, target, gain)

    return operation


def _remove_automation_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        return _service().remove_automation_point(project, track.id, point_time(track, float(a["at"])))

    return operation


def _clear_automation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        _service().clear_automation(project, track.id)
        return track

    return operation


def _set_automation_curve(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track = audio_track(project, a["track"])
        points = list(a["points"])
        if len(points) > MAX_AUTOMATION_POINTS:                      # le schéma le borne déjà ; la garde vaut aussi pour un appel direct
            raise ValueError(f"Une courbe compte au plus {MAX_AUTOMATION_POINTS} points ({len(points)} donnés).")
        for before, after in zip(points, points[1:]):
            if float(after["time_seconds"]) <= float(before["time_seconds"]) + _TOLERANCE:
                raise ValueError("Les points de la courbe doivent être à des instants strictement croissants (aucun doublon).")
        service = _service()
        service.clear_automation(project, track.id)                  # l'ancienne courbe part en entier ; la session restaure si un point est refusé
        for point in points:
            service.add_automation_point(
                project, track.id, float(point["time_seconds"]), float(point["gain_db"]), float(point.get("fade_seconds", 0.0)),
            )
        return track

    return operation


# --- Ducking ----------------------------------------------------------------------------------------------------------


def _config_from(base: DuckingConfig, a: Mapping[str, Any]) -> DuckingConfig:
    """``base`` avec les champs donnés (le modèle valide les bornes : rien n'est écrêté)."""
    return replace(base, **{name: float(a[name]) for name in _CONFIG_NAMES if name in a})


def _add_ducking_sidechain(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        music, voice = audio_track(project, a["music"]), audio_track(project, a["voice"])    # comme le menu : deux pistes audio déverrouillées
        if music.id == voice.id:
            raise ValueError("Une piste ne peut pas se baisser elle-même.")
        require_no_ducking_loop(project, music.id, voice.id)
        require_sidechain_room(project, 1)
        try:
            identifier = resolve_object_id(a.get("id"), prefix="duck", kind="ducking", taken=sidechain_ids(project))
        except InvalidObjectId as error:
            raise ValueError(str(error)) from None
        base = DUCKING_PRESETS[a["preset"]] if a.get("preset") is not None else DuckingConfig()    # le réglage du menu : celui du modèle
        return _service().add_ducking_sidechain(project, music.id, voice.id, _config_from(base, a), sidechain_id=identifier)

    return operation


def _sidechain(project: Project, selector: str) -> Any:
    """L'association désignée (séquence active), après avoir vérifié que la piste « musique » (la baissée) n'est pas verrouillée."""
    item = _pick(project.ducking_sidechains, selector, "Ducking", names=False)
    music = next((track for track in project.tracks if track.id == item.music_track_id), None)
    if music is not None:                                       # une association orpheline (piste supprimée) reste modifiable
        _require_unlocked(music)
    return item


def _remove_ducking_sidechain(a: Mapping[str, Any]) -> Operation:
    return lambda p: _service().remove_ducking_sidechain(p, _sidechain(p, a["ducking"]).id)


def _set_ducking_enabled(a: Mapping[str, Any]) -> Operation:
    return lambda p: _service().set_ducking_enabled(p, _sidechain(p, a["ducking"]).id, bool(a["enabled"]))


def _update_ducking_config(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        if not any(name in a for name in _CONFIG_NAMES):
            raise ValueError("update_ducking_config : donnez au moins un réglage.")
        item = _sidechain(project, a["ducking"])
        current = item.config if isinstance(item.config, DuckingConfig) else DuckingConfig()
        return _service().update_ducking_config(project, item.id, _config_from(current, a))

    return operation


# --- Ordre des inserts ------------------------------------------------------------------------------------------------


def _checked_delta(a: Mapping[str, Any], chain: list[Any], effect_id: str, verb: str) -> int:
    delta = int(a["delta"])
    target = next(index for index, item in enumerate(chain) if item.id == effect_id) + delta
    if delta == 0 or not 0 <= target < len(chain):
        raise ValueError(f"{verb} : déplacement impossible (la chaîne compte {len(chain)} insert(s)) ; le modèle le borne, un script le refuse.")
    return delta


def _move_track_insert(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        track_id, effect_id = _track_insert(project, a["track"], a["insert"])
        owner = next(item for item in project.tracks if item.id == track_id)
        if owner.type not in ("audio", "video"):
            raise ValueError(f"La piste '{track_id}' est une piste '{owner.type}' : seules les pistes audio et vidéo portent des inserts.")
        _require_unlocked(owner)
        return move_track_insert(project, track_id, effect_id, _checked_delta(a, owner.inserts, effect_id, "move_track_insert"))

    return operation


def _move_bus_insert(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        bus_id = _bus_id(project, a["bus"])
        effect_id = _insert_id(project, bus_id, a["insert"])
        bus = next(item for item in project.buses if item.id == bus_id)
        return move_bus_insert(project, bus_id, effect_id, _checked_delta(a, bus.inserts, effect_id, "move_bus_insert"))

    return operation


AUDIO_VERBS: tuple[Verb, ...] = (
    Verb("set_track_role", "Donne un rôle à une piste audio déverrouillée (voix, musique, effets sonores ou autre) ; les rôles servent à « duck_music_under_voice ».",
         (_TRACK, Param("role", Kind.ENUM, "Le rôle.", choices=_ROLES)), _set_track_role, group="audio"),
    Verb("add_automation_point", "Pose un point sur la courbe de volume d'une piste audio ; un point existe déjà à cet instant : refusé (utilisez « update_automation_point »).",
         (_TRACK, _AT, _GAIN, _FADE),
         _add_automation_point, group="audio"),
    Verb("update_automation_point", "Modifie le gain ou le maintien du point de la courbe situé à cet instant (au moins l'un des deux).",
         (_TRACK, Param("at", Kind.NUMBER, "Instant du point (tel que « describe » le donne), en secondes.", minimum=0.0, maximum=MAX_TIME),
          replace(_GAIN, required=False), _FADE),
         _update_automation_point, group="audio"),
    Verb("move_automation_point", "Déplace le point de la courbe situé à « at » vers l'instant « to » (et, si donné, un autre gain) ; un instant déjà pris est refusé.",
         (_TRACK, Param("at", Kind.NUMBER, "Instant actuel du point, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("to", Kind.NUMBER, "Nouvel instant, en secondes.", minimum=0.0, maximum=MAX_TIME),
          Param("gain_db", Kind.NUMBER, "Nouveau gain en dB (absent : le gain actuel).", required=False, minimum=_GAIN.minimum, maximum=_GAIN.maximum)),
         _move_automation_point, group="audio"),
    Verb("remove_automation_point", "Retire le point de la courbe de volume situé à cet instant.",
         (_TRACK, Param("at", Kind.NUMBER, "Instant du point, en secondes.", minimum=0.0, maximum=MAX_TIME)), _remove_automation_point, group="audio"),
    Verb("clear_automation", "Efface la courbe de volume d'une piste audio (la piste revient à son volume seul).", (_TRACK,), _clear_automation, group="audio"),
    Verb("set_automation_curve", f"Remplace toute la courbe de volume d'une piste audio par ces points (0 à {MAX_AUTOMATION_POINTS}, instants strictement croissants).",
         (_TRACK, Param("points", Kind.LIST, f"Les points, du premier au dernier instant ({MAX_AUTOMATION_POINTS} au plus).", minimum=0,
                        maximum=MAX_AUTOMATION_POINTS,
                        fields=(Param("time_seconds", Kind.NUMBER, "Instant sur la timeline, en secondes.", minimum=0.0, maximum=MAX_TIME),
                                Param("gain_db", Kind.NUMBER, _GAIN.doc, minimum=_GAIN.minimum, maximum=_GAIN.maximum),
                                _FADE))),
         _set_automation_curve, group="audio"),
    Verb("add_ducking_sidechain", "La piste « music » baisse sous la piste « voice » (deux pistes audio déverrouillées, sans boucle, une seule association par paire) ; "
                                  "réglage : un preset et/ou des valeurs, sinon le réglage par défaut du modèle.",
         (_MUSIC, _VOICE, Param("preset", Kind.ENUM, "Réglage de départ (défaut : celui du modèle).", required=False, choices=tuple(DUCKING_PRESETS)), *_CONFIG),
         _add_ducking_sidechain, creates="ducking", group="audio"),
    Verb("remove_ducking_sidechain", "Retire une association de ducking ; refusé si la piste baissée est verrouillée.", (_DUCKING,), _remove_ducking_sidechain,
         group="audio"),
    Verb("set_ducking_enabled", "Active ou désactive une association de ducking sans la retirer ; refusé si la piste baissée est verrouillée.",
         (_DUCKING, Param("enabled", Kind.BOOLEAN, "Active ou non.")), _set_ducking_enabled, group="audio"),
    Verb("update_ducking_config", "Modifie le réglage d'une association de ducking (au moins une valeur ; les autres restent) ; refusé si la piste baissée est verrouillée.",
         (_DUCKING, *_CONFIG), _update_ducking_config, group="audio"),
    Verb("move_track_insert", "Déplace un insert dans la chaîne d'une piste (-1 : monter, 1 : descendre) ; refusé s'il sortirait de la chaîne.",
         (_TRACK, _TRACK_INSERT, _DELTA), _move_track_insert, group="audio"),
    Verb("move_bus_insert", "Déplace un insert dans la chaîne d'un bus (-1 : monter, 1 : descendre) ; refusé s'il sortirait de la chaîne.",
         (_BUS, _INSERT, _DELTA), _move_bus_insert, group="audio"),
)

__all__ = ["AUDIO_VERBS"]

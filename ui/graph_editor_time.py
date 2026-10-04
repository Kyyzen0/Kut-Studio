"""Le Graph Editor devant une courbe de vitesse : chaque édition passe par les opérations transactionnelles du temps.

Un point de vitesse est un keyframe comme un autre, mais le modifier change la **durée** du clip (la source est épuisée plus tôt ou
plus tard). Les mutateurs génériques de :mod:`core.keyframe_editing` ne le savent pas : ils laisseraient un clip de quelques
millisecondes, ne recoupleraient pas les keyframes d'après la nouvelle fin et ignoreraient la politique de ripple de l'utilisateur.
Pour ``time.speed``, l'éditeur appelle donc :mod:`core.time_ops` : durée minimale, ripple, découpe des keyframes, et retour à l'état
précédent si l'édition est refusée (le refus est dit dans la barre d'état ; l'éditeur relit l'état réel).

**Un glissement est idempotent.** À la première étape d'un geste, l'état du clip est mémorisé ; chaque étape suivante le restaure
puis applique le déplacement *total*. Sinon un clip raccourci en cours de geste perdrait pour de bon les keyframes découpés d'après
sa fin, même si le geste le rallonge ensuite. Le geste reste une seule entrée d'historique (:meth:`finish_gesture`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.animation import TangentMode
from core.keyframe_editing import KeyframeRef
from core.time_map import SPEED_PROPERTY
from core.time_ops import (
    RippleMode,
    TimeState,
    add_speed_point,
    capture_time_state,
    edit_speed_points,
    set_speed_point_interpolation,
    set_speed_tangents,
    speed_points,
)


class SpeedCurveEditing:
    """Mixin de ``GraphEditorWindow`` : les éditions de la propriété ``time.speed``."""

    host: Any
    clip_id: str | None
    property_id: str | None
    _gesture_state: dict[str, TimeState] | None

    def _is_speed(self) -> bool:
        return self.property_id == SPEED_PROPERTY

    def _ripple(self) -> RippleMode:
        """La politique de ripple choisie par l'utilisateur (conserver la plage source, ou la durée sur la timeline)."""
        policy = getattr(self.host, "_ripple_mode", None)
        return policy() if callable(policy) else RippleMode.SOURCE

    def _gesture_origin(self) -> dict[str, TimeState]:
        """État du clip au début du geste en cours (pris à la première étape)."""
        if self._gesture_state is None:
            clip = self._clip()  # type: ignore[attr-defined]
            self._gesture_state = {} if clip is None else {clip.id: capture_time_state(clip)}
        return self._gesture_state

    def _attempt_speed(self, operation: Callable[[], object]) -> bool:
        """Exécute une édition de vitesse. Refusée (durée dégénérée, piste verrouillée…), elle est dite à l'utilisateur et l'éditeur
        relit l'état réel du clip : ``False``, rien n'a changé."""
        try:
            operation()
        except (KeyError, ValueError) as error:
            self.host._report_edit_refused(error)
            self.host.on_graph_edit("", self.clip_id, record=False)
            return False
        return True

    def _speed_ref(self, keyframe_id: str) -> KeyframeRef:
        return KeyframeRef(str(self.clip_id), SPEED_PROPERTY, keyframe_id)

    # -- gestes continus (idempotents) ------------------------------------------------------------------

    def _speed_drag_points(self, origin: dict, delta: float, dv: float, fps: float) -> bool:
        """Glisse les points saisis de ``delta`` secondes et ``dv`` en valeur, **depuis le début du geste**."""
        values = {self._speed_ref(kid): value + dv for kid, value in origin["values"].items()}
        refs = list(values)
        done = self._attempt_speed(lambda: edit_speed_points(
            self.host.project, refs, delta_seconds=delta, values=values, fps=fps, mode=self._ripple(), origin=self._gesture_origin(),
        ))
        if done:
            self._reselect(refs)
        return done

    def _speed_drag_handle(self, ref: KeyframeRef, slopes: dict[str, float]) -> bool:
        done = self._attempt_speed(lambda: set_speed_tangents(
            self.host.project, [ref], ripple=self._ripple(), origin=self._gesture_origin(), **slopes,
        ))
        if done:
            self._reselect([ref])
        return done

    def _reselect(self, refs: list[KeyframeRef]) -> None:
        """Rétablit la sélection sur ceux des points saisis qui existent dans l'état courant : l'hôte élague la sélection à chaque
        étape, et un point absent à mi-course (clip raccourci) doit redevenir sélectionné quand le geste le ramène."""
        clip = self._clip()  # type: ignore[attr-defined]
        alive = {point.id for point in speed_points(clip)} if clip is not None else set()
        self.host.set_keyframe_selection({ref for ref in refs if ref.keyframe_id in alive})

    # -- éditions ponctuelles ------------------------------------------------------------------------

    def _speed_add_point(self, local_time: float) -> bool:
        return self._attempt_speed(lambda: add_speed_point(self.host.project, str(self.clip_id), local_time, mode=self._ripple()))

    def _speed_set_interpolation(self, refs: list[KeyframeRef], interpolation: Any) -> bool:
        changed: list[int] = []
        ok = self._attempt_speed(lambda: changed.append(set_speed_point_interpolation(
            self.host.project, str(self.clip_id), [ref.keyframe_id for ref in refs], interpolation, mode=self._ripple(),
        )))
        return ok and bool(changed and changed[0])

    def _speed_set_tangent_mode(self, refs: list[KeyframeRef], mode: str) -> bool:
        tangents: dict[str, Any] = {"auto": True} if mode == "auto" else {"mode": TangentMode(mode)}
        return self._attempt_speed(lambda: set_speed_tangents(self.host.project, refs, ripple=self._ripple(), **tangents))

    def _speed_move_by(self, refs: list[KeyframeRef], delta: float, fps: float) -> bool:
        return self._attempt_speed(lambda: edit_speed_points(
            self.host.project, refs, delta_seconds=delta, fps=fps, mode=self._ripple(),
        ))

    def _speed_set_value(self, ref: KeyframeRef, value: float) -> bool:
        return self._attempt_speed(lambda: edit_speed_points(self.host.project, [ref], values={ref: value}, mode=self._ripple()))

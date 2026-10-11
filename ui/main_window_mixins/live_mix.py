"""Mixage en direct dans la fenêtre : pendant la lecture, on entend le mixage de l'export (:mod:`core.live_mix`).

À chaque synchronisation du moniteur (chaque tic de lecture, saut, modification), :meth:`_sync_live_mix` compare ce
qui devrait jouer (lecture en cours, projet et sa révision d'historique, Master) à ce qui joue, et lance, arrête ou
relance le flux. Un geste continu (un fader qu'on glisse) ne relance le flux qu'une fois posé
(:data:`RESTART_SETTLE_SECONDS`) ; un écart entre le son et la tête de lecture (saut, gel de l'interface) le relance
aussi. Pendant ce temps, le lecteur du moniteur est muet : le mixage contient déjà son son, comme celui des sources
Multicam et des compositions. Si FFmpeg échoue, le moniteur retrouve son son et le flux n'est pas relancé tant que le
projet ne change pas.
"""

from __future__ import annotations

import logging
import os
import time

LOGGER = logging.getLogger(__name__)

RESTART_SETTLE_SECONDS = 0.2
"""Un projet modifié pendant la lecture relance le flux une fois resté tel quel aussi longtemps."""
MAX_DRIFT_SECONDS = 0.25
"""Au-delà, le son et la tête de lecture ne disent plus la même chose : le flux repart de la tête."""
DRIFT_RESTARTS_LIMIT = 3
DRIFT_RESTARTS_WINDOW = 5.0
"""Autant de recalages en si peu de temps : FFmpeg ne tient pas le temps réel (effets trop lourds), on rend la main au
moniteur plutôt que d'enchaîner les relances et les silences."""


class LiveMixMixin:
    def _init_live_mix(self) -> None:
        self._live_mix = None
        self._live_mix_key = None                           # état du projet que le flux en cours fait entendre
        self._live_mix_pending: tuple | None = None         # (état voulu, depuis quand) : relance en attente
        self._live_mix_failed_key = None
        self._live_mix_drift_restarts: list[float] = []

    def _live_mix_enabled(self) -> bool:
        """Le mixage en direct peut-il jouer ? ``KUT_STUDIO_LIVE_MIX=off`` (tests, audit d'interface) ou aucune sortie
        audio : non, le moniteur garde son propre son. La sortie est cherchée une fois par session."""
        if os.environ.get("KUT_STUDIO_LIVE_MIX", "").strip().lower() in {"off", "0", "false", "no"}:
            return False
        available = getattr(self, "_live_mix_output", None)
        if available is None:
            from ui.live_mix_player import output_available

            available = self._live_mix_output = output_available()
        return bool(available)

    def _live_mix_wanted_key(self) -> tuple:
        history = getattr(self, "history", None)
        return (id(self.project), self.project.active_sequence_id, getattr(history, "revision", 0),
                float(self._master_gain_db), bool(self._master_muted))

    @property
    def live_mix_active(self) -> bool:
        player = getattr(self, "_live_mix", None)
        return player is not None and player.active

    def _sync_live_mix(self) -> None:
        """Lance, arrête ou relance le flux selon l'état de la lecture et du projet. Jamais d'exception.

        Un échec **synchrone** (plan ou commande impossibles à construire, lecteur qui ne démarre pas) est retenu pour
        l'état du projet qui l'a causé, comme un échec du flux (:meth:`_on_live_mix_failed`) : sans cela, chaque tic de
        lecture (25 par seconde) reconstruirait le plan et écrirait une trace, jusqu'à rendre la lecture inutilisable."""
        try:
            self._drive_live_mix()
        except Exception:
            LOGGER.warning("Mixage en direct : le moniteur garde son propre son", exc_info=True)
            self._live_mix_failed_key = self._live_mix_wanted_key()
            self._stop_live_mix()

    def _drive_live_mix(self) -> None:
        if not bool(getattr(self, "is_playing", False)) or not self._live_mix_enabled():
            self._stop_live_mix()
            return
        key = self._live_mix_wanted_key()
        if not self.live_mix_active:
            if key != self._live_mix_failed_key:
                self._start_live_mix(key)
            return
        if key != self._live_mix_key:
            pending = self._live_mix_pending
            now = time.monotonic()
            if pending is None or pending[0] != key:
                self._live_mix_pending = (key, now)
            elif now - pending[1] >= RESTART_SETTLE_SECONDS:
                self._start_live_mix(key)
            return
        heard = self._live_mix.heard_time
        if heard is not None and abs(heard - float(self.playhead_seconds)) > MAX_DRIFT_SECONDS:
            now = time.monotonic()
            recent = [moment for moment in self._live_mix_drift_restarts if now - moment < DRIFT_RESTARTS_WINDOW]
            self._live_mix_drift_restarts = [*recent, now]
            if len(self._live_mix_drift_restarts) >= DRIFT_RESTARTS_LIMIT:
                self._on_live_mix_failed("le mixage ne tient pas le temps réel")  # i18n-ignore: journal seulement
                return
            self._start_live_mix(key)

    def _start_live_mix(self, key) -> None:
        from core.live_mix import build_live_mix_command

        self._live_mix_pending = None
        live = build_live_mix_command(
            self.project, float(self.playhead_seconds), master_gain_db=float(self._master_gain_db),
            master_muted=bool(self._master_muted), resolver=self._preview_resolver(),
            timeline_index=self._ensure_timeline_index(),
        )
        if live is None:                                    # plus rien à jouer
            self._stop_live_mix()
            return
        player = self._live_mix
        if player is None:
            from ui.live_mix_player import LiveMixPlayer

            player = self._live_mix = LiveMixPlayer(self)
            player.failed.connect(self._on_live_mix_failed)
            player.set_volume(self.preview_panel.audio_output.volume())
        self._live_mix_key = key                            # avant ``start`` : un échec signalé connaît son état
        player.start(live, clock=lambda: float(self.playhead_seconds))
        self.preview_panel.set_audio_replaced(player.active)

    def _stop_live_mix(self) -> None:
        player = getattr(self, "_live_mix", None)
        if player is not None and player.active:
            player.stop()
        self._live_mix_key = None
        self._live_mix_pending = None
        panel = getattr(self, "preview_panel", None)
        if panel is not None:
            panel.set_audio_replaced(False)

    def _on_live_mix_failed(self, message: str) -> None:
        """FFmpeg n'a pas fourni le flux : le moniteur retrouve son son ; pas de nouvel essai pour ce même état."""
        self._live_mix_failed_key = self._live_mix_key
        self._stop_live_mix()
        LOGGER.warning("Mixage en direct indisponible (%s) : le moniteur joue le son du clip affiché", message)

    def _live_mix_levels(self):
        """Pour les vumètres du mixeur : ``(relevé, pistes)`` du son qu'on entend, ou ``None`` (rien ne joue)."""
        player = getattr(self, "_live_mix", None)
        if player is None or not player.active:
            return None
        reading = player.meter()
        return (reading, player.track_ids) if reading is not None else None

    def _set_live_mix_volume(self, volume: float) -> None:
        player = getattr(self, "_live_mix", None)
        if player is not None:
            player.set_volume(volume)

    def _release_live_mix(self) -> None:
        self._stop_live_mix()

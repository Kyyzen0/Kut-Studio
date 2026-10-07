"""Méthodes de ``MainWindow`` regroupées : scopes."""

from __future__ import annotations

import logging
import os
import tempfile

from PySide6.QtCore import QObject, Signal

from core.scopes_analyzer import (
    ScopeAnalysis,
    ScopeExtractionError,
    cleanup_temporary_paths,
)


LOGGER = logging.getLogger(__name__)


class _ScopeEvents(QObject):
    """Pont thread d'analyse → thread Qt (connexion en file automatique)."""

    ready = Signal(object)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class ScopesMixin:
    """Mixin de ``MainWindow`` (scopes)."""

    def _on_scopes_analysis_ready(self, analysis: ScopeAnalysis) -> None:
        """Reçoit un résultat d'analyse **depuis le thread de travail**.

        On ne fait que programmer la mise à jour du panneau Qt : le
        repaint et le calcul des courbes doivent rester dans le thread
        GUI. Un résultat marqué ``stale`` (la tête de lecture a bougé
        pendant l'analyse) est ignoré pour ne pas faire clignoter les
        scopes avec une image obsolète.
        """
        if getattr(analysis, "stale", False):
            return
        # Appelé depuis le thread d'analyse : un signal d'un QObject du thread Qt est mis en file et
        # livré dans la boucle principale. ``QTimer.singleShot`` posté depuis un thread sans boucle
        # d'événements n'exécutait jamais son rappel : les scopes ne s'affichaient jamais.
        self._scope_events.ready.emit(analysis.result)

    def _on_scopes_analysis_failed(
        self, request, error: BaseException,
    ) -> None:
        """Trace une erreur d'analyse sans interrompre la lecture."""
        # Une erreur d'extraction est banale quand aucune image n'est
        # compositionnée (tête de lecture hors media) : on reste
        # silencieux, sinon on sature la console pendant la lecture.
        if isinstance(error, ScopeExtractionError):
            return
        LOGGER.warning("Analyse des scopes échouée : %r", error)

    def _request_scopes_analysis(self, force: bool = False) -> None:
        """Déclenche une analyse de l'image composée à la tête de lecture.

        Pendant la lecture, la fréquence est bornée par
        :data:`SCOPES_MIN_INTERVAL` ; en pause (ou avec
        ``force=True``), l'analyse est immédiate pour que les scopes
        réagissent tout de suite à un changement d'exposition, de
        contraste, de courbes ou de LUT.
        """
        if not self._scopes_visible:
            return
        if self.project is None:
            return
        playhead = float(getattr(self, "playhead_seconds", 0.0))
        # On ignore les requêtes quasi identiques : inutile de
        # réanalyser la même image 10 fois par seconde.
        if not force and abs(playhead - self._last_scopes_playhead) < 1e-3:
            return
        self._last_scopes_playhead = playhead
        self._scope_temporary_paths = ()
        command = self._build_scopes_ffmpeg_command(playhead)
        if command is None:
            return
        temporary_paths = self._scope_temporary_paths
        self._scope_temporary_paths = ()
        accepted = self.scopes_analyzer.submit(
            playhead=playhead,
            ffmpeg_command=command,
            color_space=self.scopes_panel._color_space,
            levels=self.scopes_panel.levels(),
            columns=_main_window().SCOPES_COLUMNS,
            vectorscope_bins=_main_window().SCOPES_VECTORSCOPE_BINS,
            source="timeline",
            temporary_paths=temporary_paths,
            force=force,
        )
        if accepted is None:
            cleanup_temporary_paths(temporary_paths)

    def _build_scopes_ffmpeg_command(
        self, playhead: float,
    ) -> list[str] | None:
        """Construit la commande ``ffmpeg`` qui rend **une** frame composée.

        On réutilise le graphe de filtres de l'export (effets,
        étalonnage, LUT, courbes) via
        :meth:`core.export_engine.ExportEngine.build_frame_command`, afin
        que les scopes reflètent exactement ce que le moniteur affiche.
        La commande est volontairement limitée à une seule image PNG
        sur stdout : l'analyse doit rester peu coûteuse, y compris
        pendant la lecture.
        """
        try:
            # Le plan est ramené à l'origine à la tête de lecture : l'image voulue est la première du
            # rendu (voir ``build_frame_command``), quel que soit l'endroit de la timeline.
            render_plan = self.get_render_plan(at=playhead)
        except Exception:
            # Projet sans média, plan incomplet : rien à analyser.
            LOGGER.debug("Plan à la tête de lecture non construit : scopes non analysés", exc_info=True)
            return None
        if not getattr(render_plan, "video_layers", ()):
            return None
        frame_engine: _main_window().ExportEngine | None = None
        try:
            # Le chemin de sortie n'est jamais écrit (la sortie est un
            # PNG sur stdout) mais ``ExportRequest`` en exige un : on
            # pointe donc vers un dossier toujours présent.
            request = self.export_panel.build_request(
                render_plan, os.path.join(tempfile.gettempdir(), "kut-frame.png")
            )
            # Une instance dédiée évite qu'une analyse de scopes ne remplace
            # le SRT temporaire d'un export déjà en cours.
            frame_engine = _main_window().ExportEngine()
            frame_engine.flow_preference = self._flow_preference()
            # Construite sur le fil de l'interface, à chaque déplacement de la tête de lecture : un clip interpolé y est lu en
            # échantillonnage, comme dans le moniteur (voir ``build_frame_command``).
            command = frame_engine.build_frame_command(request, 0.0)
            self._scope_temporary_paths = frame_engine.take_temporary_files()
            return command
        except Exception:
            if frame_engine is not None:
                cleanup_temporary_paths(frame_engine.take_temporary_files())
            cleanup_temporary_paths(self._scope_temporary_paths)
            self._scope_temporary_paths = ()
            return None

    def _persist_scopes_preferences(self) -> None:
        """Enregistre la disposition / les niveaux des scopes.

        ``UserSettings`` est immuable : on reconstruit un instantané
        complet via :meth:`_settings_snapshot` plutôt que de muter
        l'instance chargée (ce qui lèverait une ``FrozenInstanceError``).
        """
        try:
            _main_window().save_user_settings(self._settings_snapshot())
        except OSError:
            # Un échec d'écriture des préférences ne doit pas
            # interrompre l'édition.
            pass

    def toggle_scopes_visible(self) -> None:
        """Affiche / masque le panneau de scopes."""
        self._scopes_visible = not self._scopes_visible
        self.scopes_panel.setVisible(self._scopes_visible)
        # L'action de menu reflète toujours l'état réel du splitter.
        action = getattr(self, "scopes_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(self._scopes_visible)
            action.blockSignals(False)
        # Le redimensionnement suit la visibilité : replié quand masqué,
        # il reprend sa hauteur quand affiché.
        host = getattr(self, "_viewer_host", None)
        if host is not None:
            if self._scopes_visible:
                host.setSizes([420, 260])
            else:
                host.setSizes([680, 0])
        self._persist_scopes_preferences()
        if self._scopes_visible:
            # On analyse immédiatement : l'utilisateur veut voir les
            # scopes tout de suite.
            self._request_scopes_analysis(force=True)

"""Méthodes de ``MainWindow`` regroupées : scopes."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from core.scopes import scope_frame_size

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
        # La limite de fréquence est vérifiée avant de fabriquer la commande, pas après : pendant la lecture, une
        # demande refusée ne coûte plus rien au fil de l'interface.
        if not self.scopes_analyzer.would_accept(force=force):
            return
        self._last_scopes_playhead = playhead
        # Sur ce fil, seulement ce qui lit l'état de l'application (plan à la tête de lecture, réglages d'export) :
        # quelques millisecondes. La commande elle-même (graphe, images des calques) est fabriquée par le thread de
        # l'analyseur ; construite ici, elle gelait l'interface jusqu'à plusieurs secondes par tic de lecture.
        factory = self._prepare_scopes_command(playhead)
        if factory is None:
            return
        self.scopes_analyzer.submit(
            playhead=playhead,
            command_factory=factory,
            color_space=self.scopes_panel._color_space,
            levels=self.scopes_panel.levels(),
            columns=_main_window().SCOPES_COLUMNS,
            vectorscope_bins=_main_window().SCOPES_VECTORSCOPE_BINS,
            source="timeline",
            force=force,
        )

    def _prepare_scopes_command(self, playhead: float):
        """Partie « fil de l'interface » de l'analyse : fabrique ``() -> (commande, fichiers temporaires)``, ou ``None``.

        Le plan à la tête de lecture et la requête d'export lisent l'état de l'application : ils sont pris ici, en
        quelques millisecondes. La fabrique, elle, peut tourner sur n'importe quel thread : elle ne touche plus qu'à
        ces objets figés et à son propre moteur.
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
        try:
            # Le chemin de sortie n'est jamais écrit (la sortie est un
            # PNG sur stdout) mais ``ExportRequest`` en exige un : on
            # pointe donc vers un dossier toujours présent.
            request = self.export_panel.build_request(
                render_plan, os.path.join(tempfile.gettempdir(), "kut-frame.png")
            )
            # L'analyse n'échantillonne que ~10⁵ pixels : l'image est composée à cette taille, pas en pleine
            # définition (même graphe, effets en pixels mis à l'échelle comme dans un aperçu réduit).
            reduced = scope_frame_size(*request.preset.resolution)
            request = replace(request, preset=replace(request.preset, resolution=reduced))
            # Une instance dédiée évite qu'une analyse de scopes ne remplace
            # le SRT temporaire d'un export déjà en cours.
            frame_engine = _main_window().ExportEngine()
            frame_engine.flow_preference = self._flow_preference()
        except Exception:
            LOGGER.debug("Requête d'image des scopes non construite : scopes non analysés", exc_info=True)
            return None

        def build() -> tuple[list[str], tuple[str, ...]]:
            try:
                # Un clip interpolé y est lu en échantillonnage, comme dans le moniteur (voir ``build_frame_command``).
                command = frame_engine.build_frame_command(request, 0.0)
            except Exception:
                cleanup_temporary_paths(frame_engine.take_temporary_files())
                raise
            return command, tuple(frame_engine.take_temporary_files())

        return build

    def _build_scopes_ffmpeg_command(
        self, playhead: float,
    ) -> list[str] | None:
        """Construit tout de suite la commande ``ffmpeg`` qui rend **une** frame composée (bloquant).

        On réutilise le graphe de filtres de l'export (effets,
        étalonnage, LUT, courbes) via
        :meth:`core.export_engine.ExportEngine.build_frame_command`, afin
        que les scopes reflètent exactement ce que le moniteur affiche.
        L'analyse des scopes passe par :meth:`_prepare_scopes_command`, qui
        construit la même commande hors du fil de l'interface. Les
        fichiers temporaires de la commande sont rangés dans
        ``_scope_temporary_paths``.
        """
        factory = self._prepare_scopes_command(playhead)
        if factory is None:
            return None
        try:
            command, temporary = factory()
        except Exception:
            LOGGER.debug("Commande d'image des scopes non construite", exc_info=True)
            return None
        self._scope_temporary_paths = temporary
        return command

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

    def toggle_scopes_visible(self, *, persist: bool = True) -> None:
        """Affiche / masque le panneau de scopes (``persist=False`` : le temps d'une page, sans changer la préférence)."""
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
        if persist:
            self._persist_scopes_preferences()
        if self._scopes_visible:
            # On analyse immédiatement : l'utilisateur veut voir les
            # scopes tout de suite.
            self._request_scopes_analysis(force=True)

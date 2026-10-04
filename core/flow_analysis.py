"""Pré-calcul « Analyser le flux optique » : un travail d'arrière-plan annulable, avec progression.

L'export et l'aperçu fidèle calculent seuls les vecteurs de mouvement dont ils ont besoin ; analyser à l'avance les range dans
le cache (:mod:`core.flow_cache`) pour que ces rendus les relisent. Le mouvement est celui du média, pas du clip : l'analyse reste
valable si on déplace le clip, change sa vitesse ou sa courbe (rien n'est invalidé tant que le média, le cadrage et le moteur sont
les mêmes).

Un :class:`FlowAnalysisJob` tourne dans un fil et se laisse **interroger** (:meth:`snapshot`) : l'interface le lit par une minuterie,
comme le suivi de mouvement. :meth:`cancel` l'arrête à la paire suivante ; les paires déjà calculées sont conservées.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from .flow_cache import FlowCache
from .retime_prepare import PrepareCancelled, PrepareReport, PrepareRequest, analyze_pairs
from .time_remapping import TimeInterpolation


class AnalysisState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class AnalysisSnapshot:
    """L'état du travail à un instant."""

    state: AnalysisState
    done: int = 0
    total: int = 0
    message: str = ""
    """Cause de l'échec (état ``FAILED``)."""
    report: PrepareReport | None = None
    """Bilan (état ``DONE``) : paires calculées, paires relues du cache."""

    @property
    def finished(self) -> bool:
        return self.state in (AnalysisState.DONE, AnalysisState.CANCELLED, AnalysisState.FAILED)

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0


def _pair_count(request: PrepareRequest) -> int:
    """Paires à estimer pour ce clip : celles du plan en flux optique, aucune pour le mélange ou l'échantillonnage."""
    return len(request.plan().pairs()) if request.interpolation is TimeInterpolation.OPTICAL_FLOW else 0


@dataclass
class FlowAnalysisJob:
    """Analyse les paires de ``requests`` (un clip chacune), l'une après l'autre, dans un fil."""

    requests: list[PrepareRequest]
    cache: FlowCache
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _state: AnalysisState = AnalysisState.PENDING
    _done: int = 0
    _total: int = 0
    _message: str = ""
    _report: PrepareReport | None = None

    def start(self) -> None:
        """Lance le travail ; sans effet s'il a déjà démarré."""
        with self._lock:
            if self._thread is not None:
                return
            self._state = AnalysisState.RUNNING
            self._thread = threading.Thread(target=self._run, name="kut-flow-analysis", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Demande l'arrêt : le fil s'interrompt à la paire suivante (les paires déjà rangées restent valables)."""
        self._cancel.set()

    def join(self, timeout: float | None = None) -> bool:
        """Attend la fin ; ``False`` si le délai est dépassé."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def snapshot(self) -> AnalysisSnapshot:
        with self._lock:
            return AnalysisSnapshot(self._state, self._done, self._total, self._message, self._report)

    def _run(self) -> None:
        total = sum(_pair_count(request) for request in self.requests)
        with self._lock:
            self._total = total
        merged = PrepareReport()
        offset = 0
        try:
            for request in self.requests:
                if self._cancel.is_set():
                    raise PrepareCancelled

                def relay(done: int, _count: int, base: int = offset) -> None:
                    with self._lock:
                        self._done = base + done

                part = analyze_pairs(request, self.cache, progress=relay, cancelled=self._cancel.is_set)
                offset += _pair_count(request)
                merged.backend = part.backend or merged.backend
                merged.images += part.images
                merged.pairs_computed += part.pairs_computed
                merged.pairs_cached += part.pairs_cached
                merged.seconds += part.seconds
        except PrepareCancelled:
            self._finish(AnalysisState.CANCELLED)
            return
        except BaseException as error:  # noqa: BLE001 - tout échec est rapporté à l'appelant par l'état, jamais perdu dans le fil
            self._finish(AnalysisState.FAILED, message=str(error) or type(error).__name__)
            return
        self._finish(AnalysisState.DONE, report=merged)

    def _finish(self, state: AnalysisState, *, message: str = "", report: PrepareReport | None = None) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._report = report
            if state is AnalysisState.DONE:
                self._done = self._total


__all__ = ["AnalysisSnapshot", "AnalysisState", "FlowAnalysisJob"]

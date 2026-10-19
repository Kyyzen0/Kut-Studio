"""La session d'édition d'un script (ADR-0005, étape 2) : appliquer des opérations sur un projet **en tout ou rien**, comme
**une seule** étape d'historique.

Une opération est une fonction ``f(project) -> valeur`` : le plus souvent un appel d'une fonction de ``core/`` (``add_clip_to_track``,
``route_track``…) avec ses arguments. La session enchaîne les opérations sur le projet réel, et :

* **tout ou rien** : si l'une échoue, le projet est **restauré** à l'état d'avant (un instantané copié en profondeur, remis *en
  place* : l'objet ``Project`` reste le même pour ceux qui le référencent) et le rapport dit laquelle (index, nom, message) ;
* **le même recadrage que l'interface** : après les opérations, ``core.sequences.clamp_nested_clips`` ramène les clips imbriqués qui
  dépassent une source raccourcie, dans la même transaction. C'est ce que fait ``_record_history`` de l'interface avant
  d'enregistrer ; ce que l'interface fait d'autre autour d'une édition (clore une saisie, invalider des aperçus) ne change pas le projet
  et n'a pas d'équivalent ici ;
* **une étape d'historique** : une transaction réussie s'enregistre en une entrée de :class:`~core.edit_history.ProjectHistory`
  (annulable d'un coup, comme un geste de l'interface) ; une transaction échouée n'en laisse aucune.

Les erreurs attendues du modèle (``ValueError`` et ses filles, ``KeyError``, ``OSError``…) sont **rendues dans le rapport**. Une erreur
de programmation (``TypeError``, ``AttributeError``…) restaure le projet puis **se propage** : un bogue ne doit pas passer pour un
« échec d'opération ».
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from ..edit_history import ProjectHistory
from ..project_model import Project
from ..sequences import ClampAdjustment, clamp_nested_clips

LOGGER = logging.getLogger("kut_studio.scripting")

Operation = Callable[[Project], Any]
"""Une opération : reçoit le projet, le modifie, rend ce qu'elle a créé ou trouvé (le clip ajouté, le bus…)."""

# Ce que les fonctions de ``core/`` lèvent **volontairement** pour refuser une opération : valeur hors bornes, verrou, identifiant refusé
# (``ValueError`` et ses filles), cible introuvable (``KeyError``), média absent (``OSError``). Pas leurs classes mères (``LookupError``,
# ``ArithmeticError``) : un ``IndexError`` ou un ``ZeroDivisionError`` est un bogue, il doit se propager (aucune fonction de ``core/``
# n'en lève pour refuser quoi que ce soit).
_REFUSALS = (ValueError, KeyError, OSError)


class SessionBusyError(RuntimeError):
    """Une transaction est déjà ouverte : elles ne s'imbriquent pas (une seule étape d'historique par transaction)."""


@dataclass(frozen=True)
class OperationResult:
    """Ce qu'une opération réussie a rendu."""

    index: int
    name: str
    value: Any = None


@dataclass(frozen=True)
class ApplyReport:
    """Le bilan d'une transaction.

    Attributes:
        ok: la transaction a été validée.
        label: son libellé d'historique.
        results: un résultat par opération réussie, dans l'ordre (toutes si ``ok``, celles d'avant l'échec sinon : le projet est
            alors restauré, ces valeurs ne désignent plus rien).
        failed_index / failed_name: la première opération refusée (``None`` si ``ok``).
        error / error_type: son message et le nom de son type.
        adjustments: clips imbriqués recadrés par la transaction (comme l'interface les annonce).
    """

    ok: bool
    label: str
    results: tuple[OperationResult, ...] = ()
    failed_index: int | None = None
    failed_name: str = ""
    error: str = ""
    error_type: str = ""
    adjustments: tuple[ClampAdjustment, ...] = ()

    @property
    def values(self) -> tuple[Any, ...]:
        """Les valeurs rendues par les opérations, dans l'ordre."""
        return tuple(result.value for result in self.results)


def _name_of(operation: Operation) -> str:
    name = getattr(operation, "__name__", "") or getattr(getattr(operation, "func", None), "__name__", "")
    return name if name and name != "<lambda>" else "operation"


def _plan(operations: Iterable[Operation | tuple[str, Operation]]) -> list[tuple[str, Operation]]:
    """Les opérations nommées, dans l'ordre ; ``TypeError`` pour un élément qui n'est pas appelable (avant tout effet)."""
    planned: list[tuple[str, Operation]] = []
    for item in operations:
        name, operation = item if isinstance(item, tuple) else (_name_of(item), item)
        if not callable(operation):
            raise TypeError(f"Opération {len(planned)} : un objet appelable est attendu, pas {type(operation).__name__}.")
        planned.append((name, operation))
    return planned


def _restore(project: Project, snapshot: Project) -> None:
    """Remet ``project`` dans l'état de ``snapshot`` **sans changer son identité** (ceux qui le référencent le voient revenir)."""
    project.__dict__.clear()
    project.__dict__.update(snapshot.__dict__)


class Transaction:
    """Transaction ouverte (``with session.transaction("libellé") as tx:``) : ``tx.run(opération)`` applique, la sortie du bloc valide.

    Une exception dans le bloc restaure le projet et se propage ; une sortie normale recadre, enregistre **une** étape d'historique et
    pose le bilan dans :attr:`report`.
    """

    def __init__(self, session: Session, label: str, merge_key: str | None) -> None:
        self._session = session
        self._label = label
        self._merge_key = merge_key
        self._snapshot: Project | None = None
        self._results: list[OperationResult] = []
        self.report: ApplyReport | None = None

    def __enter__(self) -> Transaction:
        self._snapshot = self._session._open_with_snapshot()
        return self

    def run(self, operation: Operation, name: str | None = None) -> Any:
        """Applique une opération et rend sa valeur ; une opération refusée lève, la sortie du bloc restaure."""
        value = operation(self._session.project)
        self._results.append(OperationResult(len(self._results), name or _name_of(operation), value))
        return value

    def __exit__(self, exc_type, exc, traceback) -> Literal[False]:
        session = self._session
        try:
            if exc_type is not None:
                if self._snapshot is not None:
                    _restore(session.project, self._snapshot)
                return False
            self.report = session._commit(self._label, tuple(self._results), self._merge_key, self._snapshot)
            return False
        finally:
            session._close_transaction()


class Session:
    """Un projet, son historique, et le moyen de les modifier en transactions.

    ``history`` : l'historique de l'appelant (l'interface passe le sien) ; absent, la session en crée un dont l'état initial est le
    projet tel qu'il est donné. Après un ``undo`` ou un ``redo``, ``session.project`` est **le même objet**, remis dans l'état voulu.
    """

    def __init__(self, project: Project, history: ProjectHistory | None = None) -> None:
        self._project = project
        if history is None:
            history = ProjectHistory()
            history.reset(project)
        self.history = history
        self._open = False

    @property
    def project(self) -> Project:
        return self._project

    def describe(self, *, check_media: bool = False) -> dict[str, Any]:
        """Le projet comme un dictionnaire JSON versionné (:func:`core.scripting.api.describe`) : lecture seule, utilisable à tout moment
        (y compris pendant une transaction : il décrit le projet tel qu'il est à cet instant)."""
        from .describe import describe

        return describe(self._project, check_media=check_media)

    # -- transactions ---------------------------------------------------------------------------------------------------

    def transaction(self, label: str, *, merge_key: str | None = None) -> Transaction:
        """Ouvre une transaction : ``with session.transaction("Montage") as tx: tx.run(...)``."""
        return Transaction(self, label, merge_key)

    def apply(
        self,
        operations: Iterable[Operation | tuple[str, Operation]],
        *,
        label: str,
        merge_key: str | None = None,
    ) -> ApplyReport:
        """Applique ``operations`` en tout ou rien et rend le bilan ; un refus du modèle est **dans le rapport**, pas une exception.

        Chaque élément est une opération, ou un couple ``(nom, opération)`` pour un nom lisible dans le rapport. La suite d'opérations
        est **lue en entier avant le premier effet** : un itérable qui échoue en route (ou un élément qui n'est pas une opération)
        lève avant que le projet soit touché, et n'est jamais pris pour le refus d'une opération qui, elle, a réussi.
        """
        planned = _plan(operations)
        snapshot = self._open_with_snapshot()
        results: list[OperationResult] = []
        index = 0
        name = ""
        try:
            for index, (name, operation) in enumerate(planned):
                results.append(OperationResult(index, name, operation(self._project)))
        except _REFUSALS as error:
            _restore(self._project, snapshot)
            LOGGER.info("Transaction « %s » refusée à l'opération %s (%s) : %s", label, index, name, error)
            return ApplyReport(
                ok=False, label=label, results=tuple(results), failed_index=index, failed_name=name,
                error=str(error) or type(error).__name__, error_type=type(error).__name__,
            )
        except BaseException:
            _restore(self._project, snapshot)          # une erreur de programmation : projet restauré, puis elle se propage
            raise
        else:
            # Hors des ``except`` ci-dessus : une erreur de recadrage ou d'historique n'est pas le refus d'une opération.
            return self._commit(label, tuple(results), merge_key, snapshot)
        finally:
            self._close_transaction()

    # -- historique -----------------------------------------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    def undo(self) -> bool:
        """Annule la dernière transaction ; ``False`` s'il n'y a rien à annuler. Refusé pendant une transaction."""
        self._require_idle("annuler")
        restored = self.history.undo()
        if restored is None:
            return False
        _restore(self._project, restored)
        return True

    def redo(self) -> bool:
        """Rétablit la dernière transaction annulée ; ``False`` s'il n'y a rien à rétablir. Refusé pendant une transaction."""
        self._require_idle("rétablir")
        restored = self.history.redo()
        if restored is None:
            return False
        _restore(self._project, restored)
        return True

    # -- interne --------------------------------------------------------------------------------------------------------

    def _require_idle(self, action: str) -> None:
        """Annuler ou rétablir pendant une transaction ferait avancer l'historique que la restauration ne remet pas en place."""
        if self._open:
            raise SessionBusyError(f"Impossible de {action} pendant une transaction : terminez-la d'abord.")

    def _open_with_snapshot(self) -> Project:
        """Ouvre la transaction et rend l'instantané à restaurer en cas de refus. Si la copie échoue (mémoire, contenu non copiable),
        la session est refermée avant que l'erreur se propage : elle ne reste pas « occupée » pour toujours."""
        self._open_transaction()
        try:
            return deepcopy(self._project)
        except BaseException:
            self._close_transaction()
            raise

    def _open_transaction(self) -> None:
        if self._open:
            raise SessionBusyError("Une transaction est déjà ouverte : elles ne s'imbriquent pas.")
        self._open = True

    def _close_transaction(self) -> None:
        self._open = False

    def _commit(
        self, label: str, results: tuple[OperationResult, ...], merge_key: str | None, snapshot: Project | None,
    ) -> ApplyReport:
        """Recadre les séquences imbriquées puis enregistre **une** étape ; sans opération, rien n'est enregistré.

        Une erreur ici (recadrage, historique) restaure le projet, comme un refus d'opération.
        """
        if not results:
            return ApplyReport(ok=True, label=label)
        try:
            adjustments = tuple(clamp_nested_clips(self._project))
            self.history.record(self._project, label, merge_key=merge_key)
        except BaseException:
            if snapshot is not None:
                _restore(self._project, snapshot)
            raise
        return ApplyReport(ok=True, label=label, results=results, adjustments=adjustments)


__all__ = ["ApplyReport", "Operation", "OperationResult", "Session", "SessionBusyError", "Transaction"]

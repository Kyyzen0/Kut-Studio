"""Mode script (ADR-0005) : la session d'édition, ses opérations et, plus tard, le format déclaratif et la façade de lecture.

Rien ici n'exécute de code venu de données (règle R1) : une opération est une fonction que l'appelant fournit.
"""

from .session import ApplyReport, Operation, OperationResult, Session, SessionBusyError, Transaction

__all__ = ["ApplyReport", "Operation", "OperationResult", "Session", "SessionBusyError", "Transaction"]

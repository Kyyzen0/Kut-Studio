"""Mode script (ADR-0005) : la session d'édition, le format déclaratif (verbes, décodeur, schéma) et la lecture d'un projet (``describe``, façade publique :mod:`core.scripting.api`).

Rien ici n'exécute de code venu de données (règle R1) : une opération est une fonction que l'appelant fournit, ou un verbe de la table
:data:`VERBS`, qui n'appelle que des fonctions de ``core/`` listées.
"""

from .schema import json_schema
from .script import (
    SCRIPT_VERSION, Script, ScriptInvalid, ScriptIssue, apply_script, created_ids, loads_script, parse_script, read_script,
)
from .session import ApplyReport, Operation, OperationResult, Session, SessionBusyError, Transaction
from .verbs import VERBS, ScriptContext

__all__ = [
    "SCRIPT_VERSION", "VERBS", "ApplyReport", "Operation", "OperationResult", "Script", "ScriptContext", "ScriptInvalid", "ScriptIssue", "Session",
    "SessionBusyError", "Transaction", "apply_script", "created_ids", "json_schema", "loads_script", "parse_script", "read_script",
]

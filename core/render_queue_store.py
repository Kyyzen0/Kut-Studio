"""Persistance de la file de rendu (pur : aucune dépendance Qt).

Disposition, sous ``<config>/render_queue/`` (jamais dans le dépôt) ::

    queue.json              liste des jobs (métadonnées uniquement)
    jobs/<id>/project.kut   instantané du projet pour ce job
    jobs/<id>/…             LUT copiées avec l'instantané

``queue.json`` reste petit : il ne contient que les réglages et l'état
des jobs. Le projet n'y est jamais copié, seulement référencé par
l'instantané, supprimé avec le job.

Un fichier illisible n'empêche pas de démarrer : il est mis de côté
(``queue.json.corrupt``) et la file repart vide. Un job isolé invalide
est ignoré sans perdre les autres.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from .platform_paths import user_config_dir
from .project_io import save_project
from .project_model import Project
from .render_job import SCHEMA_VERSION, RenderJob, is_safe_job_id

QUEUE_FILE = "queue.json"
SNAPSHOT_NAME = "project.kut"


def default_queue_dir() -> Path:
    """Répertoire de la file dans la configuration utilisateur (non créé)."""
    return user_config_dir() / "render_queue"


class RenderQueueStore:
    """Lecture / écriture atomiques de la file et de ses instantanés."""

    def __init__(self, directory: str | os.PathLike[str] | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_queue_dir()

    @property
    def queue_file(self) -> Path:
        return self.directory / QUEUE_FILE

    @property
    def jobs_dir(self) -> Path:
        return self.directory / "jobs"

    # -- File --------------------------------------------------------------------------------

    def load(self) -> list[RenderJob]:
        """Relit les jobs ; ne lève jamais (fichier absent, corrompu ou partiel)."""
        path = self.queue_file
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            return []
        try:
            data = json.loads(raw)
            entries = data["jobs"] if isinstance(data, dict) else None
            if not isinstance(entries, list):
                raise ValueError("liste de jobs absente")
        except (ValueError, KeyError, TypeError):
            self._set_aside(path)
            return []
        jobs: list[RenderJob] = []
        seen: set[str] = set()
        for entry in entries:
            try:
                job = RenderJob.from_dict(entry)
            except (ValueError, TypeError):
                continue
            if job.id in seen:
                continue
            seen.add(job.id)
            jobs.append(job)
        return jobs

    def save(self, jobs: list[RenderJob]) -> None:
        """Écrit la file de façon atomique (un échec ne tronque jamais le fichier)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "jobs": [job.to_dict() for job in jobs]}
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{QUEUE_FILE}.", suffix=".tmp", dir=str(self.directory)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.queue_file)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _set_aside(path: Path) -> None:
        try:
            os.replace(path, path.with_name(path.name + ".corrupt"))
        except OSError:
            pass

    # -- Instantanés ----------------------------------------------------------------------------

    def snapshot_dir(self, job_id: str) -> Path:
        """Dossier d'instantané d'un job, toujours **sous** ``jobs_dir``.

        Lève ``ValueError`` pour un identifiant qui s'en échapperait
        (chemin absolu, ``..``) : ce dossier est supprimé récursivement.
        """
        if not is_safe_job_id(job_id):
            raise ValueError(f"Identifiant de job non sûr : {job_id!r}")
        target = self.jobs_dir / job_id
        if target.resolve().parent != self.jobs_dir.resolve():
            raise ValueError(f"Identifiant de job non sûr : {job_id!r}")
        return target

    def snapshot_file(self, job_id: str) -> Path:
        return self.snapshot_dir(job_id) / SNAPSHOT_NAME

    def write_snapshot(self, job_id: str, project: Project) -> Path:
        """Enregistre une copie du projet pour ``job_id`` et retourne son chemin.

        Le projet reçu est copié avant l'écriture : ``save_project`` rend
        les LUT portables en modifiant le projet, ce qui ne doit jamais
        toucher le montage ouvert.
        """
        target = self.snapshot_file(job_id)
        try:
            save_project(copy.deepcopy(project), str(target))
        except Exception:
            self.delete_snapshot(job_id)
            raise
        return target

    def delete_snapshot(self, job_id: str) -> None:
        try:
            target = self.snapshot_dir(job_id)
        except ValueError:
            return  # identifiant suspect : on ne supprime rien
        shutil.rmtree(target, ignore_errors=True)

    def prune_orphans(self, known_ids: set[str]) -> int:
        """Supprime les instantanés qu'aucun job ne référence plus."""
        removed = 0
        if not self.jobs_dir.is_dir():
            return 0
        for child in self.jobs_dir.iterdir():
            if child.is_dir() and child.name not in known_ids:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed


__all__ = ["QUEUE_FILE", "SNAPSHOT_NAME", "RenderQueueStore", "default_queue_dir"]

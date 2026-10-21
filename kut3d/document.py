"""Document de scène éditable : états immuables + commandes + historique annulable + aller-retour `.kut3d` (ADR-0010).

Sans Qt, sans disque hors `load` / `save`. Un `SceneDocument` garde une liste d'états (`DocumentState`) ; `execute` applique une
commande (`kut3d.document_commands`) à l'état courant. Une commande refusée lève `DocumentError` et ne change RIEN (ni l'état, ni
l'historique, ni la révision). Les états partagent leurs `Model` : l'historique coûte quelques tuples, pas des copies de maillages.

**Historique** : au plus `MAX_UNDO` éditions annulables ; une édition après un `undo` jette les rétablissements ; les commandes à
`merge_key` identique qui se suivent (un geste) tiennent en une entrée, jusqu'à `end_gesture`, `undo`, `redo` ou un enregistrement.
Jamais de fusion avec l'état initial ni avec l'état enregistré : l'indicateur « modifié » ne ment pas.

**Fichier** : `load` lit un `.kut3d` ; `save` écrit de façon atomique (`save_scene`) et refuse d'écraser un fichier qui a changé
sur le disque depuis la dernière lecture ou écriture (`DocumentConflict`), sauf `overwrite=True`. Relire puis réécrire une scène
produit les mêmes octets (`save_scene` est déterministe) ; les identifiants de modèles du fichier sont recalculés à l'écriture.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from kut3d.document_commands import Command
from kut3d.document_state import (
    DocumentError,
    DocumentState,
    state_from_scene,
)
from kut3d.errors import SceneFileError, short
from kut3d.scene import AmbientLight, Camera, DirectionalLight, Scene
from kut3d.scene_io import load_scene, save_scene

MAX_UNDO = 100  # éditions annulables conservées (même ordre de grandeur que `core.edit_history.MAX_HISTORY`)

Stamp = tuple[int, int, str]  # (mtime_ns, taille, empreinte du contenu)


class DocumentConflict(DocumentError):
    """Le fichier a changé sur le disque depuis la dernière lecture ou écriture : l'enregistrer l'écraserait."""


def _stamp(path: Path) -> Stamp | None:
    try:
        info = path.stat()
        data = path.read_bytes()
    except (OSError, ValueError):
        return None
    return (info.st_mtime_ns, info.st_size, hashlib.blake2b(data, digest_size=16).hexdigest())


def default_scene() -> Scene:
    """La scène d'un nouveau document : caméra de face, un peu d'ambiance, un soleil ; aucun objet."""
    return Scene(
        instances=[],
        lights=[AmbientLight((1.0, 1.0, 1.0), 0.3), DirectionalLight((-0.4, -0.6, -0.7), (1.0, 1.0, 1.0), 2.5)],
        camera=Camera(position=(0.0, 1.5, 6.0), target=(0.0, 0.5, 0.0)),
    )


class _Entry:
    __slots__ = ("label", "state")

    def __init__(self, label: str, state: DocumentState) -> None:
        self.label = label
        self.state = state


class SceneDocument:
    """Une scène en cours d'édition. Voir le module."""

    def __init__(self, scene: Scene | None = None, *, path: str | Path | None = None) -> None:
        self._entries = [_Entry("État initial", state_from_scene(scene if scene is not None else default_scene()))]
        self._index = 0
        self._saved_index: int | None = 0
        self._open_key: str | None = None
        self._counter = self._entries[0].state.next_id  # jamais réutilisé, même après annuler puis éditer
        self._path = None if path is None else Path(path)
        self._stamp: Stamp | None = None
        self._scene_cache: tuple[int, Scene] | None = None
        self.revision = 0
        """Augmente à chaque changement de l'état courant (édition, annulation, rétablissement) : un affichage sait ainsi qu'il doit se redessiner."""

    # --- lecture / écriture -------------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, *, allow_outside: bool = False) -> SceneDocument:
        """Ouvre un `.kut3d` (`SceneFileError` s'il est refusé). Le document se souvient du fichier et de son empreinte."""
        p = Path(path)
        scene = load_scene(p, allow_outside=allow_outside)
        doc = cls(scene, path=p)
        doc._stamp = _stamp(p)
        return doc

    @property
    def path(self) -> Path | None:
        return self._path

    def save(self, path: str | Path | None = None, *, allow_outside: bool = False, overwrite: bool = False) -> None:
        """Écrit la scène (atomique, déterministe). Sans `path`, réécrit le fichier d'origine.

        `DocumentConflict` si ce fichier a changé sur le disque depuis qu'on l'a lu ou écrit (un autre programme, ou un autre
        clip qui l'édite) ; `overwrite=True` l'écrase malgré tout. `SceneFileError` si la scène n'est pas enregistrable."""
        target = self._path if path is None else Path(path)
        if target is None:
            raise DocumentError("aucun fichier : donnez un chemin.")
        if target == self._path and not overwrite and _stamp(target) != self._stamp:
            raise DocumentConflict(
                f"{short(target.name)} a changé sur le disque depuis sa lecture : rechargez-le, ou enregistrez en l'écrasant."
            )
        save_scene(self.scene(), target, allow_outside=allow_outside)
        self._path = target
        self._stamp = _stamp(target)
        self._saved_index = self._index
        self._open_key = None

    # --- état ---------------------------------------------------------------------------------------

    @property
    def state(self) -> DocumentState:
        return self._entries[self._index].state

    def scene(self) -> Scene:
        """La `Scene` immuable de l'état courant (mémorisée par révision : l'afficher à chaque image ne reconstruit rien)."""
        cached = self._scene_cache
        if cached is not None and cached[0] == self.revision:
            return cached[1]
        try:
            built = self.state.to_scene()
        except ValueError as exc:  # ne devrait pas arriver : chaque commande revalide
            raise DocumentError(short(str(exc), 160)) from exc
        self._scene_cache = (self.revision, built)
        return built

    # --- édition ------------------------------------------------------------------------------------

    def execute(self, command: Command) -> tuple[str, ...]:
        """Applique `command`. Retourne les identifiants CRÉÉS (objet ou lumière ajoutés), dans l'ordre. Refus : rien ne change."""
        before = self.state
        if before.next_id < self._counter:  # annuler a ramené un compteur plus ancien : les identifiants jetés ne reviennent pas
            before = replace(before, next_id=self._counter)
        after = command.apply(before)
        if not isinstance(after, DocumentState):
            raise DocumentError("la commande n'a pas rendu un état de document.")
        key = command.merge_key
        merge = (
            key is not None
            and key == self._open_key
            and self._index > 0
            and self._index != self._saved_index
            and self._index == len(self._entries) - 1
        )
        if merge:
            self._entries[self._index] = _Entry(command.label, after)
        else:
            if self._saved_index is not None and self._saved_index > self._index:
                self._saved_index = -1  # l'état enregistré vivait dans la branche « rétablir » qu'on jette
            del self._entries[self._index + 1 :]
            self._entries.append(_Entry(command.label, after))
            self._index += 1
            if len(self._entries) > MAX_UNDO + 1:
                self._entries.pop(0)
                self._index -= 1
                if self._saved_index is not None:
                    self._saved_index -= 1
        self._open_key = key
        self.revision += 1
        self._counter = max(self._counter, after.next_id)
        known = set(before.ids())
        return tuple(i for i in after.ids() if i not in known)

    def end_gesture(self) -> None:
        """Le geste continu est fini (bouton de souris relâché) : la prochaine commande ouvrira une nouvelle entrée."""
        self._open_key = None

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._entries) - 1

    @property
    def undo_label(self) -> str | None:
        return self._entries[self._index].label if self.can_undo else None

    @property
    def redo_label(self) -> str | None:
        return self._entries[self._index + 1].label if self.can_redo else None

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        self._index -= 1
        self._open_key = None
        self.revision += 1
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        self._index += 1
        self._open_key = None
        self.revision += 1
        return True

    @property
    def is_dirty(self) -> bool:
        """Vrai si l'état courant n'est pas celui du dernier chargement ou enregistrement."""
        return self._saved_index != self._index

    def history(self) -> tuple[str, ...]:
        """Les libellés, du plus ancien au plus récent (panneau Historique)."""
        return tuple(e.label for e in self._entries)


__all__ = ["MAX_UNDO", "DocumentConflict", "DocumentError", "SceneDocument", "SceneFileError", "default_scene"]

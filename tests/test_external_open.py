"""Ouverture d'un projet .kut confiée par le système : Finder (macOS), argument de lancement, ligne de commande."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFileOpenEvent

import build
from ui.external_open import install_external_open, is_project_path, project_path_from_arguments
from ui.main_window_mixins.project_files import ProjectFilesMixin


def test_only_kut_files_are_project_paths_whatever_the_case():
    assert is_project_path("/Films/Montage.kut")
    assert is_project_path("/Films/MONTAGE.KUT")
    assert not is_project_path("/Films/clip.mp4")
    assert not is_project_path("/Films/montage.kut.bak")


def test_startup_argument_is_the_first_project_and_options_are_skipped():
    assert project_path_from_arguments(["--smoke-test", "/Films/clip.mp4", "/Films/montage.kut"]) == "/Films/montage.kut"
    assert project_path_from_arguments(["-psn_0_12345"]) is None
    assert project_path_from_arguments([]) is None


def test_finder_open_event_for_a_project_is_handed_to_the_opener(qapp):
    opened: list[str] = []
    handler = install_external_open(qapp, opened.append)
    try:
        QCoreApplication.sendEvent(qapp, QFileOpenEvent("/Films/montage.kut"))
    finally:
        qapp.removeEventFilter(handler)
    assert opened == ["/Films/montage.kut"]


def test_open_event_for_another_file_is_left_alone(qapp):
    opened: list[str] = []
    handler = install_external_open(qapp, opened.append)
    try:
        QCoreApplication.sendEvent(qapp, QFileOpenEvent("/Films/clip.mp4"))
    finally:
        qapp.removeEventFilter(handler)
    assert opened == []


def test_macos_bundle_declares_the_kut_document_type():
    documents = build.macos_info_overrides()["CFBundleDocumentTypes"]
    assert any("kut" in document["CFBundleTypeExtensions"] for document in documents)


def test_opening_an_external_project_goes_through_the_normal_load_path():
    calls: list[str] = []

    class Window:
        def _load_project_from_path(self, path: str) -> None:
            calls.append(path)

        def raise_(self) -> None:
            calls.append("raise")

        def activateWindow(self) -> None:  # noqa: N802 - API Qt
            calls.append("activate")

    ProjectFilesMixin.open_external_project(Window(), "/Films/montage.kut")
    assert calls == ["/Films/montage.kut", "raise", "activate"]

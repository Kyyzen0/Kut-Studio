"""« Installer et redémarrer » dans la vraie fenêtre : proposé seulement si la signature est vérifiée, fermeture normale
d'abord, jamais d'installation réelle (la préparation et l'assistant sont remplacés), message du démarrage suivant.

Les fixtures et aides viennent de ``test_update_ui`` (faux GitHub local, fenêtre isolée).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path, PurePosixPath

import pytest
from test_update_install import SILENT_EXE, make_app
from test_update_ui import (
    NEW,
    REPOSITORY,
    _finish_check,
    _finish_download,
    _publish,
    _visible_buttons,
    _window_for,
    opened,  # noqa: F401  (fixture)
    server,  # noqa: F401  (fixture)
)
from update_server import serve_releases

from core import update_helper
from core.app_version import APP_VERSION
from core.release_assets import Architecture, OperatingSystem, Target
from core.update_install import FixedPolicy, InstallOutcome, StageError, StageProblem
from core.updates import CheckMode, InstallContext, InstallKind
from ui import i18n
from ui import update_auto_install as auto
from ui.update_dialog import UpdateDialog, UpdateDialogState


@pytest.fixture
def installed_app(tmp_path):
    """Un faux ``Kut-Studio.app`` réel (inscriptible) : la décision examine le disque."""
    folder = tmp_path / "Applications"
    folder.mkdir()
    return make_app(folder, version=APP_VERSION, executable=SILENT_EXE)


@pytest.fixture
def context(installed_app):
    return InstallContext(InstallKind.MACOS_APP, PurePosixPath(str(installed_app)))


class Recorder:
    """Ce que la préparation et l'assistant (remplacés) ont reçu, dans l'ordre."""

    def __init__(self, app: Path) -> None:
        self.app = app
        self.events: list[str] = []
        self.plans: list[update_helper.HelperPlan] = []
        self.stage_calls: list[dict] = []
        self.fail: StageError | None = None
        self.launch_error: Exception | None = None
        self.gate: threading.Event | None = None

    def stage(self, archive, app, **kwargs):
        self.events.append("stage")
        self.stage_calls.append({"archive": archive, "app": app, **kwargs})
        if self.gate is not None:
            self.gate.wait(timeout=30)
        if self.fail is not None:
            raise self.fail
        staging = app.parent / f".Kut-Studio-update-{kwargs['pid']}"
        (staging / "Kut-Studio.app").mkdir(parents=True)
        return staging

    def launch(self, plan):
        self.events.append("launch")
        self.plans.append(plan)
        if self.launch_error is not None:
            raise self.launch_error
        return 1


@pytest.fixture
def recorder(monkeypatch, installed_app):
    record = Recorder(installed_app)
    monkeypatch.setattr(auto, "_stage", record.stage)
    monkeypatch.setattr(auto, "_launch", record.launch)
    return record


def _ready_window(qtbot, monkeypatch, server, tmp_path, context, *, verified=True):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    window._auto_installer.policy = FixedPolicy(verified)
    _finish_check(qtbot, window, CheckMode.MANUAL)
    result = _finish_download(qtbot, window)
    assert result.error is None
    return window, window._update_dialog


def _answer(monkeypatch, window, answer):
    from ui import main_window as window_module

    asked: list[str] = []

    def question(_parent, title, text, buttons, default):
        asked.append(text)
        assert default == window_module.QMessageBox.No, "par défaut, on ne continue pas"
        return window_module.QMessageBox.Yes if answer else window_module.QMessageBox.No

    monkeypatch.setattr("ui.main_window.QMessageBox.question", question)
    return asked


def _staging_leftovers(app: Path) -> list[str]:
    return sorted(p.name for p in app.parent.iterdir() if p.name.startswith(".Kut-Studio-update-"))


# ---------------------------------------------------------------------------
# Ce que le dialogue propose
# ---------------------------------------------------------------------------


def test_a_verified_package_offers_install_and_restart(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    assert dialog.state is UpdateDialogState.READY
    assert dialog.auto_install_button.text() == i18n.translate("update.button.install_restart")
    names = ("download", "later", "skip", "release_page", "cancel", "retry", "close", "open_folder", "install", "auto_install")
    visible = {name for name in names if getattr(dialog, f"{name}_button").isVisibleTo(dialog)}
    assert visible == {"open_folder", "auto_install", "close"}, "« Quitter et installer… » est remplacé"
    assert dialog.auto_install_button.isDefault()
    assert dialog.instructions.text() == i18n.translate("update.auto.description")
    assert dialog.auto_install_button.accessibleDescription() == i18n.translate("update.auto.description")


def test_an_unverified_package_keeps_the_assisted_install_unchanged(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context, verified=False)
    assert _visible_buttons(dialog) == {"open_folder", "install", "close"}
    assert not dialog.auto_install_button.isVisibleTo(dialog)
    assert dialog.instructions.text() == i18n.translate("update.steps.macos"), "les étapes d'aujourd'hui, inchangées"
    assert i18n.translate("update.auto.unavailable", reason=i18n.translate("update.auto.reason.not_verified")) \
        in dialog.details.text(), "message clair : la signature de cette version n'est pas vérifiée"


def test_the_default_policy_requires_the_signature_status_of_the_package(qtbot, monkeypatch, server, tmp_path, context):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    assert window._auto_installer.policy is None, "la vraie valeur : signature_status du paquet (lot 1)"
    _finish_check(qtbot, window, CheckMode.MANUAL)
    _finish_download(qtbot, window)
    assert not window._update_dialog.auto_install_button.isVisibleTo(window._update_dialog)


def test_a_translocated_copy_is_told_why_the_install_stays_assisted(qtbot, monkeypatch, server, tmp_path, installed_app, recorder):
    context = InstallContext(InstallKind.MACOS_APP, PurePosixPath(str(installed_app)), translocated=True)
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    assert _visible_buttons(dialog) == {"open_folder", "install", "close"}
    assert i18n.translate("update.auto.unavailable", reason=i18n.translate("update.auto.reason.translocated")) \
        in dialog.details.text()


def test_an_application_in_an_unwritable_folder_stays_assisted(qtbot, monkeypatch, server, tmp_path, context, recorder):
    real = auto.evaluate_install
    monkeypatch.setattr(auto, "evaluate_install", lambda *args, **kwargs: real(*args, access=lambda path, mode: False, **kwargs))
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    assert "auto_install" not in _visible_buttons(dialog) and dialog.install_button.isVisibleTo(dialog)
    assert i18n.translate("update.auto.reason.parent_not_writable") in dialog.details.text()


def test_running_from_the_sources_never_proposes_anything_automatic(qtbot, monkeypatch, server, tmp_path, recorder):
    source = InstallContext(InstallKind.SOURCE, PurePosixPath("/src"), revision="abc")
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=source)
    assert not window._auto_installer.startup_enabled()
    assert window._auto_installer.decision(None) is None


# ---------------------------------------------------------------------------
# Le parcours
# ---------------------------------------------------------------------------


def test_install_and_restart_prepares_then_closes_normally_then_launches_the_helper(
        qtbot, monkeypatch, server, tmp_path, context, recorder, opened):  # noqa: F811
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    asked = _answer(monkeypatch, window, True)
    closes: list[bool] = []
    original_close = type(window).close

    def tracked_close(self):
        recorder.events.append("close")
        closes.append(True)
        return original_close(self)

    monkeypatch.setattr(type(window), "close", tracked_close)
    dialog.auto_install_button.click()
    assert asked == [i18n.translate("update.auto.confirm.text")]
    qtbot.waitUntil(lambda: "launch" in recorder.events, timeout=8000)
    assert recorder.events == ["stage", "close", "launch"], "préparer, fermer par le chemin normal, puis lancer l'assistant"
    assert not window.isVisible()
    (plan,) = recorder.plans
    assert plan.app == recorder.app and plan.pid == os.getpid()
    assert plan.version == NEW and plan.from_version == APP_VERSION
    call = recorder.stage_calls[0]
    assert call["signature_verified"] is True and call["required_arch"] == "arm64" and str(call["expected_version"]) == NEW
    assert opened == [], "pas de dossier ouvert : l'installation est automatique"


def test_without_consent_nothing_is_prepared(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    _answer(monkeypatch, window, False)
    dialog.auto_install_button.click()
    assert recorder.events == [] and window.isVisible() and dialog.state is UpdateDialogState.READY
    assert _staging_leftovers(recorder.app) == []


def test_a_refused_close_removes_the_staging_and_launches_nothing(qtbot, monkeypatch, server, tmp_path, context, recorder):
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    _answer(monkeypatch, window, True)
    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: False)    # « Annuler » à l'enregistrement
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: dialog.state is UpdateDialogState.READY and "stage" in recorder.events, timeout=8000)
    assert window.isVisible(), "un refus garde le montage ouvert"
    assert recorder.events == ["stage"] and recorder.plans == []
    assert _staging_leftovers(recorder.app) == [], "le staging est supprimé : l'application n'a pas été touchée"
    assert dialog.auto_install_button.isVisibleTo(dialog), "on peut recommencer"


@pytest.mark.parametrize("problem", list(StageProblem))
def test_a_failed_preparation_falls_back_to_the_assisted_install(qtbot, monkeypatch, server, tmp_path, context, recorder, problem):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    recorder.fail = StageError(problem, "détail technique")
    _answer(monkeypatch, window, True)
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: "auto_install" not in _visible_buttons(dialog) and dialog.state is UpdateDialogState.READY,
                    timeout=8000)
    assert window.isVisible() and recorder.plans == [], "la fenêtre reste ouverte, rien n'est lancé"
    assert i18n.translate(f"update.auto.problem.{problem.value}") in dialog.details.text()
    assert dialog.instructions.text() == i18n.translate("update.steps.macos"), "les étapes d'installation à la main"
    assert dialog.install_button.isVisibleTo(dialog)


def test_cancelling_the_preparation_installs_nothing_and_cleans_up(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    recorder.gate = threading.Event()
    _answer(monkeypatch, window, True)
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: "stage" in recorder.events, timeout=8000)
    assert dialog.state is UpdateDialogState.INSTALLING
    assert dialog.progress.maximum() == 0, "barre indéterminée"
    assert dialog.progress.accessibleName() == i18n.translate("update.auto.progress.name")
    assert {name for name in ("cancel", "auto_install", "install", "close", "open_folder")
            if getattr(dialog, f"{name}_button").isVisibleTo(dialog)} == {"cancel"}
    dialog.cancel_button.click()
    assert dialog.state is UpdateDialogState.READY
    recorder.gate.set()
    qtbot.waitUntil(lambda: _staging_leftovers(recorder.app) == [], timeout=8000)
    assert window.isVisible() and "launch" not in recorder.events


def test_escape_during_the_preparation_cancels_it(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    recorder.gate = threading.Event()
    _answer(monkeypatch, window, True)
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: "stage" in recorder.events, timeout=8000)
    dialog.reject()
    assert dialog.state is UpdateDialogState.READY
    recorder.gate.set()
    qtbot.waitUntil(lambda: _staging_leftovers(recorder.app) == [], timeout=8000)
    assert "launch" not in recorder.events


def test_an_unlaunchable_helper_removes_the_staging_and_opens_the_folder(
        qtbot, monkeypatch, server, tmp_path, context, recorder, opened):  # noqa: F811
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    recorder.launch_error = OSError("refusé")
    _answer(monkeypatch, window, True)
    package = dialog.download.path
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: bool(opened), timeout=8000)
    assert _staging_leftovers(recorder.app) == []
    assert opened[-1] == ("folder", str(package.parent)), "repli sur l'installation à la main"


def test_closing_the_window_during_the_preparation_discards_the_result(qtbot, monkeypatch, server, tmp_path, context, recorder):
    window, dialog = _ready_window(qtbot, monkeypatch, server, tmp_path, context)
    recorder.gate = threading.Event()
    _answer(monkeypatch, window, True)
    dialog.auto_install_button.click()
    qtbot.waitUntil(lambda: "stage" in recorder.events, timeout=8000)
    window.close()
    recorder.gate.set()
    qtbot.waitUntil(lambda: _staging_leftovers(recorder.app) == [], timeout=8000)
    assert "launch" not in recorder.events


# ---------------------------------------------------------------------------
# Démarrage suivant
# ---------------------------------------------------------------------------


@pytest.fixture
def messages(monkeypatch):
    shown: list[tuple[str, str, list[str], bool]] = []

    def capture(parent, title, text, actions, *, warning):
        shown.append((title, text, [label for label, _callback in actions], warning))
        return actions

    monkeypatch.setattr(auto, "show_message", capture)
    return shown


@pytest.fixture
def updates_dir(monkeypatch, tmp_path):
    folder = tmp_path / "cache" / "updates"
    folder.mkdir(parents=True)
    monkeypatch.setattr(auto, "updates_directory", lambda: folder)
    return folder


def _status(folder: Path, **values: str) -> None:
    data = {"result": "ok", "version": APP_VERSION, "previous": "0.0.1", "reason": "started", **values}
    (folder / update_helper.STATUS_NAME).write_text(json.dumps(data), encoding="utf-8")


def test_startup_writes_the_started_file_and_reports_a_successful_update_once(
        qtbot, monkeypatch, server, tmp_path, context, updates_dir, messages):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    _status(updates_dir)
    window._auto_installer.startup()
    marker = json.loads((updates_dir / update_helper.STARTED_NAME).read_text(encoding="utf-8"))
    assert marker["version"] == APP_VERSION and marker["pid"] == os.getpid()
    ((title, text, actions, warning),) = messages
    assert text == i18n.translate("update.outcome.ok.text", version=APP_VERSION) and not warning
    assert actions == [i18n.translate("update.outcome.notes")], "avec le lien vers les notes"
    assert not (updates_dir / update_helper.STATUS_NAME).exists(), "une seule fois"
    window._auto_installer.startup()
    assert len(messages) == 1


def test_startup_reports_a_rollback_with_the_log(qtbot, monkeypatch, server, tmp_path, context, updates_dir, messages):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    _status(updates_dir, result="rolled_back", version="9.9.9", reason="start_timeout")
    window._auto_installer.startup()
    ((title, text, actions, warning),) = messages
    assert title == i18n.translate("update.outcome.failed.title") and warning
    assert "9.9.9" in text and i18n.translate("update.outcome.reason.start_timeout") in text
    assert actions == [i18n.translate("update.outcome.log")]


def test_a_successful_status_for_another_version_is_discarded_silently(
        qtbot, monkeypatch, server, tmp_path, context, updates_dir, messages):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    _status(updates_dir, version="9.9.9")
    window._auto_installer.startup()
    assert messages == [] and not (updates_dir / update_helper.STATUS_NAME).exists()


def test_the_new_application_waits_for_the_status_while_the_helper_is_alive(
        qtbot, monkeypatch, server, tmp_path, context, updates_dir, messages):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    (updates_dir / update_helper.LOCK_NAME).write_text(str(os.getpid()), encoding="utf-8")
    scheduled: list[int] = []
    monkeypatch.setattr(auto.QTimer, "singleShot", staticmethod(lambda ms, callback: scheduled.append(ms)))
    window._auto_installer.check_outcome()
    assert scheduled == [auto.OUTCOME_POLL_MS] and messages == []
    _status(updates_dir)
    window._auto_installer.check_outcome()
    assert len(messages) == 1


def test_startup_does_nothing_outside_a_macos_application(qtbot, monkeypatch, server, tmp_path, updates_dir, messages):
    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path,
                         context=InstallContext(InstallKind.SOURCE, PurePosixPath("/src")))
    _status(updates_dir)
    window._auto_installer.startup()
    assert messages == [] and list(updates_dir.iterdir()) == [updates_dir / update_helper.STATUS_NAME]


# ---------------------------------------------------------------------------
# Traductions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_every_installation_text_is_translated(qtbot, tmp_path, monkeypatch, language, context, installed_app):
    from core.update_install import InstallDecision, InstallRefusal
    from test_update_ui import _offer

    original = i18n.current_language()
    try:
        i18n.set_language(language)
        with i18n.strict_translations():
            ctx = InstallContext(InstallKind.MACOS_APP, PurePosixPath(str(installed_app)))
            dialog = UpdateDialog(ctx)
            qtbot.addWidget(dialog)
            offer = _offer(target=Target(OperatingSystem.MACOS, Architecture.ARM64))
            from core.update_service import DownloadResult

            result = DownloadResult(offer, path=tmp_path / "Kut-Studio.zip", sha256="0" * 64)
            dialog.show_download_result(result)
            dialog.set_install_decision(InstallDecision(True, None, installed_app))
            assert dialog.auto_install_button.text() and dialog.instructions.text()
            dialog.show_installing()
            assert dialog.headline.text() and dialog.progress_label.text()
            for refusal in InstallRefusal:
                dialog.show_download_result(result)
                dialog.set_install_decision(InstallDecision(False, refusal, installed_app))
            for problem in StageProblem:
                dialog.show_auto_failure(i18n.translate(f"update.auto.problem.{problem.value}"))
            assert not dialog.details.text().startswith("[")
            installer = auto.AutoInstaller.__new__(auto.AutoInstaller)
            seen: list[str] = []
            monkeypatch.setattr(auto, "show_message", lambda parent, title, text, actions, **_k: seen.extend(
                [title, text, *[label for label, _cb in actions]]))
            installer.window = type("W", (), {})()
            installer.shown = []
            reasons = ["wait_timeout", "swap_failed", "staging_invalid", "unexpected_state", "start_timeout",
                       "crashed_after_start", "started_marker_invalid", "open_failed", "signal", "interrupted",
                       "rollback_failed", "started"]
            for reason in reasons:
                for status in ("rolled_back", "failed"):
                    installer.show_outcome(InstallOutcome(status, "9.9.9", "0.0.1", reason))
            installer.show_outcome(InstallOutcome("ok", APP_VERSION, "0.0.1", "started"))
            assert seen and not any(text.startswith("[") for text in seen)
    finally:
        i18n.set_language(original)


def test_the_real_policy_follows_the_signature_of_the_downloaded_package(qtbot, monkeypatch, server, tmp_path, context, recorder):
    from test_update_install import SUMS, _download, _key
    from test_update_ui import _offer

    from core.update_install import SignaturePolicy
    from core.update_service import DownloadResult

    serve_releases(server, REPOSITORY, [_publish(server)])
    window = _window_for(qtbot, monkeypatch, server, tmp_path, context=context)
    window._auto_installer.policy = SignaturePolicy(keys=[_key(1)[1]])
    dialog = window._ensure_update_dialog()
    offer = _offer(target=Target(OperatingSystem.MACOS, Architecture.ARM64))
    for label, signed, expected in (("vérifiée", _download(), True), ("non signée", _download(signer=None), False),
                                    ("clé inconnue", _download(signer=2), False),
                                    ("falsifiée", _download(sums=SUMS + b"x"), False)):
        result = DownloadResult(offer, path=tmp_path / "Kut-Studio.zip", sha256="0" * 64, signature=signed.signature)
        dialog.show_download_result(result)
        window._auto_installer.refresh_dialog()
        assert dialog.auto_install_button.isVisibleTo(dialog) is expected, label
        assert dialog.install_button.isVisibleTo(dialog) is (not expected), label

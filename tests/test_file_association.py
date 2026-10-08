"""Association des projets .kut sous Windows et Linux : ce qui est écrit, et seulement là où l'utilisateur l'a demandé.

Aucun test n'écrit dans le registre ni dans le profil réel : le registre et le répertoire de données sont injectés.
"""

from __future__ import annotations

import configparser
import sys
import xml.etree.ElementTree as ET

import core.file_association as association
from core.file_association import (
    associate_project_files,
    desktop_entry,
    file_association_supported,
    mime_definition,
)

COMMAND = ["/opt/kut/Kut-Studio"]


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_default(self, key: str, value: str) -> None:
        self.values[key] = value


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command, **options):
        self.calls.append(list(command))
        return None


def test_only_windows_and_linux_get_the_association(tmp_path):
    assert file_association_supported("win32") and file_association_supported("linux")
    assert not file_association_supported("darwin")
    registry = FakeRegistry()
    result = associate_project_files(platform="darwin", command=COMMAND, registry=registry, data_home=tmp_path)
    assert result.status == "unsupported"
    assert registry.values == {} and list(tmp_path.iterdir()) == []


def test_windows_extension_points_to_a_launcher_that_receives_the_project(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    registry = FakeRegistry()
    exe = "C:\\Program Files\\Kut-Studio\\Kut-Studio.exe"
    result = associate_project_files(platform="win32", command=[exe], registry=registry)

    assert result.status == "associated"
    assert registry.values[".kut"] == "KutStudio.Projet"
    assert registry.values["KutStudio.Projet\\shell\\open\\command"] == f'"{exe}" "%1"'
    assert registry.values["KutStudio.Projet\\DefaultIcon"] == f'"{exe}",0'


def test_windows_source_run_keeps_the_default_icon_out(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    registry = FakeRegistry()
    associate_project_files(platform="win32", command=["C:\\python.exe", "main.py"], registry=registry)
    assert "KutStudio.Projet\\DefaultIcon" not in registry.values
    assert registry.values["KutStudio.Projet\\shell\\open\\command"] == '"C:\\python.exe" "main.py" "%1"'


def test_desktop_entry_is_a_valid_launcher_with_quoted_arguments():
    text = desktop_entry(["/home/moi/Kut Studio/kut-studio", "--flag"])
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # les clés de .desktop sont sensibles à la casse
    parser.read_string(text)
    entry = parser["Desktop Entry"]
    assert entry["Type"] == "Application"
    assert entry["MimeType"] == "application/x-kut-studio;"
    assert entry["Exec"] == '"/home/moi/Kut Studio/kut-studio" --flag %f'


def test_a_percent_sign_in_the_path_is_doubled_for_the_desktop_file():
    assert desktop_entry(["/opt/100%/kut"]).splitlines()[3] == "Exec=/opt/100%%/kut %f"


def test_mime_definition_is_well_formed_and_matches_the_kut_extension():
    root = ET.fromstring(mime_definition())
    namespace = "{http://www.freedesktop.org/standards/shared-mime-info}"
    mime_type = root.find(f"{namespace}mime-type")
    assert mime_type is not None and mime_type.get("type") == "application/x-kut-studio"
    assert mime_type.find(f"{namespace}glob").get("pattern") == "*.kut"


def test_linux_association_writes_two_files_under_the_data_home_and_refreshes_databases(tmp_path, monkeypatch):
    tools = {"update-mime-database": "/usr/bin/update-mime-database",
             "update-desktop-database": "/usr/bin/update-desktop-database"}
    monkeypatch.setattr(association.shutil, "which", lambda name: tools.get(name))
    runner = RecordingRunner()

    result = associate_project_files(platform="linux", command=COMMAND, data_home=tmp_path, runner=runner)

    assert result.status == "associated"
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert written == ["applications/kut-studio.desktop", "mime/packages/kut-studio.xml"]
    assert runner.calls == [
        ["/usr/bin/update-mime-database", str(tmp_path / "mime")],
        ["/usr/bin/update-desktop-database", str(tmp_path / "applications")],
    ]


def test_running_the_association_twice_leaves_the_same_files(tmp_path):
    first = associate_project_files(platform="linux", command=COMMAND, data_home=tmp_path, runner=RecordingRunner())
    before = (tmp_path / "applications" / "kut-studio.desktop").read_text(encoding="utf-8")
    second = associate_project_files(platform="linux", command=COMMAND, data_home=tmp_path, runner=RecordingRunner())
    assert first.status == second.status == "associated"
    assert (tmp_path / "applications" / "kut-studio.desktop").read_text(encoding="utf-8") == before


def test_a_profile_that_cannot_be_written_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "pas-un-dossier"
    blocker.write_text("x", encoding="utf-8")
    result = associate_project_files(
        platform="linux", command=COMMAND, data_home=blocker / "data", runner=RecordingRunner(),
    )
    assert result.status == "failed" and result.detail


def test_source_run_launches_main_py(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = association.application_command()
    assert command[0] == sys.executable and command[-1].endswith("main.py")

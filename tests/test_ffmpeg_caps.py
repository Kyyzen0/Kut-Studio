"""La garde libass : saut avec la raison, échec avec ``KUT_STUDIO_REQUIRE_LIBASS=1``, jamais de faux vert.

Ces tests tournent partout : ils interrogent un faux FFmpeg (un petit script Python) dont on choisit les
réponses à ``-filters`` et ``-buildconf``, pas le vrai.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import ffmpeg_caps
import pytest

from core import export_engine

FILTERS_WITH_SUBTITLES = """\
Filters:
  T.. = Timeline support
  ------
 .. ass               V->V       Render ASS subtitles onto input video using the libass library.
 TS blur              V->V       Blur the input.
 .. subtitles         V->V       Render text subtitles onto input video using the libass library.
"""
FILTERS_WITHOUT_SUBTITLES = """\
Filters:
  T.. = Timeline support
  ------
 TS blur              V->V       Blur the input.
 .. unsharp           V->V       Sharpen or blur the input video.
"""
BUILDCONF_WITH_LIBASS = "--enable-gpl\n--enable-libx264\n--enable-libass\n"
BUILDCONF_WITHOUT_LIBASS = "--enable-gpl\n--enable-libx264\n"

FAKE_FFMPEG = textwrap.dedent(
    """\
    import sys
    arguments = sys.argv[1:]
    if "-version" in arguments:
        print("ffmpeg version 99.1-fake Copyright (c) 2000-2099 the FFmpeg developers")
    elif "-filters" in arguments:
        sys.stdout.write({filters!r})
    elif "-buildconf" in arguments:
        sys.stdout.write({buildconf!r})
    """
)


def _use_fake_ffmpeg(monkeypatch, tmp_path: Path, *, filters: str, buildconf: str) -> None:
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(FAKE_FFMPEG.format(filters=filters, buildconf=buildconf), encoding="utf-8")
    monkeypatch.setattr(export_engine, "_ffmpeg_path", (sys.executable, str(script)))


def _guard() -> tuple[str, str]:
    """Issue de la garde : (« run » | « skip » | « fail », message).

    Les trois issues sont attrapées ici : un ``pytest.skip`` qui s'échapperait d'un ``pytest.raises(fail)`` ferait
    *sauter* le test au lieu de l'échouer, et un faux vert se cacherait dans les sauts.
    """
    try:
        ffmpeg_caps.ensure_libass()
    except pytest.skip.Exception as skipped:
        return "skip", str(skipped)
    except pytest.fail.Exception as failed:
        return "fail", str(failed)
    return "run", ""


@pytest.fixture
def not_required(monkeypatch):
    monkeypatch.delenv(ffmpeg_caps.REQUIRE_LIBASS_VARIABLE, raising=False)


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setenv(ffmpeg_caps.REQUIRE_LIBASS_VARIABLE, "1")


def test_a_missing_libass_skips_the_test_with_the_reason_by_default(monkeypatch, tmp_path, not_required) -> None:
    _use_fake_ffmpeg(monkeypatch, tmp_path, filters=FILTERS_WITHOUT_SUBTITLES, buildconf=BUILDCONF_WITHOUT_LIBASS)
    outcome, message = _guard()
    assert outcome == "skip"
    assert "« subtitles »" in message
    assert "libass absent" in message
    assert "99.1-fake" in message    # le FFmpeg incriminé est nommé
    assert not ffmpeg_caps.has_libass()
    assert ffmpeg_caps.libass_skip_reason() == message


def test_a_missing_libass_fails_the_test_when_libass_is_required(monkeypatch, tmp_path, required) -> None:
    _use_fake_ffmpeg(monkeypatch, tmp_path, filters=FILTERS_WITHOUT_SUBTITLES, buildconf=BUILDCONF_WITHOUT_LIBASS)
    outcome, message = _guard()
    assert outcome == "fail"
    assert "KUT_STUDIO_REQUIRE_LIBASS=1" in message
    assert "libass absent" in message
    assert "brew install ffmpeg-full" in message    # le message dit quoi faire


@pytest.mark.parametrize("requirement", ["not_required", "required"])
def test_a_ffmpeg_with_libass_lets_the_test_run(monkeypatch, tmp_path, request, requirement) -> None:
    request.getfixturevalue(requirement)
    _use_fake_ffmpeg(monkeypatch, tmp_path, filters=FILTERS_WITH_SUBTITLES, buildconf=BUILDCONF_WITH_LIBASS)
    assert _guard() == ("run", "")    # ni saut, ni échec
    status = ffmpeg_caps.libass_status()
    assert status.has_filter and status.configured and status.version == "99.1-fake"
    assert ffmpeg_caps.has_libass() and ffmpeg_caps.libass_skip_reason() == ""


def test_the_filter_without_the_build_proof_fails_only_when_libass_is_required(monkeypatch, tmp_path) -> None:
    """Le filtre suffit au comportement historique ; la preuve ``--enable-libass`` n'est exigée qu'en mode strict."""
    _use_fake_ffmpeg(monkeypatch, tmp_path, filters=FILTERS_WITH_SUBTITLES, buildconf=BUILDCONF_WITHOUT_LIBASS)
    monkeypatch.delenv(ffmpeg_caps.REQUIRE_LIBASS_VARIABLE, raising=False)
    assert _guard() == ("run", "")
    monkeypatch.setenv(ffmpeg_caps.REQUIRE_LIBASS_VARIABLE, "1")
    outcome, message = _guard()
    assert outcome == "fail"
    assert "--enable-libass" in message


@pytest.mark.parametrize("line, expected", [
    (" .. subtitles         V->V       Render text subtitles onto input video using the libass library.", True),
    (" TS subtitles         V->V       Render text subtitles.", True),
    (" TSC subtitles        V->V       Render text subtitles.", True),
    (" .. ass               V->V       Render ASS subtitles onto input video using the libass library.", False),
    (" .. unsharp           V->V       Sharpen or blur the input video.", False),
    ("  .S. = Slice threading about subtitles filter", False),
], ids=["deux-drapeaux", "TS", "trois-drapeaux", "filtre-ass-seul", "autre-filtre", "legende"])
def test_only_a_filter_named_subtitles_counts(line, expected) -> None:
    assert bool(ffmpeg_caps._SUBTITLES_FILTER.search(f"Filters:\n  ------\n{line}\n")) is expected


def test_an_unlaunchable_ffmpeg_is_reported_not_swallowed(monkeypatch, tmp_path, not_required) -> None:
    monkeypatch.setattr(export_engine, "_ffmpeg_path", (str(tmp_path / "absent" / "ffmpeg"),))
    outcome, message = _guard()
    assert (outcome, "FFmpeg inutilisable" in message) == ("skip", True)
    monkeypatch.setenv(ffmpeg_caps.REQUIRE_LIBASS_VARIABLE, "1")
    outcome, message = _guard()
    assert (outcome, "FFmpeg inutilisable" in message) == ("fail", True)


def test_a_working_libass_makes_the_application_forget_a_stale_detection(monkeypatch, tmp_path, not_required) -> None:
    """Un test précédent a pu laisser ``_cached = False`` (faux FFmpeg) : l'application relirait ce faux négatif."""
    _use_fake_ffmpeg(monkeypatch, tmp_path, filters=FILTERS_WITH_SUBTITLES, buildconf=BUILDCONF_WITH_LIBASS)
    detection = export_engine._ffmpeg_supports_subtitles
    detection._cached = False    # type: ignore[attr-defined]
    try:
        assert _guard() == ("run", "")
        assert not hasattr(detection, "_cached")
        assert detection() is True
    finally:
        if hasattr(detection, "_cached"):
            del detection._cached


@pytest.mark.parametrize("value, expected", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    ("0", False), ("", False), ("false", False), ("no", False),
])
def test_the_requirement_variable_is_read_as_a_boolean(value, expected) -> None:
    assert ffmpeg_caps.require_libass_requested({ffmpeg_caps.REQUIRE_LIBASS_VARIABLE: value}) is expected
    assert ffmpeg_caps.require_libass_requested({}) is False

"""Versions SemVer : lecture stricte et ordre numérique (jamais lexicographique)."""

from __future__ import annotations

import random

import pytest

from core.app_version import APP_VERSION
from core.versioning import InvalidVersion, Version


def test_the_application_version_is_a_valid_release_version():
    version = Version.parse(APP_VERSION)
    assert not version.build, "une version publiée ne porte pas de métadonnées de construction"
    assert str(version) == APP_VERSION


@pytest.mark.parametrize(
    "older,newer",
    [
        ("0.9.0", "0.10.0"),          # lexicographiquement « 0.9.0 » > « 0.10.0 » : l'erreur classique
        ("1.9.9", "1.10.0"),
        ("1.2.3", "2.0.0"),
        ("1.0.0", "1.0.1"),
        ("9.0.0", "10.0.0"),
    ],
)
def test_numeric_components_are_compared_as_numbers(older, newer):
    assert Version.parse(older) < Version.parse(newer)
    assert not Version.parse(newer) < Version.parse(older)
    assert max(Version.parse(newer), Version.parse(older)) == Version.parse(newer)


def test_a_string_comparison_would_have_picked_the_older_version():
    """Le piège que :class:`Version` évite : en texte, 0.9.0 « dépasse » 0.10.0."""
    assert max(["0.9.0", "0.10.0"]) == "0.9.0"
    assert str(max(Version.parse("0.9.0"), Version.parse("0.10.0"))) == "0.10.0"


def test_the_semver_specification_precedence_example_holds():
    """Exemple de la règle 11 de SemVer 2.0, dans l'ordre exact."""
    ordered = [
        "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
        "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
    ]
    versions = [Version.parse(text) for text in ordered]
    shuffled = versions[:]
    random.Random(4).shuffle(shuffled)
    assert sorted(shuffled) == versions
    assert all(a < b for a, b in zip(versions, versions[1:]))


def test_a_release_is_newer_than_its_prereleases_and_numbers_sort_before_words():
    assert Version.parse("1.0.0-rc.9") < Version.parse("1.0.0")
    assert Version.parse("1.0.0-beta.2") < Version.parse("1.0.0-beta.10")
    assert Version.parse("1.0.0-1") < Version.parse("1.0.0-alpha")
    assert Version.parse("0.9.0") < Version.parse("1.0.0-alpha")


def test_build_metadata_and_the_v_prefix_are_ignored_by_comparison():
    assert Version.parse("v1.2.3") == Version.parse("1.2.3")
    assert Version.parse("1.2.3+build.7") == Version.parse("1.2.3")
    assert hash(Version.parse("1.2.3+a")) == hash(Version.parse("1.2.3+b"))
    assert str(Version.parse("v1.2.3-beta.1+sha.5")) == "1.2.3-beta.1+sha.5"
    assert Version.parse("2.0.0-rc.1").core == "2.0.0"


def test_prerelease_flag():
    assert Version.parse("1.0.0-beta").is_prerelease
    assert not Version.parse("1.0.0").is_prerelease
    assert not Version.parse("1.0.0+build").is_prerelease


@pytest.mark.parametrize(
    "text",
    ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03", "1.2.3-", "1.2.3-01", "1.2.3-beta..1", "1.2.3+",
     " 1.2.3", "1.2.3 ", "V1.2.3", "vv1.2.3", "1.2.x", "-1.2.3", "1.2.3-bêta", "latest", "1" * 200 + ".0.0"],
)
def test_malformed_versions_are_refused_not_guessed(text):
    with pytest.raises(InvalidVersion):
        Version.parse(text)
    assert Version.try_parse(text) is None


def test_try_parse_refuses_non_strings():
    assert Version.try_parse(None) is None
    assert Version.try_parse(1.2) is None
    assert Version.try_parse(["1.2.3"]) is None


def test_comparing_with_something_else_is_not_supported():
    with pytest.raises(TypeError):
        _ = Version.parse("1.0.0") < "1.0.1"

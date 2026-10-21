"""Dupliquer une séquence garde son parentage, ses groupes et ses liaisons de tracking dans la copie.

Régression : les clips de la copie reçoivent de nouveaux identifiants, mais seuls les transitions étaient
réécrits. Le parentage et le groupe d'un calque, comme la source d'une liaison de tracking, pointaient encore
vers les clips de l'original : le parentage disparaissait en silence et la liaison donnait « source introuvable ».
"""

from __future__ import annotations

from rich_project import build_rich_project

from core.sequences import duplicate_sequence


def _clips(sequence):
    return [clip for track in sequence.tracks for clip in track.clips]


def _references(sequence) -> dict[str, set[str]]:
    clips = _clips(sequence)
    parents = {c.graphic.parent_id for c in clips if c.graphic is not None and c.graphic.parent_id}
    groups = {c.graphic.group_id for c in clips if c.graphic is not None and c.graphic.group_id}
    links = {link.source_clip_id for c in clips if c.tracking is not None for link in c.tracking.links
             if link.source_clip_id}
    return {"parent": parents, "group": groups, "link": links}


def test_a_duplicated_sequence_keeps_its_references_inside_the_copy():
    project = build_rich_project()
    source = project.active_sequence
    before = _references(source)
    assert all(before.values()), f"le projet de test doit porter un parentage, un groupe et une liaison : {before}"

    clone = duplicate_sequence(project, source.id)
    source_ids = {c.id for c in _clips(source)}
    clone_ids = {c.id for c in _clips(clone)}
    assert clone_ids.isdisjoint(source_ids)
    for kind, targets in _references(clone).items():
        assert targets and targets <= clone_ids, f"{kind} : la copie pointe hors d'elle-même ({targets - clone_ids})"


def test_duplicating_never_touches_the_original():
    project = build_rich_project()
    source = project.active_sequence
    before = _references(source)
    duplicate_sequence(project, source.id)
    assert _references(source) == before
    source_ids = {c.id for c in _clips(source)}
    assert all(targets <= source_ids for targets in _references(source).values())

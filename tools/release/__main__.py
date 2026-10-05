"""``python -m tools.release <commande>`` : les étapes du workflow de publication, exécutables à la main.

Commandes :

* ``check-tag --ref REF`` : compare le tag à ``core/app_version.py`` ; écrit ``version``, ``prerelease``,
  ``publish`` dans ``$GITHUB_OUTPUT`` ;
* ``check-target --expect macos-arm64`` : la machine de construction est-elle la cible attendue ?
* ``archive --dist dist --out release-assets`` : archive conventionnelle de l'application construite ;
* ``checksums DOSSIER`` : écrit ``SHA256SUMS.txt`` ;
* ``verify DOSSIER`` : refuse une release incomplète ou incohérente (code de sortie 1) ;
* ``notes --signing notarized|signed|adhoc [--generated FICHIER] --out FICHIER`` : notes de release.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.app_version import APP_VERSION
from core.release_assets import detect_target

from .packaging import (
    SIGNING_STATUSES,
    ReleaseError,
    check_tag,
    create_archive,
    expect_target,
    release_notes,
    safe_output_value,
    verify_release_directory,
    write_checksums,
    write_github_output,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.release", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    tag = commands.add_parser("check-tag")
    tag.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    target = commands.add_parser("check-target")
    target.add_argument("--expect", required=True)
    archive = commands.add_parser("archive")
    archive.add_argument("--dist", type=Path, default=Path("dist"))
    archive.add_argument("--out", type=Path, default=Path("release-assets"))
    archive.add_argument("--version", default=APP_VERSION)
    checksums = commands.add_parser("checksums")
    checksums.add_argument("directory", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--version", default=APP_VERSION)
    notes = commands.add_parser("notes")
    notes.add_argument("--signing", choices=SIGNING_STATUSES, required=True)
    notes.add_argument("--version", default=APP_VERSION)
    notes.add_argument("--generated", type=Path)
    notes.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check-tag":
            result = check_tag(args.ref)
            write_github_output(os.environ.get("GITHUB_OUTPUT"), {
                "version": safe_output_value(result.version),
                "prerelease": "true" if result.prerelease else "false",
                "publish": "true" if result.publish else "false",
            })
            mode = "publication" if result.publish else "construction d'essai (aucune publication)"
            print(f"Version {result.version} : {mode}", file=sys.stderr)
        elif args.command == "check-target":
            print(expect_target(args.expect, detect_target()).slug)
        elif args.command == "archive":
            target = detect_target()
            if target is None:
                raise ReleaseError("système ou architecture sans paquet prévu")
            print(create_archive(args.dist, args.out, args.version, target))
        elif args.command == "checksums":
            path = write_checksums(args.directory)
            print(path.read_text(encoding="utf-8"), end="")
        elif args.command == "verify":
            problems = verify_release_directory(args.directory, args.version)
            for problem in problems:
                print(f"::error::{problem}")
            if problems:
                return 1
            print(f"Release {args.version} conforme : {args.directory}")
        elif args.command == "notes":
            generated = args.generated.read_text(encoding="utf-8") if args.generated else ""
            args.out.write_text(release_notes(args.version, args.signing, generated), encoding="utf-8")
    except ReleaseError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

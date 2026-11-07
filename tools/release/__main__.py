"""``python -m tools.release <commande>`` : les étapes du workflow de publication, exécutables à la main.

Commandes :

* ``check-tag --ref REF`` : compare le tag à ``core/app_version.py`` ; écrit ``version``, ``prerelease``,
  ``publish`` dans ``$GITHUB_OUTPUT`` ;
* ``check-target --expect macos-arm64`` : la machine de construction est-elle la cible attendue ?
* ``archive --dist dist --out release-assets`` : archive conventionnelle de l'application construite ;
* ``checksums DOSSIER`` : écrit ``SHA256SUMS.txt`` ;
* ``verify DOSSIER`` : refuse une release incomplète ou incohérente (code de sortie 1) ;
* ``sign DOSSIER --status-file FICHIER`` : signe ``SHA256SUMS.txt`` (Ed25519) avec le secret ``UPDATE_SIGNING_KEY`` de
  l'environnement ; sans secret, release non signée (le dit en clair) ; avec secret, toute anomalie est une erreur ;
* ``gen-update-key --out FICHIER`` : à lancer **par le mainteneur** ; crée la clé de signature (jamais affichée) et
  affiche la clé publique à coller dans ``core/update_keys.py`` (ADR-0012) ;
* ``notes --signing notarized|signed|adhoc --update-signature "signed <clé>|unsigned" [--generated FICHIER] --out
  FICHIER`` : notes de release.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.app_version import APP_VERSION
from core.release_assets import SIGNATURE_FILE, detect_target

from core.update_signature import parse_signature_file

from .signing import UPDATE_SIGNATURE_STATUSES, generate_update_key, key_instructions, sign_release_directory
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


def verify_key_id(directory: Path) -> str:
    """Identifiant de la clé annoncé par le fichier de signature qu'on vient de produire."""
    key_id, _signature = parse_signature_file((directory / SIGNATURE_FILE).read_bytes())
    return key_id


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
    sign = commands.add_parser("sign")
    sign.add_argument("directory", type=Path)
    sign.add_argument("--status-file", type=Path, required=True)
    genkey = commands.add_parser("gen-update-key")
    genkey.add_argument("--out", type=Path, required=True,
                        help="fichier de la graine secrète (créé en 600, jamais écrasé), p. ex. kut-studio.update-key")
    notes = commands.add_parser("notes")
    notes.add_argument("--signing", choices=SIGNING_STATUSES, required=True)
    notes.add_argument("--update-signature", required=True, metavar="STATE",
                       help='contenu de signing-status/update-signature.txt : "signed <identifiant de clé>" ou "unsigned"')
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
        elif args.command == "sign":
            state = sign_release_directory(args.directory)
            if state == "signed":
                key_id = verify_key_id(args.directory)
                args.status_file.parent.mkdir(parents=True, exist_ok=True)
                args.status_file.write_text(f"signed {key_id}\n", encoding="utf-8")
                print(f"Mises à jour signées (clé {key_id}) : {args.directory / SIGNATURE_FILE}", file=sys.stderr)
            else:
                args.status_file.parent.mkdir(parents=True, exist_ok=True)
                args.status_file.write_text("unsigned\n", encoding="utf-8")
                message = ("secret UPDATE_SIGNING_KEY absent : release produite SANS signature, mises à jour "
                           "automatiques indisponibles pour cette version")
                print(f"::warning::{message}")
                summary = os.environ.get("GITHUB_STEP_SUMMARY")
                if summary:
                    with open(summary, "a", encoding="utf-8") as stream:
                        stream.write(f"**Mises à jour automatiques indisponibles pour cette version** : {message}.\n")
        elif args.command == "gen-update-key":
            public = generate_update_key(args.out)
            print(key_instructions(public, args.out))
        elif args.command == "notes":
            generated = args.generated.read_text(encoding="utf-8") if args.generated else ""
            state, _, key_id = args.update_signature.strip().partition(" ")
            if state not in UPDATE_SIGNATURE_STATUSES or (state == "signed") != bool(key_id):
                raise ReleaseError(f"état de signature des mises à jour illisible : {args.update_signature!r}")
            args.out.write_text(release_notes(args.version, args.signing, generated, update_signature=state,
                                              update_key_id=key_id.strip()), encoding="utf-8")
    except ReleaseError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

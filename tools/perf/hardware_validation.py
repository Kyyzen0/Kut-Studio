"""Diagnostic local : chaque encodeur de cette machine produit-il un export juste (balises et couleurs) ?

Usage::

    python -m tools.perf.hardware_validation                    # témoin CPU + tous les backends
    python -m tools.perf.hardware_validation --encoder videotoolbox --keep
    python -m tools.perf.hardware_validation --json rapport.json --timeout 120
    KUT_STUDIO_REQUIRE_HARDWARE=nvenc python -m tools.perf.hardware_validation   # absence = échec

Pour le témoin logiciel puis chaque backend matériel (VideoToolbox, NVENC, Quick Sync, AMF, VAAPI) :
mini export de 1 s en 320×180 avec la **chaîne d'export de l'application**, relu avec ``ffprobe``
(matrice, primaires, transfert, plage), puis une image décodée en BT.709 dont trois bandes de couleur
connue doivent revenir à leur valeur. Le rapport dit aussi ce qui a été détecté (encodeurs et
décodeurs, validés ou non) et ce que ferait l'application si l'encodeur échouait (repli CPU).

Code de sortie : ``0`` aucun échec (des backends absents sont « sautés », jamais « réussis ») ;
``1`` un backend disponible ou exigé a échoué ; ``2`` validation impossible (FFmpeg ou ffprobe
introuvable, valeur de ``KUT_STUDIO_REQUIRE_HARDWARE`` inconnue, source de test impossible).

Fonctionne sans aucun GPU : le rapport le dit et seul le témoin CPU est exporté. La logique est dans
:mod:`core.hardware_validation` (partagée avec les tests).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hardware_cache import default_service  # noqa: E402
from core.hardware_validation import (  # noqa: E402
    REQUIRE_VARIABLE,
    RUN_TIMEOUT_SECONDS,
    format_report,
    media_tools,
    parse_backend_name,
    required_backends,
    run_validation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.perf.hardware_validation",
        description="Mini export par encodeur avec la chaîne de l'application, relu avec ffprobe "
                    "(balises de couleur BT.709 et couleurs décodées).",
    )
    parser.add_argument(
        "--encoder", action="append", metavar="NOM", default=[],
        help="backend (cpu, videotoolbox, nvenc, qsv, amf, vaapi) ou encodeur FFmpeg (h264_nvenc…) ; "
             "répétable. Par défaut : tous. Le témoin CPU est toujours exporté.",
    )
    parser.add_argument("--keep", action="store_true", help="conserver la source et les fichiers exportés")
    parser.add_argument("--timeout", type=float, default=RUN_TIMEOUT_SECONDS,
                        help=f"délai par commande FFmpeg, en secondes (défaut : {RUN_TIMEOUT_SECONDS:g})")
    parser.add_argument("--json", metavar="CHEMIN", help="écrire aussi le rapport en JSON")
    parser.add_argument("--rescan", action="store_true",
                        help="refaire la détection des capacités (après une mise à jour de pilote)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        backends = [parse_backend_name(name) for name in args.encoder]
    except ValueError as error:
        parser.error(str(error))
    try:
        required = required_backends()
    except ValueError as error:
        print(f"Erreur : {error}")
        return 2
    tools = media_tools()
    if tools is None:
        print("Erreur : FFmpeg ou ffprobe introuvable : aucune validation possible "
              f"(voir KUT_STUDIO_FFMPEG_DIR). {REQUIRE_VARIABLE} n'y change rien.")
        return 2

    service = default_service()  # la détection de l'application, avec son cache
    capabilities = service.rescan() if args.rescan else service.capabilities()
    workdir = Path(tempfile.mkdtemp(prefix="kut-hardware-validation-"))
    try:
        report = run_validation(capabilities, tools=tools, workdir=workdir, backends=backends or None,
                                required=required, timeout=args.timeout)
        print(format_report(report, workdir=str(workdir) if args.keep else ""))
        if args.json:
            Path(args.json).write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

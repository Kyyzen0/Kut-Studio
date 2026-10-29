import os
import sys

from core.diagnostics_log import install_diagnostics
from core.process_supervisor import helper_mode_exit_code
from core.tool_paths import extend_search_path, log_tool_search


def cli_requested(argv):
    """``True`` si ``argv`` demande la ligne de commande. Jamais dans la version Mac App Store (ADR-0013, étape 8) : le
    module ``core.cli`` n'y est pas livré (ni le mode script), donc il n'est même pas importé."""
    from core.build_flavor import is_store

    if is_store():
        return False
    from core.cli import is_cli_invocation

    return is_cli_invocation(argv)


def main():
    # Processus auxiliaire de la supervision des enfants (gardien, enfant d'auto-contrôle) : l'application se
    # relance elle-même avec un argument dédié, en source comme gelée. Traité avant le journal et avant Qt.
    helper = helper_mode_exit_code(sys.argv)
    if helper is not None:
        return helper
    # Ligne de commande (« Kut-Studio info | validate | presets | export ») : le même binaire, sans fenêtre, traitée avant Qt et
    # avant la fenêtre. FFmpeg (Homebrew…) se cherche avec le PATH complet, comme pour l'interface.
    if cli_requested(sys.argv):
        extend_search_path()
        from core.cli import main as cli_main

        return cli_main(sys.argv[1:])
    # Lancée depuis le Dock ou le Finder, l'application n'a pas le PATH du shell : FFmpeg (Homebrew…) resterait
    # introuvable. À faire avant les imports de l'interface, dont certains résolvent FFmpeg dès le chargement.
    added_dirs = extend_search_path()
    # Avant toute autre chose : une exception, même au chargement de Qt ou de l'interface, doit laisser une trace
    # dans le journal. Qt et la fenêtre ne sont donc importés qu'ici, une fois le journal installé.
    install_diagnostics()
    # Le journal n'existait pas encore au moment de la recherche : on la consigne maintenant (aucun sous-processus).
    log_tool_search(added_dirs)
    # FFmpeg laissés par une instance morte que ni son gardien ni son objet Job n'ont pu arrêter.
    from core.process_supervisor import sweep_dead_instances

    sweep_dead_instances()
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    if "--smoke-test" in sys.argv:
        # Le smoke test (CI, application construite) ne contacte jamais GitHub.
        os.environ["KUT_STUDIO_UPDATE_CHECK"] = "off"
    app = QApplication(sys.argv)
    # Polices embarquées (templates, presets verticaux) : disponibles dans les sélecteurs avant la première fenêtre.
    from core.bundled_fonts import register_bundled_fonts

    register_bundled_fonts()
    window = MainWindow()
    window.show()
    # Projet confié par le système : Finder (macOS), double-clic ou « Ouvrir avec » (Windows, Linux).
    from ui.external_open import install_external_open, project_path_from_arguments

    install_external_open(app, window.open_external_project)
    startup_project = project_path_from_arguments(sys.argv[1:])
    if startup_project is not None:
        window.open_external_project(startup_project)
    if "--smoke-test" in sys.argv:
        app.processEvents()
        window.close()
        app.processEvents()
        # Le tracking dépend de numpy (extensions C) : vérifié dans l'application construite.
        from core.tracking_match import self_check

        problem = self_check()
        if problem:
            print(f"Smoke test : tracking indisponible — {problem}", file=sys.stderr)
            return 1
        # Synchronisation audio du Multicam : même dépendance (numpy), contrôle sur des signaux synthétiques.
        from core.audio_sync import self_check as audio_sync_check

        problem = audio_sync_check()
        if problem:
            print(f"Smoke test : synchronisation audio indisponible — {problem}", file=sys.stderr)
            return 1
        # Flux optique (images intermédiaires) : même dépendance (numpy), contrôle sur un décalage connu.
        from core.optical_flow import self_check as optical_flow_check

        problem = optical_flow_check()
        if problem:
            print(f"Smoke test : flux optique indisponible — {problem}", file=sys.stderr)
            return 1
        # Aperçu GPU : les shaders compilés doivent être embarqués et lisibles.
        from ui.gpu_preview import gpu_self_check

        problem = gpu_self_check()
        if problem:
            print(f"Smoke test : aperçu GPU indisponible — {problem}", file=sys.stderr)
            return 1
        # Kut-3D : le paquet kut3d, ses shaders et un rendu numpy d'un triangle (sans sonde GPU en sous-processus).
        from core.kut3d_check import kut3d_self_check

        problem = kut3d_self_check()
        if problem:
            print(f"Smoke test : Kut-3D indisponible — {problem}", file=sys.stderr)
            return 1
        # Mises à jour (absentes de la version Store, ADR-0013) : l'application construite doit pouvoir parler HTTPS
        # (moteur TLS de Qt embarqué) et vérifier les signatures (Ed25519 en Python pur, ADR-0012).
        from core.build_flavor import is_store

        if not is_store():
            from core.update_service import tls_self_check

            problem = tls_self_check()
            if problem:
                print(f"Smoke test : HTTPS indisponible — {problem}", file=sys.stderr)
                return 1
            from core.update_signature import self_check as update_signature_check

            problem = update_signature_check()
            if problem:
                print(f"Smoke test : vérification des signatures de mise à jour indisponible — {problem}", file=sys.stderr)
                return 1
        # Supervision des FFmpeg : gardien (macOS, Linux) ou objet Job (Windows) réellement déclenché.
        from core.process_supervisor import supervision_self_check

        problem = supervision_self_check()
        if problem:
            print(f"Smoke test : supervision des processus indisponible — {problem}", file=sys.stderr)
            return 1
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

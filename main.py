import os
import sys

from core.diagnostics_log import install_diagnostics
from core.process_supervisor import helper_mode_exit_code
from core.tool_paths import extend_search_path


def main():
    # Processus auxiliaire de la supervision des enfants (gardien, enfant d'auto-contrôle) : l'application se
    # relance elle-même avec un argument dédié, en source comme gelée. Traité avant le journal et avant Qt.
    helper = helper_mode_exit_code(sys.argv)
    if helper is not None:
        return helper
    # Lancée depuis le Dock ou le Finder, l'application n'a pas le PATH du shell : FFmpeg (Homebrew…) resterait
    # introuvable. À faire avant les imports de l'interface, dont certains résolvent FFmpeg dès le chargement.
    extend_search_path()
    # Avant toute autre chose : une exception, même au chargement de Qt ou de l'interface, doit laisser une trace
    # dans le journal. Qt et la fenêtre ne sont donc importés qu'ici, une fois le journal installé.
    install_diagnostics()
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
        # Mises à jour : l'application construite doit pouvoir parler HTTPS (moteur TLS de Qt embarqué).
        from core.update_service import tls_self_check

        problem = tls_self_check()
        if problem:
            print(f"Smoke test : HTTPS indisponible — {problem}", file=sys.stderr)
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

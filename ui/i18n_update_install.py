"""Traductions de l'installation automatique macOS (fusionnées dans ``ui.i18n_updates``).

Clés ``update.auto.*`` (parcours dans le dialogue) et ``update.outcome.*`` (message du démarrage suivant).
"""

from __future__ import annotations

UPDATE_INSTALL_TRANSLATIONS: dict[str, dict[str, str]] = {
    "update.button.install_restart": {
        "fr": "Installer et redémarrer",
        "en": "Install and restart",
        "es": "Instalar y reiniciar",
    },
    "update.auto.description": {
        "fr": "Kut-Studio va se fermer, se mettre à jour et se rouvrir. Vos projets et préférences ne sont pas "
              "touchés.\n\nSi la nouvelle version ne démarre pas, la version précédente est remise en place "
              "automatiquement.",
        "en": "Kut-Studio will close, update and reopen. Your projects and preferences are not touched.\n\n"
              "If the new version does not start, the previous version is put back automatically.",
        "es": "Kut-Studio se cerrará, se actualizará y se volverá a abrir. Sus proyectos y preferencias no se "
              "tocan.\n\nSi la nueva versión no arranca, la versión anterior se restaura automáticamente.",
    },
    "update.auto.confirm.title": {
        "fr": "Installer et redémarrer",
        "en": "Install and restart",
        "es": "Instalar y reiniciar",
    },
    "update.auto.confirm.text": {
        "fr": "Kut-Studio va se fermer, se mettre à jour et se rouvrir. Vos projets et préférences ne sont pas "
              "touchés.\n\nUn projet non enregistré vous sera proposé à l'enregistrement, et un rendu en cours "
              "demandera confirmation. Si vous annulez, rien n'est modifié.\n\nSi la nouvelle version ne démarre "
              "pas, la version précédente est remise en place. Continuer ?",
        "en": "Kut-Studio will close, update and reopen. Your projects and preferences are not touched.\n\n"
              "You will be offered to save an unsaved project, and a render in progress will ask for "
              "confirmation. If you cancel, nothing is changed.\n\nIf the new version does not start, the "
              "previous version is put back. Continue?",
        "es": "Kut-Studio se cerrará, se actualizará y se volverá a abrir. Sus proyectos y preferencias no se "
              "tocan.\n\nSe le propondrá guardar un proyecto sin guardar, y un renderizado en curso pedirá "
              "confirmación. Si cancela, no se cambia nada.\n\nSi la nueva versión no arranca, se restaura la "
              "versión anterior. ¿Continuar?",
    },
    "update.auto.preparing.title": {
        "fr": "Préparation de Kut-Studio {version}…",
        "en": "Preparing Kut-Studio {version}…",
        "es": "Preparando Kut-Studio {version}…",
    },
    "update.auto.preparing.text": {
        "fr": "La nouvelle application est extraite et vérifiée à côté de l'actuelle. Rien n'est remplacé pour le "
              "moment ; vous pouvez annuler.",
        "en": "The new application is being extracted and checked next to the current one. Nothing is replaced "
              "yet; you can cancel.",
        "es": "La nueva aplicación se extrae y se comprueba junto a la actual. Todavía no se reemplaza nada; "
              "puede cancelar.",
    },
    "update.auto.preparing.progress": {
        "fr": "Extraction et vérification en cours…",
        "en": "Extracting and checking…",
        "es": "Extrayendo y comprobando…",
    },
    "update.auto.progress.name": {
        "fr": "Préparation de la mise à jour",
        "en": "Preparing the update",
        "es": "Preparación de la actualización",
    },
    "update.auto.unavailable": {
        "fr": "Installation automatique indisponible : {reason}. Suivez les étapes ci-dessus.",
        "en": "Automatic installation is unavailable: {reason}. Follow the steps above.",
        "es": "La instalación automática no está disponible: {reason}. Siga los pasos anteriores.",
    },
    "update.auto.failed": {
        "fr": "La préparation de la mise à jour a échoué : {problem}. Rien n'a été modifié ; installez la nouvelle "
              "version à la main, comme indiqué ci-dessus.",
        "en": "Preparing the update failed: {problem}. Nothing was changed; install the new version by hand, as "
              "described above.",
        "es": "La preparación de la actualización ha fallado: {problem}. No se ha cambiado nada; instale la nueva "
              "versión a mano, como se indica arriba.",
    },
    "update.auto.launch_failed": {
        "fr": "L'assistant d'installation n'a pas pu être lancé : le dossier du paquet vérifié est ouvert pour "
              "l'installation à la main.",
        "en": "The installation helper could not be started: the folder of the verified package is open for a "
              "manual installation.",
        "es": "No se pudo iniciar el asistente de instalación: se abre la carpeta del paquete verificado para la "
              "instalación manual.",
    },
    # --- Pourquoi l'installation automatique n'est pas proposée ------------------------------------------------
    "update.auto.reason.source": {
        "fr": "exécution depuis les sources", "en": "running from source", "es": "ejecución desde el código fuente",
    },
    "update.auto.reason.not_macos": {
        "fr": "réservée à l'application macOS", "en": "only for the macOS application",
        "es": "solo para la aplicación de macOS",
    },
    "update.auto.reason.not_bundle": {
        "fr": "l'application n'est pas un Kut-Studio.app reconnu", "en": "the application is not a recognised "
        "Kut-Studio.app", "es": "la aplicación no es un Kut-Studio.app reconocido",
    },
    "update.auto.reason.translocated": {
        "fr": "macOS exécute cette copie depuis un emplacement temporaire (déplacez Kut-Studio dans Applications)",
        "en": "macOS runs this copy from a temporary location (move Kut-Studio to Applications)",
        "es": "macOS ejecuta esta copia desde una ubicación temporal (mueva Kut-Studio a Aplicaciones)",
    },
    "update.auto.reason.missing_bundle": {
        "fr": "le bundle de l'application est introuvable", "en": "the application bundle cannot be found",
        "es": "no se encuentra el paquete de la aplicación",
    },
    "update.auto.reason.read_only_volume": {
        "fr": "l'application est sur un volume en lecture seule", "en": "the application is on a read-only volume",
        "es": "la aplicación está en un volumen de solo lectura",
    },
    "update.auto.reason.parent_not_writable": {
        "fr": "vous n'avez pas le droit d'écrire dans le dossier de l'application",
        "en": "you cannot write in the application's folder",
        "es": "no tiene permiso para escribir en la carpeta de la aplicación",
    },
    "update.auto.reason.bundle_not_writable": {
        "fr": "vous n'avez pas le droit de remplacer l'application", "en": "you cannot replace the application",
        "es": "no tiene permiso para reemplazar la aplicación",
    },
    "update.auto.reason.not_verified": {
        "fr": "la signature de cette version n'est pas vérifiée",
        "en": "the signature of this version is not verified",
        "es": "la firma de esta versión no está verificada",
    },
    # --- Pourquoi la préparation a échoué ------------------------------------------------------------------------
    "update.auto.problem.tool": {
        "fr": "un outil du système est introuvable ou n'a pas répondu", "en": "a system tool is missing or did not "
        "answer", "es": "una herramienta del sistema falta o no respondió",
    },
    "update.auto.problem.archive": {
        "fr": "l'archive est illisible ou contient autre chose que Kut-Studio.app",
        "en": "the archive is unreadable or contains something other than Kut-Studio.app",
        "es": "el archivo es ilegible o contiene algo distinto de Kut-Studio.app",
    },
    "update.auto.problem.disk": {
        "fr": "pas assez d'espace libre à côté de l'application", "en": "not enough free space next to the "
        "application", "es": "no hay espacio libre suficiente junto a la aplicación",
    },
    "update.auto.problem.bundle": {
        "fr": "la nouvelle application est incomplète", "en": "the new application is incomplete",
        "es": "la nueva aplicación está incompleta",
    },
    "update.auto.problem.identifier": {
        "fr": "ce n'est pas la même application (identifiant différent)",
        "en": "it is not the same application (different identifier)",
        "es": "no es la misma aplicación (identificador distinto)",
    },
    "update.auto.problem.version": {
        "fr": "la version contenue n'est pas celle annoncée", "en": "the contained version is not the announced one",
        "es": "la versión contenida no es la anunciada",
    },
    "update.auto.problem.not_newer": {
        "fr": "la version contenue n'est pas plus récente que celle installée",
        "en": "the contained version is not newer than the installed one",
        "es": "la versión contenida no es más reciente que la instalada",
    },
    "update.auto.problem.architecture": {
        "fr": "l'application n'est pas compatible avec ce Mac", "en": "the application is not compatible with this "
        "Mac", "es": "la aplicación no es compatible con este Mac",
    },
    "update.auto.problem.signature": {
        "fr": "la signature de la nouvelle application est invalide", "en": "the signature of the new application "
        "is invalid", "es": "la firma de la nueva aplicación no es válida",
    },
    "update.auto.problem.gatekeeper": {
        "fr": "macOS (Gatekeeper) n'accepte pas la nouvelle application", "en": "macOS (Gatekeeper) does not "
        "accept the new application", "es": "macOS (Gatekeeper) no acepta la nueva aplicación",
    },
    "update.auto.problem.team": {
        "fr": "la nouvelle application n'est pas signée par le même éditeur", "en": "the new application is not "
        "signed by the same publisher", "es": "la nueva aplicación no está firmada por el mismo editor",
    },
    "update.auto.problem.not_verified": {
        "fr": "la signature de la version n'est pas vérifiée", "en": "the version's signature is not verified",
        "es": "la firma de la versión no está verificada",
    },
    # --- Message du démarrage suivant -----------------------------------------------------------------------------
    "update.outcome.ok.title": {
        "fr": "Mise à jour installée", "en": "Update installed", "es": "Actualización instalada",
    },
    "update.outcome.ok.text": {
        "fr": "Mise à jour installée : {version}. Vos projets et préférences sont conservés.",
        "en": "Update installed: {version}. Your projects and preferences are kept.",
        "es": "Actualización instalada: {version}. Sus proyectos y preferencias se conservan.",
    },
    "update.outcome.notes": {"fr": "Notes de version", "en": "Release notes", "es": "Notas de la versión"},
    "update.outcome.log": {"fr": "Ouvrir le journal", "en": "Open the log", "es": "Abrir el registro"},
    "update.outcome.failed.title": {
        "fr": "La mise à jour a échoué", "en": "The update failed", "es": "La actualización ha fallado",
    },
    "update.outcome.restored.text": {
        "fr": "La mise à jour vers {version} a échoué ({reason}). La version précédente a été restaurée.",
        "en": "The update to {version} failed ({reason}). The previous version has been restored.",
        "es": "La actualización a {version} ha fallado ({reason}). Se ha restaurado la versión anterior.",
    },
    "update.outcome.kept.text": {
        "fr": "La mise à jour vers {version} n'a pas été installée ({reason}). La version actuelle est conservée.",
        "en": "The update to {version} was not installed ({reason}). The current version is kept.",
        "es": "La actualización a {version} no se instaló ({reason}). Se conserva la versión actual.",
    },
    "update.outcome.rollback_failed.text": {
        "fr": "La mise à jour vers {version} a échoué et la version précédente n'a pas pu être restaurée "
              "automatiquement. Elle se trouve à côté de l'application, dans un dossier caché nommé "
              "« .Kut-Studio.app.previous-… » : renommez-la en « Kut-Studio.app ».",
        "en": "The update to {version} failed and the previous version could not be restored automatically. It is "
              "next to the application, in a hidden folder named “.Kut-Studio.app.previous-…”: rename it to "
              "“Kut-Studio.app”.",
        "es": "La actualización a {version} ha fallado y no se pudo restaurar automáticamente la versión anterior. "
              "Está junto a la aplicación, en una carpeta oculta llamada «.Kut-Studio.app.previous-…»: "
              "renómbrela a «Kut-Studio.app».",
    },
    "update.outcome.recovered.text": {
        "fr": "Une installation interrompue a été réparée : la version précédente de Kut-Studio a été restaurée.",
        "en": "An interrupted installation has been repaired: the previous version of Kut-Studio has been restored.",
        "es": "Se ha reparado una instalación interrumpida: se ha restaurado la versión anterior de Kut-Studio.",
    },
    "update.outcome.reason.wait_timeout": {
        "fr": "Kut-Studio ne s'est pas fermé à temps", "en": "Kut-Studio did not close in time",
        "es": "Kut-Studio no se cerró a tiempo",
    },
    "update.outcome.reason.swap_failed": {
        "fr": "le remplacement a échoué", "en": "the replacement failed", "es": "el reemplazo falló",
    },
    "update.outcome.reason.staging_invalid": {
        "fr": "la nouvelle application préparée était invalide", "en": "the prepared new application was invalid",
        "es": "la nueva aplicación preparada no era válida",
    },
    "update.outcome.reason.unexpected_state": {
        "fr": "état des dossiers inattendu", "en": "unexpected folder state", "es": "estado de carpetas inesperado",
    },
    "update.outcome.reason.start_timeout": {
        "fr": "la nouvelle version n'a pas démarré", "en": "the new version did not start",
        "es": "la nueva versión no arrancó",
    },
    "update.outcome.reason.crashed_after_start": {
        "fr": "la nouvelle version s'est arrêtée juste après son démarrage", "en": "the new version stopped right "
        "after starting", "es": "la nueva versión se detuvo justo después de arrancar",
    },
    "update.outcome.reason.started_marker_invalid": {
        "fr": "la nouvelle version n'a pas confirmé son démarrage", "en": "the new version did not confirm that it "
        "started", "es": "la nueva versión no confirmó su arranque",
    },
    "update.outcome.reason.open_failed": {
        "fr": "macOS n'a pas pu ouvrir la nouvelle version", "en": "macOS could not open the new version",
        "es": "macOS no pudo abrir la nueva versión",
    },
    "update.outcome.reason.signal": {
        "fr": "l'installation a été interrompue", "en": "the installation was interrupted",
        "es": "la instalación se interrumpió",
    },
    "update.outcome.reason.interrupted": {
        "fr": "l'installation a été interrompue", "en": "the installation was interrupted",
        "es": "la instalación se interrumpió",
    },
    "update.outcome.reason.rollback_failed": {
        "fr": "le retour arrière a échoué", "en": "the rollback failed", "es": "la restauración falló",
    },
}

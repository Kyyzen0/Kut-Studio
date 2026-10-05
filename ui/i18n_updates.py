"""Traductions du domaine « mises à jour » : recherche, téléchargement vérifié, installation assistée, À propos.

Fusionnées dans :mod:`ui.i18n` par ``DOMAIN_TABLES`` (une clé définie deux fois est refusée à l'import). Les erreurs
de :mod:`core.updates` se traduisent par leur genre : ``update.error.<UpdateErrorKind>``.
"""

from __future__ import annotations

UPDATES_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- Menu et barre supérieure -------------------------------------------------------------------------------
    "update.menu.check": {
        "fr": "Rechercher des mises à jour…",
        "en": "Check for updates…",
        "es": "Buscar actualizaciones…",
    },
    "update.menu.about": {"fr": "À propos de Kut-Studio", "en": "About Kut-Studio", "es": "Acerca de Kut-Studio"},
    "update.notice": {"fr": "Mise à jour {version}", "en": "Update {version}", "es": "Actualización {version}"},
    "update.notice.tooltip": {
        "fr": "Kut-Studio {version} est disponible : voir les nouveautés et le téléchargement",
        "en": "Kut-Studio {version} is available: see what's new and download it",
        "es": "Kut-Studio {version} está disponible: ver las novedades y la descarga",
    },
    "update.dialog.title": {
        "fr": "Mises à jour de Kut-Studio",
        "en": "Kut-Studio updates",
        "es": "Actualizaciones de Kut-Studio",
    },
    # --- Recherche ----------------------------------------------------------------------------------------------
    "update.checking.title": {
        "fr": "Recherche de mises à jour…",
        "en": "Checking for updates…",
        "es": "Buscando actualizaciones…",
    },
    "update.checking.text": {
        "fr": "Kut-Studio consulte les versions publiées sur GitHub. Vous pouvez continuer à travailler.",
        "en": "Kut-Studio is reading the versions published on GitHub. You can keep working.",
        "es": "Kut-Studio consulta las versiones publicadas en GitHub. Puede seguir trabajando.",
    },
    "update.up_to_date.title": {
        "fr": "Kut-Studio est à jour",
        "en": "Kut-Studio is up to date",
        "es": "Kut-Studio está actualizado",
    },
    "update.up_to_date.text": {
        "fr": "La version {version} est la plus récente publiée.",
        "en": "Version {version} is the latest published release.",
        "es": "La versión {version} es la más reciente publicada.",
    },
    "update.up_to_date.prerelease_hidden": {
        "fr": "La préversion {version} existe, mais les préversions ne sont pas proposées : activez-les dans "
              "Préférences › Général › Mises à jour.",
        "en": "Pre-release {version} exists, but pre-releases are not offered: enable them in "
              "Preferences › General › Updates.",
        "es": "Existe la versión preliminar {version}, pero no se ofrecen versiones preliminares: actívelas en "
              "Preferencias › General › Actualizaciones.",
    },
    # --- Version disponible -------------------------------------------------------------------------------------
    "update.available.title": {
        "fr": "Kut-Studio {version} est disponible",
        "en": "Kut-Studio {version} is available",
        "es": "Kut-Studio {version} está disponible",
    },
    "update.available.text": {
        "fr": "Vous utilisez la version {current}.",
        "en": "You are using version {current}.",
        "es": "Está usando la versión {current}.",
    },
    "update.available.published": {
        "fr": "Publiée le {date}.",
        "en": "Published on {date}.",
        "es": "Publicada el {date}.",
    },
    "update.available.prerelease": {
        "fr": "C'est une préversion : elle peut contenir des défauts.",
        "en": "This is a pre-release: it may contain bugs.",
        "es": "Es una versión preliminar: puede contener errores.",
    },
    "update.available.skipped": {
        "fr": "Vous aviez choisi d'ignorer cette version.",
        "en": "You had chosen to skip this version.",
        "es": "Había elegido omitir esta versión.",
    },
    "update.notes.title": {"fr": "Notes de publication", "en": "Release notes", "es": "Notas de la versión"},
    "update.notes.empty": {
        "fr": "Cette version n'a pas de notes de publication.",
        "en": "This release has no release notes.",
        "es": "Esta versión no tiene notas.",
    },
    "update.package": {
        "fr": "Paquet pour {platform} : {name} ({size})",
        "en": "Package for {platform}: {name} ({size})",
        "es": "Paquete para {platform}: {name} ({size})",
    },
    "update.package.missing": {
        "fr": "Cette version ne contient pas de paquet pour {platform}. Aucun autre paquet n'est proposé : il ne "
              "fonctionnerait pas sur ce système. La page de la version liste les fichiers publiés.",
        "en": "This release has no package for {platform}. No other package is offered: it would not run on "
              "this system. The release page lists the published files.",
        "es": "Esta versión no incluye un paquete para {platform}. No se ofrece ningún otro paquete: no "
              "funcionaría en este sistema. La página de la versión enumera los archivos publicados.",
    },
    "update.package.unknown_platform": {
        "fr": "Aucun paquet officiel n'existe pour ce système ({platform}).",
        "en": "There is no official package for this system ({platform}).",
        "es": "No existe ningún paquete oficial para este sistema ({platform}).",
    },
    "update.package.no_checksums": {
        "fr": "Cette version ne publie pas d'empreintes ({file}) : sans elles, le téléchargement intégré est "
              "désactivé. Passez par la page de la version.",
        "en": "This release does not publish checksums ({file}): without them, the built-in download is "
              "disabled. Use the release page instead.",
        "es": "Esta versión no publica sumas de verificación ({file}): sin ellas, la descarga integrada está "
              "desactivada. Use la página de la versión.",
    },
    "update.source.text": {
        "fr": "Kut-Studio s'exécute depuis les sources : mettez votre copie à jour avec git (par exemple "
              "« git pull »). Rien n'est téléchargé ni remplacé automatiquement.",
        "en": "Kut-Studio is running from source: update your copy with git (for example “git pull”). "
              "Nothing is downloaded or replaced automatically.",
        "es": "Kut-Studio se ejecuta desde el código fuente: actualice su copia con git (por ejemplo "
              "«git pull»). No se descarga ni se reemplaza nada automáticamente.",
    },
    # --- Téléchargement -----------------------------------------------------------------------------------------
    "update.downloading.title": {
        "fr": "Téléchargement de Kut-Studio {version}",
        "en": "Downloading Kut-Studio {version}",
        "es": "Descargando Kut-Studio {version}",
    },
    "update.downloading.text": {
        "fr": "Le paquet est écrit dans un fichier temporaire, puis vérifié (taille et SHA-256) avant d'être "
              "proposé. Vous pouvez continuer à travailler.",
        "en": "The package is written to a temporary file, then verified (size and SHA-256) before it is "
              "offered. You can keep working.",
        "es": "El paquete se escribe en un archivo temporal y se verifica (tamaño y SHA-256) antes de "
              "ofrecerse. Puede seguir trabajando.",
    },
    "update.progress": {"fr": "{received} sur {total}", "en": "{received} of {total}", "es": "{received} de {total}"},
    "update.progress.unknown": {"fr": "{received} reçus", "en": "{received} received", "es": "{received} recibidos"},
    "update.cancelled.title": {"fr": "Téléchargement annulé", "en": "Download cancelled", "es": "Descarga cancelada"},
    # --- Paquet vérifié -----------------------------------------------------------------------------------------
    "update.ready.title": {
        "fr": "Kut-Studio {version} est prêt à installer",
        "en": "Kut-Studio {version} is ready to install",
        "es": "Kut-Studio {version} está listo para instalar",
    },
    "update.ready.verified": {
        "fr": "Fichier vérifié : sa taille et son empreinte SHA-256 sont celles publiées avec la version {version}.",
        "en": "File verified: its size and SHA-256 checksum are the ones published with version {version}.",
        "es": "Archivo verificado: su tamaño y su suma SHA-256 son los publicados con la versión {version}.",
    },
    "update.ready.integrity": {
        "fr": "Ce contrôle garantit que le fichier est complet et identique à celui de la release. L'empreinte vient "
              "de la même page GitHub que le paquet : elle ne prouve pas qui l'a publié.",
        "en": "This check guarantees that the file is complete and identical to the one in the release. The "
              "checksum comes from the same GitHub page as the package: it does not prove who published it.",
        "es": "Esta comprobación garantiza que el archivo está completo y es idéntico al de la versión publicada. "
              "La suma procede de la misma página de GitHub que el paquete: no demuestra quién lo publicó.",
    },
    "update.ready.file": {"fr": "Fichier vérifié : {path}", "en": "Verified file: {path}", "es": "Archivo verificado: {path}"},
    "update.authenticity.macos": {
        "fr": "Authenticité : macOS contrôle la signature Developer ID et la notarisation à la première ouverture "
              "(le fichier porte la marque de quarantaine, comme un téléchargement par navigateur). Si les notes "
              "indiquent une version non notarisée, macOS en bloquera l'ouverture : ne l'autorisez dans Réglages "
              "Système › Confidentialité et sécurité que si vous faites confiance à sa provenance.",
        "en": "Authenticity: macOS checks the Developer ID signature and notarization the first time it is opened "
              "(the file carries the quarantine flag, like a browser download). If the notes say this version is "
              "not notarized, macOS will block it: only allow it in System Settings › Privacy & Security if you "
              "trust where it comes from.",
        "es": "Autenticidad: macOS comprueba la firma Developer ID y la notarización la primera vez que se abre "
              "(el archivo lleva la marca de cuarentena, como una descarga del navegador). Si las notas indican "
              "una versión no notarizada, macOS bloqueará su apertura: autorícela en Ajustes del Sistema › "
              "Privacidad y seguridad solo si confía en su procedencia.",
    },
    "update.authenticity.windows": {
        "fr": "Authenticité : les paquets Windows ne sont pas encore signés (Authenticode) ; SmartScreen peut "
              "signaler un éditeur inconnu. Le fichier porte la marque « téléchargé d'Internet » de Windows.",
        "en": "Authenticity: Windows packages are not signed yet (Authenticode); SmartScreen may report an "
              "unknown publisher. The file carries the Windows “downloaded from the Internet” mark.",
        "es": "Autenticidad: los paquetes de Windows aún no están firmados (Authenticode); SmartScreen puede "
              "indicar un editor desconocido. El archivo lleva la marca «descargado de Internet» de Windows.",
    },
    "update.authenticity.linux": {
        "fr": "Authenticité : le système ne vérifie aucune signature pour ce paquet Linux.",
        "en": "Authenticity: the system does not check any signature for this Linux package.",
        "es": "Autenticidad: el sistema no comprueba ninguna firma para este paquete de Linux.",
    },
    "update.steps.macos": {
        "fr": "Pour installer :\n1. Quittez Kut-Studio.\n2. Ouvrez l'archive : Kut-Studio.app apparaît à côté.\n"
              "3. Glissez Kut-Studio.app dans le dossier Applications et choisissez « Remplacer ».\n"
              "4. Ouvrez la nouvelle version. Vos projets et préférences sont conservés.",
        "en": "To install:\n1. Quit Kut-Studio.\n2. Open the archive: Kut-Studio.app appears next to it.\n"
              "3. Drag Kut-Studio.app to the Applications folder and choose “Replace”.\n"
              "4. Open the new version. Your projects and preferences are kept.",
        "es": "Para instalar:\n1. Salga de Kut-Studio.\n2. Abra el archivo: Kut-Studio.app aparece a su lado.\n"
              "3. Arrastre Kut-Studio.app a la carpeta Aplicaciones y elija «Reemplazar».\n"
              "4. Abra la nueva versión. Sus proyectos y preferencias se conservan.",
    },
    "update.steps.windows": {
        "fr": "Pour installer :\n1. Quittez Kut-Studio.\n2. Clic droit sur l'archive › « Extraire tout ».\n"
              "3. Remplacez le dossier de l'ancienne version ({location}) par le dossier Kut-Studio extrait.\n"
              "4. Lancez Kut-Studio.exe. Vos projets et préférences ne sont pas dans ce dossier : ils sont conservés.",
        "en": "To install:\n1. Quit Kut-Studio.\n2. Right-click the archive › “Extract All”.\n"
              "3. Replace the old version's folder ({location}) with the extracted Kut-Studio folder.\n"
              "4. Start Kut-Studio.exe. Your projects and preferences are not in that folder: they are kept.",
        "es": "Para instalar:\n1. Salga de Kut-Studio.\n2. Clic derecho en el archivo › «Extraer todo».\n"
              "3. Reemplace la carpeta de la versión anterior ({location}) por la carpeta Kut-Studio extraída.\n"
              "4. Inicie Kut-Studio.exe. Sus proyectos y preferencias no están en esa carpeta: se conservan.",
    },
    "update.steps.linux": {
        "fr": "Pour installer :\n1. Quittez Kut-Studio.\n2. Extrayez l'archive (tar -xzf {name}).\n"
              "3. Remplacez le dossier de l'ancienne version ({location}) par le dossier Kut-Studio extrait.\n"
              "4. Lancez Kut-Studio/Kut-Studio. Vos projets et préférences sont conservés.",
        "en": "To install:\n1. Quit Kut-Studio.\n2. Extract the archive (tar -xzf {name}).\n"
              "3. Replace the old version's folder ({location}) with the extracted Kut-Studio folder.\n"
              "4. Start Kut-Studio/Kut-Studio. Your projects and preferences are kept.",
        "es": "Para instalar:\n1. Salga de Kut-Studio.\n2. Extraiga el archivo (tar -xzf {name}).\n"
              "3. Reemplace la carpeta de la versión anterior ({location}) por la carpeta Kut-Studio extraída.\n"
              "4. Inicie Kut-Studio/Kut-Studio. Sus proyectos y preferencias se conservan.",
    },
    "update.translocated": {
        "fr": "Attention : macOS exécute cette copie depuis un emplacement temporaire (App Translocation). "
              "Placez la nouvelle version dans le dossier Applications.",
        "en": "Note: macOS is running this copy from a temporary location (App Translocation). Put the new "
              "version in the Applications folder.",
        "es": "Atención: macOS ejecuta esta copia desde una ubicación temporal (App Translocation). Coloque la "
              "nueva versión en la carpeta Aplicaciones.",
    },
    "update.location.unknown": {"fr": "emplacement actuel", "en": "current location", "es": "ubicación actual"},
    "update.quit.title": {
        "fr": "Installer la mise à jour",
        "en": "Install the update",
        "es": "Instalar la actualización",
    },
    "update.quit.text": {
        "fr": "Kut-Studio va se fermer, puis ouvrir le dossier du paquet vérifié pour que vous remplaciez "
              "l'application.\n\nUn projet non enregistré vous sera proposé à l'enregistrement, et un rendu en "
              "cours demandera confirmation. Continuer ?",
        "en": "Kut-Studio will close, then open the folder of the verified package so that you can replace the "
              "application.\n\nYou will be offered to save an unsaved project, and a render in progress will ask "
              "for confirmation. Continue?",
        "es": "Kut-Studio se cerrará y luego abrirá la carpeta del paquete verificado para que reemplace la "
              "aplicación.\n\nSe le propondrá guardar un proyecto sin guardar, y un renderizado en curso pedirá "
              "confirmación. ¿Continuar?",
    },
    # --- Boutons ------------------------------------------------------------------------------------------------
    "update.button.download": {"fr": "Télécharger", "en": "Download", "es": "Descargar"},
    "update.button.later": {"fr": "Plus tard", "en": "Later", "es": "Más tarde"},
    "update.button.skip": {"fr": "Ignorer cette version", "en": "Skip this version", "es": "Omitir esta versión"},
    "update.button.unskip": {
        "fr": "Ne plus ignorer cette version",
        "en": "Stop skipping this version",
        "es": "Dejar de omitir esta versión",
    },
    "update.button.release_page": {"fr": "Page de la version", "en": "Release page", "es": "Página de la versión"},
    "update.button.cancel": {"fr": "Annuler", "en": "Cancel", "es": "Cancelar"},
    "update.button.retry": {"fr": "Réessayer", "en": "Retry", "es": "Reintentar"},
    "update.button.close": {"fr": "Fermer", "en": "Close", "es": "Cerrar"},
    "update.button.open_folder": {"fr": "Ouvrir le dossier", "en": "Open folder", "es": "Abrir la carpeta"},
    "update.button.install": {"fr": "Quitter et installer…", "en": "Quit and install…", "es": "Salir e instalar…"},
    # --- Erreurs (une clé par core.updates.UpdateErrorKind) -----------------------------------------------------
    "update.error.check_title": {
        "fr": "Recherche impossible",
        "en": "Could not check for updates",
        "es": "No se pudo buscar actualizaciones",
    },
    "update.error.download_title": {
        "fr": "Le téléchargement a échoué",
        "en": "The download failed",
        "es": "La descarga falló",
    },
    "update.error.offline": {
        "fr": "Pas de connexion à Internet, ou GitHub est injoignable.",
        "en": "No Internet connection, or GitHub cannot be reached.",
        "es": "Sin conexión a Internet, o no se puede acceder a GitHub.",
    },
    "update.error.timeout": {
        "fr": "GitHub n'a pas répondu à temps. Réessayez plus tard.",
        "en": "GitHub did not respond in time. Try again later.",
        "es": "GitHub no respondió a tiempo. Inténtelo más tarde.",
    },
    "update.error.rate_limited": {
        "fr": "GitHub limite temporairement les requêtes venant de votre réseau. Réessayez après {time}.",
        "en": "GitHub is temporarily limiting requests from your network. Try again after {time}.",
        "es": "GitHub limita temporalmente las solicitudes de su red. Inténtelo de nuevo después de las {time}.",
    },
    "update.error.rate_limited_unknown": {
        "fr": "GitHub limite temporairement les requêtes venant de votre réseau. Réessayez dans quelques minutes.",
        "en": "GitHub is temporarily limiting requests from your network. Try again in a few minutes.",
        "es": "GitHub limita temporalmente las solicitudes de su red. Inténtelo de nuevo en unos minutos.",
    },
    "update.error.http": {
        "fr": "GitHub a répondu par une erreur (HTTP {status}).",
        "en": "GitHub returned an error (HTTP {status}).",
        "es": "GitHub devolvió un error (HTTP {status}).",
    },
    "update.error.tls": {
        "fr": "La connexion sécurisée à GitHub a échoué (certificat ou TLS). Vérifiez la date de l'ordinateur et "
              "le réseau.",
        "en": "The secure connection to GitHub failed (certificate or TLS). Check the computer's date and the "
              "network.",
        "es": "La conexión segura con GitHub falló (certificado o TLS). Compruebe la fecha del ordenador y la red.",
    },
    "update.error.network": {
        "fr": "Erreur réseau pendant l'échange avec GitHub.",
        "en": "A network error occurred while talking to GitHub.",
        "es": "Error de red durante la comunicación con GitHub.",
    },
    "update.error.invalid_response": {
        "fr": "La réponse reçue est illisible ou inattendue : rien n'a été installé.",
        "en": "The response received is unreadable or unexpected: nothing was installed.",
        "es": "La respuesta recibida es ilegible o inesperada: no se ha instalado nada.",
    },
    "update.error.cancelled": {
        "fr": "Le téléchargement a été annulé ; le fichier partiel a été supprimé.",
        "en": "The download was cancelled; the partial file was deleted.",
        "es": "La descarga se canceló; el archivo parcial se eliminó.",
    },
    "update.error.too_large": {
        "fr": "Le fichier reçu dépasse la taille annoncée : téléchargement interrompu, fichier supprimé.",
        "en": "The file received is larger than announced: download stopped, file deleted.",
        "es": "El archivo recibido supera el tamaño anunciado: descarga interrumpida, archivo eliminado.",
    },
    "update.error.size_mismatch": {
        "fr": "Le fichier reçu n'a pas la taille annoncée (incomplet ou altéré) : il a été supprimé.",
        "en": "The file received does not have the announced size (incomplete or altered): it was deleted.",
        "es": "El archivo recibido no tiene el tamaño anunciado (incompleto o alterado): se eliminó.",
    },
    "update.error.checksum_mismatch": {
        "fr": "L'empreinte SHA-256 du fichier ne correspond pas à celle publiée : il a été supprimé et ne sera "
              "pas ouvert.",
        "en": "The file's SHA-256 checksum does not match the published one: it was deleted and will not be "
              "opened.",
        "es": "La suma SHA-256 del archivo no coincide con la publicada: se eliminó y no se abrirá.",
    },
    "update.error.checksum_missing": {
        "fr": "La version ne publie pas d'empreinte SHA-256 pour ce paquet : téléchargement refusé.",
        "en": "The release does not publish a SHA-256 checksum for this package: download refused.",
        "es": "La versión no publica una suma SHA-256 para este paquete: descarga rechazada.",
    },
    "update.error.no_package": {
        "fr": "Aucun paquet compatible avec ce système dans cette version.",
        "en": "No package compatible with this system in this release.",
        "es": "Ningún paquete compatible con este sistema en esta versión.",
    },
    "update.error.disk": {
        "fr": "Impossible d'enregistrer le fichier sur le disque (espace libre ou droits).",
        "en": "The file could not be saved to disk (free space or permissions).",
        "es": "No se pudo guardar el archivo en el disco (espacio libre o permisos).",
    },
    "update.error.unsupported": {
        "fr": "Cette installation ne dispose d'aucun moteur TLS : les connexions sécurisées sont impossibles.",
        "en": "This installation has no TLS backend: secure connections are impossible.",
        "es": "Esta instalación no dispone de ningún motor TLS: las conexiones seguras son imposibles.",
    },
    "update.error.detail": {
        "fr": "Détail technique : {detail}",
        "en": "Technical detail: {detail}",
        "es": "Detalle técnico: {detail}",
    },
    # --- Préférences --------------------------------------------------------------------------------------------
    "prefs.updates": {"fr": "Mises à jour", "en": "Updates", "es": "Actualizaciones"},
    "prefs.updates.check": {
        "fr": "Rechercher les mises à jour au démarrage (une fois par jour au plus)",
        "en": "Check for updates at startup (at most once a day)",
        "es": "Buscar actualizaciones al iniciar (como máximo una vez al día)",
    },
    "prefs.updates.prereleases": {
        "fr": "Proposer aussi les préversions (bêta, rc)",
        "en": "Also offer pre-releases (beta, rc)",
        "es": "Ofrecer también versiones preliminares (beta, rc)",
    },
    "prefs.updates.note": {
        "fr": "Version installée : {version}. La recherche interroge api.github.com ; aucune donnée personnelle "
              "n'est envoyée.",
        "en": "Installed version: {version}. Checking contacts api.github.com; no personal data is sent.",
        "es": "Versión instalada: {version}. La búsqueda consulta api.github.com; no se envían datos personales.",
    },
    # --- À propos -----------------------------------------------------------------------------------------------
    "update.about.title": {"fr": "À propos de Kut-Studio", "en": "About Kut-Studio", "es": "Acerca de Kut-Studio"},
    "update.about.text": {
        "fr": "Kut-Studio {version}\nÉditeur vidéo open source (licence MIT).\n\nInstallation : {install}\n"
              "Système : {system}\nQt {qt} · PySide6 {pyside} · Python {python}\n\n{website}",
        "en": "Kut-Studio {version}\nOpen-source video editor (MIT license).\n\nInstallation: {install}\n"
              "System: {system}\nQt {qt} · PySide6 {pyside} · Python {python}\n\n{website}",
        "es": "Kut-Studio {version}\nEditor de vídeo de código abierto (licencia MIT).\n\nInstalación: {install}\n"
              "Sistema: {system}\nQt {qt} · PySide6 {pyside} · Python {python}\n\n{website}",
    },
    "update.install.source": {
        "fr": "depuis les sources ({path})",
        "en": "from source ({path})",
        "es": "desde el código fuente ({path})",
    },
    "update.install.source_revision": {
        "fr": "depuis les sources ({path}, commit {revision})",
        "en": "from source ({path}, commit {revision})",
        "es": "desde el código fuente ({path}, commit {revision})",
    },
    "update.install.macos_app": {
        "fr": "application macOS ({path})",
        "en": "macOS app ({path})",
        "es": "aplicación de macOS ({path})",
    },
    "update.install.folder": {"fr": "dossier {path}", "en": "folder {path}", "es": "carpeta {path}"},
    "update.platform.unknown": {"fr": "système inconnu", "en": "unknown system", "es": "sistema desconocido"},
}

__all__ = ["UPDATES_TRANSLATIONS"]

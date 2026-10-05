"""Faux GitHub en local (HTTP sur 127.0.0.1) pour tester le vrai transport Qt des mises à jour.

Chaque route renvoie une réponse simulée : statut, en-têtes (limites de l'API…), corps envoyé d'un bloc ou par
morceaux espacés (progression, annulation, délai d'inactivité), redirection, attente avant réponse (délai total). Les
requêtes reçues sont enregistrées (chemin et en-têtes) pour vérifier ce que l'application envoie — et ce qu'elle
n'envoie pas (aucun paquet demandé si l'empreinte manque, par exemple).
"""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    chunks: int = 1
    """Nombre de morceaux du corps (avec ``pause`` secondes entre eux)."""
    pause: float = 0.0
    delay: float = 0.0
    """Attente avant toute réponse (en-têtes compris)."""
    redirect: str | None = None


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format, *args):  # noqa: A002 - signature de BaseHTTPRequestHandler
        return

    def do_GET(self):  # noqa: N802 - nom imposé par http.server
        path = urlsplit(self.path).path
        self.server.owner.requests.append((path, {key.lower(): value for key, value in self.headers.items()}))
        route = self.server.owner.routes.get(path)
        if route is None:
            route = Route(status=404, body=b'{"message": "Not Found"}')
        try:
            if route.delay:
                time.sleep(route.delay)
            if route.redirect is not None:
                self.send_response(302)
                self.send_header("Location", route.redirect)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(route.status)
            for name, value in route.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(route.body)))
            self.end_headers()
            size = max(1, -(-len(route.body) // max(1, route.chunks)))
            for start in range(0, len(route.body), size):
                self.wfile.write(route.body[start:start + size])
                self.wfile.flush()
                if route.pause and start + size < len(route.body):
                    time.sleep(route.pause)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                         # le client a abandonné : c'est ce qu'on teste parfois


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    owner: "UpdateServer"

    def server_bind(self):
        # ``HTTPServer.server_bind`` appelle ``socket.getfqdn`` : une résolution DNS inverse de 127.0.0.1 qui attendait
        # ~35 s sur les runners macOS de la CI. Le nom n'est jamais utilisé ici.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port

    def handle_error(self, request, client_address):
        return                                           # pas de trace pour une connexion coupée par le client


class UpdateServer:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.owner = self
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-github", daemon=True)
        self._thread.start()

    @property
    def base(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return self.base + path

    def requested(self, path: str) -> bool:
        return any(seen == path for seen, _headers in self.requests)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def closed_port_url(path: str = "/") -> str:
    """Adresse locale où rien n'écoute : connexion refusée (« hors ligne »)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{path}"


def publish_release(
    server: UpdateServer,
    tag: str,
    *,
    repository: str,
    packages: dict[str, bytes],
    checksums: str | None = None,
    prerelease: bool = False,
    notes: str = "## Nouveautés\n\n- Recherche de mises à jour",
    package_route: dict | None = None,
) -> dict:
    """Publie une release sur le faux GitHub : fichiers servis sous ``/download/<tag>/`` ; renvoie l'objet de l'API.

    ``checksums`` : texte de ``SHA256SUMS.txt`` (``None`` : empreintes exactes des paquets ; ``""`` : fichier absent).
    ``package_route`` : options de :class:`Route` pour les paquets (morceaux espacés, attente…).
    """
    import hashlib

    from core.release_assets import CHECKSUMS_FILE, format_checksums

    if checksums is None:
        checksums = format_checksums({name: hashlib.sha256(data).hexdigest() for name, data in packages.items()})
    files = dict(packages)
    if checksums:
        files[CHECKSUMS_FILE] = checksums.encode("utf-8")
    assets = []
    for name, data in files.items():
        options = package_route if (package_route and name != CHECKSUMS_FILE) else {}
        server.routes[f"/download/{tag}/{name}"] = Route(body=data, **options)
        assets.append({"name": name, "size": len(data), "state": "uploaded",
                       "browser_download_url": server.url(f"/download/{tag}/{name}")})
    return {
        "tag_name": tag, "name": f"Kut-Studio {tag.lstrip('v')}", "body": notes, "draft": False,
        "prerelease": prerelease, "published_at": "2026-10-05T12:00:00Z", "assets": assets,
        "html_url": f"https://github.com/{repository}/releases/tag/{tag}",
    }


def serve_releases(server: UpdateServer, repository: str, releases: list[dict], **route) -> None:
    """Réponse de ``GET /repos/<repository>/releases``."""
    import json

    server.routes[f"/repos/{repository}/releases"] = Route(body=json.dumps(releases).encode("utf-8"), **route)

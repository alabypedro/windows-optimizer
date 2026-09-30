"""Atualização do programa pelos Releases do GitHub.

Fluxo (nada acontece sem o usuário confirmar):
  1. :func:`check_for_update` consulta o último Release de ``UPDATE_REPO``
     (somente leitura, sem login);
  2. :func:`download_installer` baixa o instalador ``WindowsOptimizer-Setup-<versão>.exe``
     e confere o SHA-256 publicado junto (sem hash conferido, não instala);
  3. :func:`launch_installer` abre o instalador em modo silencioso e o programa
     fecha; o instalador substitui os arquivos e reabre o programa.

Segurança: só HTTPS e só hosts do GitHub, tamanho máximo do download, hash
obrigatório e o instalador é salvo numa pasta temporária própria.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from utils.helpers import APP_VERSION, UPDATE_REPO, format_size
from utils.logger import get_logger

log = get_logger("updater")

INSTALLER_PATTERN = re.compile(r"^WindowsOptimizer-Setup-[\w.\-]+\.exe$", re.IGNORECASE)
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com"}
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
TIMEOUT = 20
USER_AGENT = f"WindowsOptimizer/{APP_VERSION} (+updater)"

ProgressFn = Optional[Callable[[str], None]]


class UpdateError(Exception):
    """Erro com mensagem pronta para o usuário."""


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    installer_name: str
    installer_url: str
    installer_size: int
    sha256: str  # hex, minúsculo

    @property
    def available(self) -> bool:
        return is_newer(self.version, APP_VERSION)


# ---------------------------------------------------------------------------
# Versões
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10). Partes não numéricas (ex.: '-beta') são ignoradas."""
    numbers = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ---------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------

def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise UpdateError(f"Endereço de atualização não permitido: {parsed.hostname or url}")


def _open(url: str, accept: str):
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Nenhuma versão publicada foi encontrada.") from exc
        if exc.code in (403, 429):
            raise UpdateError("O GitHub limitou as consultas por agora. Tente de novo em alguns minutos.") from exc
        raise UpdateError(f"O servidor de atualizações respondeu com erro {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Sem conexão com a internet (ou o GitHub não respondeu).") from exc
    _check_url(response.geturl())  # redirecionamentos também precisam ficar no GitHub
    return response


def _read_text(url: str, limit: int = 1024 * 1024) -> str:
    with _open(url, "application/octet-stream") as response:
        return response.read(limit).decode("utf-8", errors="replace")


def check_for_update(repo: str = "") -> UpdateInfo:
    """Consulta o último Release publicado. Não baixa nada."""
    repo = repo or UPDATE_REPO
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo or ""):
        raise UpdateError("A origem das atualizações ainda não foi configurada.")
    with _open(f"https://api.github.com/repos/{repo}/releases/latest", "application/vnd.github+json") as response:
        try:
            release = json.loads(response.read(2 * 1024 * 1024))
        except json.JSONDecodeError as exc:
            raise UpdateError("Resposta inválida do servidor de atualizações.") from exc
    assets = release.get("assets") or []
    installer = next((a for a in assets if INSTALLER_PATTERN.match(a.get("name", ""))), None)
    if installer is None:
        raise UpdateError("A versão publicada não tem instalador para Windows.")

    sha256 = ""
    digest = installer.get("digest") or ""  # o GitHub publica "sha256:<hex>" para cada arquivo
    if digest.lower().startswith("sha256:"):
        sha256 = digest.split(":", 1)[1]
    else:
        hash_asset = next((a for a in assets if a.get("name") == installer["name"] + ".sha256"), None)
        if hash_asset is not None:
            content = _read_text(hash_asset["browser_download_url"]).split()
            sha256 = content[0] if content else ""
    sha256 = sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("A versão publicada não tem o código de verificação (SHA-256) do instalador; "
                          "por segurança ela não será instalada.")

    return UpdateInfo(
        version=str(release.get("tag_name") or "").lstrip("vV"),
        notes=str(release.get("body") or "").strip(),
        page_url=str(release.get("html_url") or f"https://github.com/{repo}/releases/latest"),
        installer_name=installer["name"],
        installer_url=installer["browser_download_url"],
        installer_size=int(installer.get("size") or 0),
        sha256=sha256,
    )


def download_installer(info: UpdateInfo, progress: ProgressFn = None,
                       cancel: Optional[threading.Event] = None) -> Path:
    """Baixa o instalador para uma pasta temporária e confere o SHA-256."""
    if info.installer_size > MAX_DOWNLOAD_BYTES:
        raise UpdateError("O instalador publicado é grande demais; download recusado.")
    folder = Path(tempfile.mkdtemp(prefix="wo_update_"))
    target = folder / Path(info.installer_name).name
    digest = hashlib.sha256()
    received = 0
    log.info(f"INÍCIO download da atualização {info.version}: {info.installer_url}")
    try:
        with _open(info.installer_url, "application/octet-stream") as response, open(target, "wb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Download cancelado.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("O download passou do tamanho máximo permitido.")
                digest.update(chunk)
                handle.write(chunk)
                if progress:
                    total = f" de {format_size(info.installer_size)}" if info.installer_size else ""
                    progress(f"Baixando atualização {info.version}: {format_size(received)}{total}")
        if digest.hexdigest() != info.sha256:
            raise UpdateError("O arquivo baixado não confere com o código de verificação (SHA-256). "
                              "Ele foi descartado; tente novamente mais tarde.")
    except BaseException:
        target.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    log.info(f"FIM download da atualização {info.version}: {target} ({format_size(received)}), SHA-256 conferido")
    return target


# ---------------------------------------------------------------------------
# Instalação
# ---------------------------------------------------------------------------

def installed_app_dir() -> Optional[Path]:
    """Pasta do programa instalado pelo instalador (tem o desinstalador ao lado), ou None."""
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    return folder if (folder / "unins000.exe").exists() else None


def can_self_update() -> bool:
    return sys.platform == "win32" and installed_app_dir() is not None


def launch_installer(installer: Path) -> None:
    """Abre o instalador (silencioso, com barra de progresso) e deixa ele reabrir o programa.

    O chamador deve fechar o programa logo em seguida: o instalador espera os
    arquivos serem liberados (Restart Manager) antes de substituí-los.
    """
    if installed_app_dir() is None:
        raise UpdateError("A atualização automática só funciona na versão instalada pelo instalador.")
    # Mesmo AppId: o instalador reaproveita a pasta e o modo (usuário/todos) da instalação atual.
    # /WAITPID: o instalador espera este processo terminar antes de substituir os arquivos.
    args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/RELAUNCH=1", f"/WAITPID={os.getpid()}"]
    log.info(f"Iniciando instalador da atualização: {installer}")
    # Sem shell; processo independente (continua depois que o programa fechar).
    subprocess.Popen(args, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def open_release_page(info: Optional[UpdateInfo] = None) -> None:
    url = info.page_url if info else f"https://github.com/{UPDATE_REPO}/releases/latest"
    try:
        _check_url(url)
        os.startfile(url)  # type: ignore[attr-defined]
    except (OSError, UpdateError):
        pass

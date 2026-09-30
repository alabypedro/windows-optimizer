"""Funções auxiliares: caminhos da aplicação, formatação e execução de comandos.

Todos os comandos externos passam por :func:`run_command`, que:
  * nunca usa ``shell=True`` (argumentos são passados como lista);
  * sempre define timeout (processos não ficam presos);
  * não abre janela de console (CREATE_NO_WINDOW);
  * decodifica a saída de forma tolerante (UTF-8, UTF-16 ou code page OEM).
"""

from __future__ import annotations

import base64
import ctypes
import json
import locale
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

APP_NAME = "Windows Optimizer"
APP_VERSION = "1.0.0"  # única fonte da versão: o instalador e o publish_release.py leem daqui
# Repositório do GitHub ("dono/nome") cujos Releases trazem as atualizações.
# Vazio = botão de atualização avisa que a origem não foi configurada.
UPDATE_REPO = os.environ.get("WO_UPDATE_REPO", "alabypedro/windows-optimizer")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------

def base_dir() -> Path:
    """Pasta onde o programa está (ou do .exe, quando empacotado)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


_data_dir_cache: Optional[Path] = None


def data_dir() -> Path:
    """Pasta gravável para logs/ e backups/.

    Usa a pasta do programa; se ela não for gravável (ex.: instalado em
    Program Files), cai para %LOCALAPPDATA%\\WindowsOptimizer.
    """
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache
    candidate = base_dir()
    if not _writable(candidate / "logs"):
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        candidate = Path(local) / "WindowsOptimizer"
        candidate.mkdir(parents=True, exist_ok=True)
    _data_dir_cache = candidate
    return candidate


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_path(var: str, default: str = "") -> Optional[Path]:
    """Retorna Path de uma variável de ambiente, ou None se não definida."""
    value = os.environ.get(var) or default
    return Path(value) if value else None


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def format_size(num_bytes: float) -> str:
    """Bytes em formato legível no padrão brasileiro (ex.: 1,23 GB)."""
    value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}".replace(".", ",")
        value /= 1024.0
    return f"{value:.2f} TB"


def format_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def format_percent(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}min"
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


# ---------------------------------------------------------------------------
# Decodificação da saída de ferramentas nativas
# ---------------------------------------------------------------------------

def oem_encoding() -> str:
    """Code page OEM do console (ex.: cp850 no Windows em português)."""
    if IS_WINDOWS:
        try:
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            pass
    return locale.getpreferredencoding(False) or "utf-8"


def decode_output(data: bytes) -> str:
    """Decodifica saída de comandos nativos com tolerância a encoding.

    - sfc.exe grava UTF-16 LE quando a saída é redirecionada;
    - ferramentas clássicas (ipconfig, powercfg, dism) usam a code page OEM;
    - PowerShell é forçado a UTF-8 por :func:`run_powershell`.
    """
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    # Muitos bytes nulos em posições ímpares => UTF-16 LE sem BOM.
    sample = data[:400]
    if len(sample) > 8 and sample[1::2].count(0) > len(sample) // 4:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(oem_encoding(), errors="replace")


# ---------------------------------------------------------------------------
# Execução de comandos
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    def summary(self) -> str:
        if self.timed_out:
            return "tempo limite excedido"
        if self.error:
            return self.error
        text = (self.stderr or self.stdout).strip()
        return text.splitlines()[-1] if text else f"código de saída {self.returncode}"


def run_command(args: Sequence[str], timeout: float = 60) -> CommandResult:
    """Executa um comando (sem shell) e captura a saída.

    Nunca lança exceção: erros viram um CommandResult com ``error``.
    """
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return CommandResult(args, proc.returncode, decode_output(proc.stdout),
                             decode_output(proc.stderr))
    except subprocess.TimeoutExpired as exc:
        # subprocess.run já mata o processo filho no timeout.
        return CommandResult(args, -1, decode_output(exc.stdout or b""),
                             decode_output(exc.stderr or b""), timed_out=True)
    except FileNotFoundError:
        return CommandResult(args, -1, "", "", error=f"comando não encontrado: {args[0]}")
    except OSError as exc:
        return CommandResult(args, -1, "", "", error=str(exc))


def stream_command(
    args: Sequence[str],
    on_line: Callable[[str], None],
    cancel_event: Optional[threading.Event] = None,
) -> int:
    """Executa um comando longo (SFC/DISM/CHKDSK) enviando a saída linha a linha.

    Trata '\\r' como quebra de linha (as ferramentas usam '\\r' para progresso).
    Retorna o código de saída, ou -1 em caso de erro de execução.
    """
    try:
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        on_line(f"Erro ao iniciar {args[0]}: {exc}")
        return -1

    buffer = b""
    utf16 = None  # detectado no primeiro bloco
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            if utf16 is None:
                utf16 = chunk.startswith(b"\xff\xfe") or chunk[1::2].count(0) > len(chunk) // 4
            buffer += chunk
            if utf16:
                # Mantém número par de bytes antes de decodificar.
                cut = len(buffer) - (len(buffer) % 2)
                text, buffer = buffer[:cut].decode("utf-16-le", errors="replace"), buffer[cut:]
                text = text.lstrip("\ufeff")
            else:
                text, buffer = decode_output(buffer), b""
            for piece in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                piece = piece.strip()
                if piece:
                    on_line(piece)
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                on_line("Operação cancelada pelo usuário.")
                break
    finally:
        if proc.stdout:
            proc.stdout.close()
    try:
        return proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1


def run_powershell(script: str, timeout: float = 60) -> CommandResult:
    """Executa um script PowerShell com saída em UTF-8.

    O script é enviado via -EncodedCommand (UTF-16 LE em base64), evitando
    qualquer problema de aspas/escape na linha de comando.
    """
    full = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" \
           "$ProgressPreference = 'SilentlyContinue'\n" + script
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    return run_command(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        timeout=timeout,
    )


def run_powershell_json(script: str, timeout: float = 60) -> Any:
    """Executa PowerShell cujo script termina em ConvertTo-Json.

    Retorna o objeto decodificado (list/dict) ou None em caso de falha.
    """
    result = run_powershell(script, timeout=timeout)
    if not result.ok or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def as_list(value: Any) -> list:
    """ConvertTo-Json devolve objeto único em vez de lista quando há 1 item."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def open_in_explorer(path: Path | str) -> None:
    """Abre uma pasta (ou seleciona um arquivo) no Explorer."""
    path = Path(path)
    try:
        if path.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(path)], creationflags=CREATE_NO_WINDOW)
        elif path.exists():
            os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def open_uri(uri: str) -> None:
    """Abre um URI do Windows (ex.: ms-settings:windowsupdate)."""
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
    except OSError:
        pass

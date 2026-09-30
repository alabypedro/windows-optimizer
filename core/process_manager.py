"""Gerenciador de processos simples + detecção de apps em segundo plano.

Finalizar processo NÃO pode ser desfeito; por isso a interface sempre pede
confirmação e processos críticos (lista em core.security) são bloqueados.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from dataclasses import dataclass
from typing import Optional

import psutil

from core.security import SENSITIVE_PROCESSES, is_process_protected
from core.system_info import ProcessUsage, sample_processes
from utils.helpers import IS_WINDOWS, format_size
from utils.logger import get_logger

log = get_logger("process")


@dataclass
class ProcessRow:
    pid: int
    name: str
    cpu_percent: float
    memory_bytes: int
    exe: str
    protected: bool
    protected_reason: str
    sensitive: bool


def list_processes() -> list[ProcessRow]:
    rows = []
    for p in sample_processes(interval=0.6):
        protected, reason = is_process_protected(p.name, p.pid, p.exe)
        rows.append(ProcessRow(p.pid, p.name, p.cpu_percent, p.memory_bytes, p.exe,
                               protected, reason, p.name.lower() in SENSITIVE_PROCESSES))
    return rows


def terminate_process(pid: int, expected_name: str) -> tuple[bool, str]:
    """Finaliza um processo (após confirmação). Revalida a proteção e o nome
    (evita finalizar outro processo que reutilizou o mesmo PID)."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        try:
            exe = proc.exe()
        except (psutil.AccessDenied, OSError):
            exe = ""
    except psutil.NoSuchProcess:
        return False, "o processo já foi encerrado"
    except psutil.AccessDenied:
        return False, "acesso negado"
    if name.lower() != expected_name.lower():
        return False, "o PID agora pertence a outro processo; atualize a lista"
    protected, reason = is_process_protected(name, pid, exe)
    if protected:
        log.warning(f"Bloqueada tentativa de finalizar processo protegido: {name} (PID {pid})")
        return False, f"processo protegido: {reason}"
    log.info(f"INÍCIO finalizar processo {name} (PID {pid}, {exe})")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        log.error(f"ERRO: acesso negado ao finalizar {name} (PID {pid})")
        return False, "acesso negado (pode exigir Administrador)"
    except psutil.TimeoutExpired:
        return False, "o processo não respondeu"
    log.info(f"FIM processo finalizado: {name} (PID {pid})")
    return True, f"{name} finalizado"


# ---------------------------------------------------------------------------
# Aplicativos em segundo plano
# ---------------------------------------------------------------------------

def _pids_with_visible_windows() -> set[int]:
    """PIDs que possuem ao menos uma janela visível (EnumWindows)."""
    pids: set[int] = set()
    if not IS_WINDOWS:
        return pids
    user32 = ctypes.windll.user32
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(proto(callback), 0)
    return pids


@dataclass
class BackgroundApp:
    name: str
    exe: str
    instances: int
    memory_bytes: int
    cpu_percent: float
    pids: list[int]
    recommendation: str


def list_background_apps(samples: Optional[list[ProcessUsage]] = None) -> list[BackgroundApp]:
    """Apps de terceiros do usuário atual rodando SEM janela visível.

    Ignora processos do Windows (pasta do sistema) e processos protegidos.
    Não altera nada: apenas apresenta recomendações.
    """
    samples = samples if samples is not None else sample_processes(interval=0.6)
    visible = _pids_with_visible_windows()
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    try:
        username = psutil.Process().username()
    except Exception:
        username = None
    groups: dict[str, BackgroundApp] = {}
    for p in samples:
        if not p.exe or p.exe.lower().startswith(windir) or p.pid in visible:
            continue
        if is_process_protected(p.name, p.pid, p.exe)[0]:
            continue
        try:
            if username and psutil.Process(p.pid).username() != username:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        key = p.exe.lower()
        app = groups.setdefault(key, BackgroundApp(p.name, p.exe, 0, 0, 0.0, [], ""))
        app.instances += 1
        app.memory_bytes += p.memory_bytes
        app.cpu_percent += p.cpu_percent
        app.pids.append(p.pid)
    result = []
    for app in groups.values():
        if app.memory_bytes > 300 * 1024 ** 2:
            app.recommendation = (f"Consome {format_size(app.memory_bytes)} em segundo plano. Se não usa "
                                  "com frequência, feche-o ou desative-o na inicialização.")
        elif app.cpu_percent > 5:
            app.recommendation = "Usando CPU em segundo plano. Verifique se é necessário."
        else:
            app.recommendation = "Consumo baixo. Mantenha se usar o recurso (ex.: sincronização, bandeja)."
        result.append(app)
    return sorted(result, key=lambda a: a.memory_bytes, reverse=True)

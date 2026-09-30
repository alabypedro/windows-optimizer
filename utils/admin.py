"""Detecção e solicitação de privilégios de Administrador.

O programa roda normalmente SEM privilégios elevados. Operações que exigem
administrador verificam :func:`is_admin` e, se necessário, a interface
oferece reiniciar o programa elevado (UAC). Nada é elevado silenciosamente.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from utils.helpers import IS_WINDOWS


class AdminRequiredError(PermissionError):
    """Operação exige privilégios de Administrador."""

    def __init__(self, operation: str):
        super().__init__(f"'{operation}' requer privilégios de Administrador.")
        self.operation = operation


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def require_admin(operation: str) -> None:
    """Lança AdminRequiredError se o processo não estiver elevado."""
    if not is_admin():
        raise AdminRequiredError(operation)


def relaunch_as_admin() -> bool:
    """Reabre o programa com elevação (prompt do UAC).

    Retorna True se o novo processo foi iniciado; o chamador deve então
    encerrar a instância atual. Retorna False se o usuário recusou o UAC.
    """
    if not IS_WINDOWS:
        return False
    try:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, subprocess.list2cmdline(sys.argv[1:])
        else:
            script = str(Path(sys.argv[0]).resolve())
            # pythonw evita uma janela de console extra para a GUI.
            exe = sys.executable
            pythonw = Path(exe).with_name("pythonw.exe")
            if pythonw.exists():
                exe = str(pythonw)
            params = subprocess.list2cmdline([script, *sys.argv[1:]])
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(result) > 32
    except Exception:
        return False

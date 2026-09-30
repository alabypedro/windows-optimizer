"""Gerenciador de programas de inicialização.

Detecta itens das chaves Run do Registro e das pastas Inicializar.
Para desativar, usa o MESMO mecanismo do Gerenciador de Tarefas
(chaves ``Explorer\\StartupApproved``): o item original NÃO é apagado,
apenas marcado como desativado — totalmente reversível.

Cada alteração é precedida de backup (reg export) e registrada no histórico
com o valor anterior, permitindo desfazer.
"""

from __future__ import annotations

import os
import re
import struct
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

from core import restore_manager
from core.security import is_startup_item_protected
from utils.admin import is_admin
from utils.helpers import as_list, run_powershell_json
from utils.logger import get_logger

log = get_logger("startup")

_APPROVED_BASE = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_KEY_32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"

_HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}


@dataclass
class StartupItem:
    name: str
    command: str
    exe_path: str
    location: str          # texto legível do local
    source: str            # "run" | "folder"
    hive: str              # "HKCU" | "HKLM"
    approved_subkey: str   # "Run" | "Run32" | "StartupFolder"
    enabled: bool
    publisher: str = ""
    impact: str = "Desconhecido"
    protected: bool = False
    protected_reason: str = ""
    running: bool = False
    memory_bytes: int = 0

    @property
    def key(self) -> str:
        return f"{self.hive}|{self.approved_subkey}|{self.name}"

    @property
    def needs_admin(self) -> bool:
        return self.hive == "HKLM"


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def extract_exe_path(command: str) -> str:
    """Extrai o executável de uma linha de comando do Registro."""
    cmd = os.path.expandvars(command.strip())
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd.strip('"')
    match = re.match(r"(.+?\.(exe|com|bat|cmd|vbs|js|lnk))(\s|$)", cmd, re.IGNORECASE)
    if match:
        return match.group(1)
    return cmd.split(" ")[0]


def _approved_state(hive: str, subkey: str, name: str) -> tuple[bool, Optional[bytes]]:
    """(ativado, valor bruto). Sem valor = ativado (padrão do Windows)."""
    try:
        with winreg.OpenKey(_HIVES[hive], f"{_APPROVED_BASE}\\{subkey}") as key:
            data, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return True, None
    if isinstance(data, bytes) and data:
        return (data[0] & 1) == 0, data  # 0x02/0x06 = ativado; 0x03/0x07 = desativado
    return True, None


def _read_run_key(hive: str, path: str, approved: str, label: str) -> list[StartupItem]:
    items = []
    try:
        key = winreg.OpenKey(_HIVES[hive], path)
    except OSError:
        return items
    with key:
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if not isinstance(value, str) or not value.strip():
                continue
            enabled, _ = _approved_state(hive, approved, name)
            items.append(StartupItem(
                name=name, command=value, exe_path=extract_exe_path(value),
                location=label, source="run", hive=hive, approved_subkey=approved, enabled=enabled,
            ))
    return items


def _startup_folders() -> list[tuple[str, Path, str]]:
    folders = []
    appdata = os.environ.get("APPDATA")
    progdata = os.environ.get("ProgramData")
    if appdata:
        folders.append(("HKCU", Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup",
                        "Pasta Inicializar (usuário)"))
    if progdata:
        folders.append(("HKLM", Path(progdata) / r"Microsoft\Windows\Start Menu\Programs\StartUp",
                        "Pasta Inicializar (todos os usuários)"))
    return folders


def _read_startup_folders() -> list[StartupItem]:
    items = []
    for hive, folder, label in _startup_folders():
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.lower() == "desktop.ini" or not entry.is_file():
                continue
            enabled, _ = _approved_state(hive, "StartupFolder", entry.name)
            items.append(StartupItem(
                name=entry.name, command=str(entry), exe_path=str(entry), location=label,
                source="folder", hive=hive, approved_subkey="StartupFolder", enabled=enabled,
            ))
    return items


def _resolve_shortcuts_and_publishers(items: list[StartupItem]) -> None:
    """Uma única chamada PowerShell: alvo dos atalhos .lnk + fabricante do .exe."""
    paths = sorted({it.exe_path for it in items if it.exe_path})
    if not paths:
        return
    literal = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        f"@({literal}) | ForEach-Object {{ $p = $_; $t = $p; "
        "if ($p -like '*.lnk' -and (Test-Path -LiteralPath $p)) { try { $t = $sh.CreateShortcut($p).TargetPath } catch {} }; "
        "$c = ''; $s = 0; if ($t -and (Test-Path -LiteralPath $t -PathType Leaf)) { $i = Get-Item -LiteralPath $t; "
        "$c = $i.VersionInfo.CompanyName; $s = $i.Length }; "
        "[pscustomobject]@{Path=$p; Target=$t; Company=[string]$c; Size=[int64]$s} } | ConvertTo-Json -Compress"
    )
    info = {str(d.get("Path", "")).lower(): d for d in as_list(run_powershell_json(script, timeout=60))}
    for item in items:
        data = info.get(item.exe_path.lower())
        if not data:
            continue
        target = str(data.get("Target") or "")
        if item.exe_path.lower().endswith(".lnk") and target:
            item.exe_path = target
        item.publisher = str(data.get("Company") or "").strip()


def _estimate_impact(items: list[StartupItem]) -> None:
    """Impacto APROXIMADO: memória usada pelo processo, se estiver em execução.

    O Windows mede o impacto real durante o boot; aqui é uma estimativa
    baseada no consumo atual de RAM do programa.
    """
    usage: dict[str, int] = {}
    for proc in psutil.process_iter(["exe", "memory_info"]):
        try:
            exe = (proc.info.get("exe") or "").lower()
            mem = proc.info.get("memory_info")
            if exe and mem:
                usage[exe] = usage.get(exe, 0) + mem.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for item in items:
        mem = usage.get(item.exe_path.lower(), 0)
        item.running = mem > 0
        item.memory_bytes = mem
        if not item.enabled:
            item.impact = "—"
        elif mem > 250 * 1024 ** 2:
            item.impact = "Alto (estimado)"
        elif mem > 60 * 1024 ** 2:
            item.impact = "Médio (estimado)"
        elif mem > 0:
            item.impact = "Baixo (estimado)"
        else:
            item.impact = "Não medido (não está em execução)"


def list_startup_items() -> list[StartupItem]:
    items: list[StartupItem] = []
    items += _read_run_key("HKCU", _RUN_KEY, "Run", "Registro: Run (usuário)")
    items += _read_run_key("HKLM", _RUN_KEY, "Run", "Registro: Run (todos os usuários)")
    items += _read_run_key("HKLM", _RUN_KEY_32, "Run32", "Registro: Run 32 bits (todos os usuários)")
    items += _read_startup_folders()
    _resolve_shortcuts_and_publishers(items)
    for item in items:
        item.protected, item.protected_reason = is_startup_item_protected(
            item.name, f"{item.command} {item.exe_path} {item.publisher}")
    _estimate_impact(items)
    return items


# ---------------------------------------------------------------------------
# Alteração (sempre após confirmação na interface)
# ---------------------------------------------------------------------------

def _filetime_now() -> bytes:
    return struct.pack("<Q", int((time.time() + 11644473600) * 10_000_000))


def _write_approved(hive: str, subkey: str, name: str, value: Optional[bytes]) -> None:
    path = f"{_APPROVED_BASE}\\{subkey}"
    with winreg.CreateKeyEx(_HIVES[hive], path, 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, value)


def set_enabled(item: StartupItem, enable: bool) -> tuple[bool, str]:
    """Ativa/desativa um item de inicialização (reversível)."""
    if not enable and item.protected:
        return False, f"item protegido ({item.protected_reason})"
    if item.needs_admin and not is_admin():
        return False, "este item é de todos os usuários e requer Administrador"
    _, previous = _approved_state(item.hive, item.approved_subkey, item.name)
    new_value = (b"\x02" + b"\x00" * 11) if enable else (b"\x03\x00\x00\x00" + _filetime_now())
    hive_name = "HKEY_CURRENT_USER" if item.hive == "HKCU" else "HKEY_LOCAL_MACHINE"
    restore_manager.export_registry_key(f"{hive_name}\\{_APPROVED_BASE}", f"startupapproved_{item.hive}")
    try:
        _write_approved(item.hive, item.approved_subkey, item.name, new_value)
    except OSError as exc:
        log.error(f"ERRO ao alterar inicialização de '{item.name}': {exc}")
        return False, str(exc)
    action = "Ativado" if enable else "Desativado"
    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="startup_toggle",
        description=f"{action} na inicialização: {item.name} ({item.location})",
        previous_state="ativado" if item.enabled else "desativado",
        new_state="ativado" if enable else "desativado",
        undoable=True, undo_type="startup_toggle",
        undo_data={"hive": item.hive, "subkey": item.approved_subkey, "name": item.name,
                   "previous_hex": previous.hex() if previous is not None else None},
    ))
    item.enabled = enable
    return True, f"{action}: {item.name}"


def _undo_startup(data: dict) -> tuple[bool, str]:
    if data.get("hive") == "HKLM" and not is_admin():
        return False, "requer Administrador"
    previous = data.get("previous_hex")
    try:
        _write_approved(data["hive"], data["subkey"], data["name"],
                        bytes.fromhex(previous) if previous else None)
    except (OSError, KeyError, ValueError) as exc:
        return False, str(exc)
    return True, f"estado anterior de '{data['name']}' restaurado"


restore_manager.register_undo_handler("startup_toggle", _undo_startup)

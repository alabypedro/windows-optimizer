"""Camadas de proteção centralizadas.

Todo módulo que apaga arquivos, finaliza processos, altera serviços ou itens
de inicialização consulta este módulo ANTES de agir. As listas aqui são
deliberadamente conservadoras: na dúvida, o item fica protegido.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import functools
import os
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

from utils.helpers import IS_WINDOWS

# ---------------------------------------------------------------------------
# Processos
# ---------------------------------------------------------------------------

#: Processos cujo encerramento derruba ou desestabiliza o Windows, ou que
#: fazem parte da proteção do sistema. Nunca podem ser finalizados.
PROTECTED_PROCESSES: frozenset[str] = frozenset({
    "system", "system idle process", "idle", "registry", "memory compression",
    "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "conhost.exe",
    "runtimebroker.exe", "taskhostw.exe", "spoolsv.exe", "wudfhost.exe",
    "audiodg.exe", "dllhost.exe", "searchindexer.exe", "wmiprvse.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
    "lockapp.exe", "logonui.exe", "userinit.exe", "trustedinstaller.exe",
    "tiworker.exe", "msmpeng.exe", "nissrv.exe", "mpdefendercoreservice.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "smartscreen.exe",
    "mssense.exe", "sgrmbroker.exe", "wlanext.exe", "dashost.exe",
    "applicationframehost.exe", "systemsettings.exe", "usocoreworker.exe",
    "musnotification.exe", "wuauclt.exe", "tabtip.exe",
})

#: Processos que podem ser finalizados, mas exigem aviso extra.
SENSITIVE_PROCESSES: frozenset[str] = frozenset({
    "explorer.exe", "searchhost.exe", "searchapp.exe", "onedrive.exe",
})


def is_process_protected(name: str, pid: int, exe_path: str = "") -> tuple[bool, str]:
    """Retorna (protegido, motivo)."""
    lname = (name or "").lower()
    if pid in (0, 4):
        return True, "processo do núcleo do Windows"
    if pid == os.getpid():
        return True, "é o próprio Windows Optimizer"
    if lname in PROTECTED_PROCESSES:
        return True, "processo crítico/segurança do Windows"
    if exe_path:
        lpath = exe_path.lower()
        if "windows defender" in lpath or "\\microsoft\\windows defender" in lpath:
            return True, "componente do Microsoft Defender"
    return False, ""


# ---------------------------------------------------------------------------
# Serviços
# ---------------------------------------------------------------------------

#: Serviços que o programa se recusa a alterar (segurança, atualização e núcleo).
PROTECTED_SERVICES: frozenset[str] = frozenset(s.lower() for s in {
    # Segurança
    "WinDefend", "WdNisSvc", "WdBoot", "WdFilter", "Sense", "SecurityHealthService",
    "wscsvc", "mpssvc", "BFE", "SamSs", "KeyIso", "VaultSvc", "CryptSvc",
    "SgrmBroker", "webthreatdefsvc", "webthreatdefusersvc", "EFS", "Netlogon",
    "mpsdrv", "MDCoreSvc",
    # Atualização
    "wuauserv", "UsoSvc", "WaaSMedicSvc", "BITS", "TrustedInstaller", "DoSvc",
    "InstallService", "msiserver",
    # Núcleo
    "RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "EventLog", "PlugPlay", "Power",
    "ProfSvc", "Schedule", "SENS", "SystemEventsBroker", "Winmgmt", "BrokerInfrastructure",
    "CoreMessagingRegistrar", "StateRepository", "AppXSvc", "ClipSVC", "gpsvc",
    "Appinfo", "UserManager", "ShellHWDetection", "FontCache", "EventSystem",
    "TimeBrokerSvc", "TokenBroker", "sppsvc", "DeviceInstall", "DsmSvc",
    "camsvc", "AppReadiness", "TextInputManagementService", "CDPUserSvc",
    "Themes", "UmRdpService", "LanmanServer",
    # Rede e áudio (básicos para uso normal)
    "Dhcp", "Dnscache", "nsi", "NlaSvc", "netprofm", "LanmanWorkstation",
    "WlanSvc", "Wcmsvc", "AudioSrv", "AudioEndpointBuilder", "iphlpsvc",
    "W32Time", "NcbService",
})


def _service_base_name(name: str) -> str:
    # Serviços por usuário têm sufixo aleatório (ex.: CDPUserSvc_1a2b3).
    return re.sub(r"_[0-9a-f]{4,}$", "", name.lower())


def is_service_protected(name: str, display_name: str = "") -> tuple[bool, str]:
    base = _service_base_name(name)
    if base in PROTECTED_SERVICES:
        return True, "serviço essencial, de segurança ou de atualização do Windows"
    text = f"{name} {display_name}".lower()
    for keyword in ("defender", "firewall", "security", "segurança", "windows update"):
        if keyword in text:
            return True, "relacionado a segurança/atualização"
    if base != name.lower():
        return True, "serviço por usuário gerenciado pelo Windows"
    return False, ""


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

_STARTUP_PROTECTED_PATTERNS = [
    (r"securityhealth", "Segurança do Windows"),
    (r"windows defender|msmpeng|msascui|windowsdefender", "Microsoft Defender"),
    (r"\\windows\\system32\\|\\windows\\syswow64\\|%windir%|%systemroot%", "componente do Windows"),
    (r"ctfmon|userinit|explorer\.exe", "componente do Windows"),
    # Drivers / hardware (áudio, vídeo, touchpad, Bluetooth, teclado)
    (r"realtek|rtkaud|rthdvcpl|rtkngui|waves maxx|maxxaudio|dolby|nahimic|conexant|"
     r"synaptics|syntp|elantech|etdctrl|alps|igfx|intel\(r\) graphics|igcc|"
     r"nvcontainer|nvidia|nvbackend|\bamd\b|radeon|\bccc\.exe|bthudtask|bluetooth|"
     r"logitech options|lghub|razer synapse|thunderbolt", "driver/componente de hardware"),
]


def is_startup_item_protected(name: str, command: str) -> tuple[bool, str]:
    """Itens que nunca podem ser desativados pelo programa."""
    text = f"{name} {command}".lower()
    for pattern, reason in _STARTUP_PROTECTED_PATTERNS:
        if re.search(pattern, text):
            return True, reason
    return False, ""


# ---------------------------------------------------------------------------
# Arquivos e diretórios
# ---------------------------------------------------------------------------

#: Extensões nunca apagadas em áreas de sistema, mesmo dentro de pastas temporárias.
DANGEROUS_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".dll", ".sys", ".msi", ".msu", ".msp", ".com", ".scr", ".drv",
    ".ocx", ".cpl", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".jar", ".lnk", ".ini", ".inf", ".reg", ".ttf", ".otf",
    ".fon", ".cab", ".appx", ".appxbundle", ".msix", ".cat", ".mui",
})

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_reparse_point(path: Path) -> bool:
    """Symlink ou junction (nunca seguidos, para não sair da área autorizada)."""
    try:
        st = path.lstat()
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()


def _resolve(path: Path) -> Optional[Path]:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def system_protected_roots() -> list[Path]:
    """Diretórios de sistema/programas: nunca alvo de exclusão de arquivos do usuário."""
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        windir,
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")),
        Path(os.environ.get("ProgramData", r"C:\ProgramData")),
        Path(os.environ.get("SystemDrive", "C:") + "\\Recovery"),
        Path(os.environ.get("SystemDrive", "C:") + "\\Boot"),
        Path(os.environ.get("SystemDrive", "C:") + "\\System Volume Information"),
        Path(os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin"),
    ]
    return [p for p in (_resolve(c) for c in candidates) if p is not None]


def personal_roots() -> list[Path]:
    """Pastas pessoais: nunca tocadas pela limpeza automática de temporários."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    names = ["Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music",
             "OneDrive", "Favorites", "Contacts", "Saved Games", "Links"]
    roots = [home / n for n in names]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return [p for p in (_resolve(r) for r in roots) if p is not None]


@functools.lru_cache(maxsize=1)
def _personal_roots_cached() -> tuple[Path, ...]:
    return tuple(personal_roots())


_CRITICAL_SUBDIRS_OF_WINDOWS = ("system32", "syswow64", "winsxs", "boot", "fonts",
                                "servicing", "assembly", "microsoft.net", "inf", "drivers")


def is_critical_system_path(path: Path) -> bool:
    """True para a raiz de unidades, C:\\Windows e seus subdiretórios críticos."""
    resolved = _resolve(path)
    if resolved is None:
        return True
    if resolved.parent == resolved:  # raiz de unidade (C:\)
        return True
    windir = _resolve(Path(os.environ.get("SystemRoot", r"C:\Windows")))
    if windir is not None:
        if resolved == windir:
            return True
        for sub in _CRITICAL_SUBDIRS_OF_WINDOWS:
            if _is_within(resolved, windir / sub):
                return True
    return False


def check_temp_root(root: Path) -> tuple[bool, str, str]:
    """Valida UMA VEZ a raiz de uma pasta temporária antes de varrê-la.

    Retorna (ok, motivo, raiz_resolvida). A raiz não pode ser
    symlink/junction, crítica do sistema, nem ficar dentro de (ou conter)
    uma pasta pessoal.
    """
    if is_reparse_point(root):
        return False, "a pasta é um link simbólico/junction", ""
    resolved = _resolve(root)
    if resolved is None:
        return False, "caminho não pôde ser resolvido", ""
    if is_critical_system_path(resolved):
        return False, "diretório crítico do sistema", ""
    for personal in _personal_roots_cached():
        if _is_within(resolved, personal) or _is_within(personal, resolved):
            return False, "pasta pessoal protegida", ""
    return True, "", str(resolved)


def is_safe_temp_file(path: str, file_attributes: int, root: str,
                      protect_executables: bool = True) -> tuple[bool, str]:
    """Checagem rápida por arquivo (sem syscalls extras) dentro de uma raiz
    já validada por :func:`check_temp_root`.

    Como a varredura nunca desce por symlinks/junctions, basta confirmar que
    o próprio arquivo não é um reparse point, que o caminho está dentro da
    raiz e que a extensão não é protegida.
    """
    if file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return False, "link simbólico/junction (não seguido)"
    norm = os.path.normcase(os.path.normpath(path))
    if not norm.startswith(os.path.normcase(root).rstrip("\\") + "\\"):
        return False, "fora da pasta autorizada"
    if protect_executables and os.path.splitext(norm)[1] in DANGEROUS_EXTENSIONS:
        return False, "extensão de sistema/executável protegida"
    return True, ""


def is_safe_temp_delete(
    path: Path,
    authorized_root: Path,
    protect_executables: bool = True,
) -> tuple[bool, str]:
    """Validação completa (mais lenta) de um arquivo em pasta temporária.

    Regras (todas precisam passar):
      1. a raiz é válida (:func:`check_temp_root`);
      2. o arquivo não é symlink/junction;
      3. após resolução, está DENTRO da raiz (e não é a própria raiz);
      4. extensão não é executável/sistema (quando ``protect_executables``).
    """
    ok, reason, root = check_temp_root(authorized_root)
    if not ok:
        return False, reason
    if is_reparse_point(path):
        return False, "link simbólico/junction (não seguido)"
    resolved = _resolve(path)
    if resolved is None:
        return False, "caminho não pôde ser resolvido"
    return is_safe_temp_file(str(resolved), 0, root, protect_executables)


def is_safe_user_file_delete(path: Path) -> tuple[bool, str]:
    """Valida o envio à Lixeira de um arquivo escolhido pelo usuário
    (arquivos grandes/duplicados). Bloqueia qualquer coisa de sistema."""
    if is_reparse_point(path):
        return False, "link simbólico/junction"
    resolved = _resolve(path)
    if resolved is None:
        return False, "caminho inválido"
    if is_critical_system_path(resolved):
        return False, "diretório crítico do sistema"
    for root in system_protected_roots():
        if _is_within(resolved, root):
            return False, f"dentro de pasta de sistema/programas ({root})"
    try:
        if not resolved.is_file():
            return False, "não é um arquivo"
    except OSError:
        return False, "sem acesso"
    return True, ""


# ---------------------------------------------------------------------------
# Envio para a Lixeira (recuperável) via SHFileOperationW
# ---------------------------------------------------------------------------

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


class _SHFILEOPSTRUCTW(ctypes.Structure):
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_recycle_bin(path: Path) -> tuple[bool, str]:
    """Move UM arquivo para a Lixeira (o usuário pode restaurá-lo).

    FOF_WANTNUKEWARNING faz o Windows perguntar caso o arquivo NÃO possa ir
    para a Lixeira (ex.: grande demais ou unidade removível), em vez de
    apagar permanentemente em silêncio.
    """
    if not IS_WINDOWS:
        return False, "não suportado"
    safe, reason = is_safe_user_file_delete(path)
    if not safe:
        return False, reason
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = str(path.resolve()) + "\0"  # lista terminada em nulo duplo
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        return False, f"SHFileOperation retornou 0x{result:X}"
    if op.fAnyOperationsAborted:
        return False, "operação cancelada"
    if path.exists():
        return False, "arquivo ainda existe (em uso?)"
    return True, "enviado para a Lixeira"


def filter_existing(paths: Iterable[Path]) -> list[Path]:
    out = []
    for p in paths:
        try:
            if p.exists():
                out.append(p)
        except OSError:
            continue
    return out


def dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for p in paths:
        resolved = _resolve(p)
        key = str(resolved or p).lower()
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

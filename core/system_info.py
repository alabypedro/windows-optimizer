"""Coleta de informações do sistema (somente leitura).

Nada neste módulo altera o sistema. Consultas lentas (PowerShell/CIM) têm
timeout e falham de forma silenciosa, retornando "desconhecido".
"""

from __future__ import annotations

import os
import sys
import time
import winreg
from dataclasses import dataclass, field
from typing import Optional

import psutil

from utils.helpers import as_list, run_command, run_powershell_json
from utils.logger import get_logger

log = get_logger("system_info")


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

@dataclass
class ProcessUsage:
    pid: int
    name: str
    cpu_percent: float
    memory_bytes: int
    exe: str = ""


@dataclass
class InstalledProgram:
    name: str
    version: str = ""
    publisher: str = ""
    install_date: str = ""
    size_bytes: int = 0
    scope: str = ""


@dataclass
class SecurityStatus:
    defender_available: bool = False
    antivirus_enabled: Optional[bool] = None
    realtime_enabled: Optional[bool] = None
    signature_age_days: Optional[int] = None
    other_antivirus: list[str] = field(default_factory=list)
    firewall_enabled: Optional[bool] = None
    detail: str = ""


@dataclass
class UpdateStatus:
    service_state: str = "desconhecido"
    service_start: str = "desconhecido"
    reboot_pending: bool = False
    pending_updates: Optional[list[str]] = None  # None = ainda não verificado
    detail: str = ""


@dataclass
class HardwareProfile:
    cpu_name: str
    physical_cores: int
    logical_cores: int
    max_mhz: float
    ram_total: int
    system_drive: str
    system_disk_type: str  # "SSD", "HDD" ou "Desconhecido"
    system_free_pct: float
    has_battery: bool

    @property
    def low_ram(self) -> bool:
        return self.ram_total < 8 * 1024 ** 3

    @property
    def very_low_ram(self) -> bool:
        return self.ram_total < 4.5 * 1024 ** 3

    @property
    def weak_cpu(self) -> bool:
        return self.logical_cores <= 4

    @property
    def low_disk_space(self) -> bool:
        return self.system_free_pct < 15


@dataclass
class SystemSnapshot:
    windows_version: str
    computer_name: str
    cpu_name: str
    cpu_percent: float
    physical_cores: int
    logical_cores: int
    ram_total: int
    ram_used: int
    ram_percent: float
    uptime_seconds: float
    boot_time: float
    drives: list[dict]
    top_cpu: list[ProcessUsage]
    top_ram: list[ProcessUsage]
    temperatures: dict[str, str]
    hardware: HardwareProfile
    collected_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Sistema operacional / CPU
# ---------------------------------------------------------------------------

def windows_version() -> str:
    try:
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
    except AttributeError:
        return "Desconhecido"
    name = "Windows 11" if build >= 22000 else "Windows 10" if build >= 10240 else "Windows"
    edition, display = "", ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            edition = _reg_value(key, "EditionID") or ""
            display = _reg_value(key, "DisplayVersion") or _reg_value(key, "ReleaseId") or ""
            ubr = _reg_value(key, "UBR")
            if ubr is not None:
                build = f"{build}.{ubr}"
    except OSError:
        pass
    parts = [name, edition, display]
    return " ".join(p for p in parts if p) + f" (build {build})"


def _reg_value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def cpu_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            value = _reg_value(key, "ProcessorNameString")
            if value:
                return " ".join(str(value).split())
    except OSError:
        pass
    return os.environ.get("PROCESSOR_IDENTIFIER", "Desconhecido")


def uptime_seconds() -> float:
    return max(0.0, time.time() - psutil.boot_time())


# ---------------------------------------------------------------------------
# Processos
# ---------------------------------------------------------------------------

def sample_processes(interval: float = 0.8) -> list[ProcessUsage]:
    """Mede CPU de todos os processos em duas amostras (sem loop contínuo)."""
    procs = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            proc.cpu_percent(None)
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(interval)
    ncpu = psutil.cpu_count() or 1
    result: list[ProcessUsage] = []
    for proc in procs:
        try:
            with proc.oneshot():
                cpu = proc.cpu_percent(None) / ncpu
                mem = proc.memory_info().rss
                try:
                    exe = proc.exe()
                except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    exe = ""
            name = proc.info.get("name") or ""
            if proc.pid == 0:
                continue
            result.append(ProcessUsage(proc.pid, name, round(cpu, 1), mem, exe))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


# ---------------------------------------------------------------------------
# Unidades
# ---------------------------------------------------------------------------

def list_drives() -> list[dict]:
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        drives.append({
            "mountpoint": part.mountpoint,
            "letter": part.mountpoint.rstrip("\\").rstrip(":"),
            "fstype": part.fstype,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
            "removable": "removable" in part.opts,
        })
    return drives


_media_cache: dict[str, str] | None = None


def drive_media_types(refresh: bool = False) -> dict[str, str]:
    """Mapeia letra da unidade -> "SSD"/"HDD"/"Desconhecido" (via Storage CIM)."""
    global _media_cache
    if _media_cache is not None and not refresh:
        return _media_cache
    data = run_powershell_json(
        "$disks = @{}; Get-PhysicalDisk | ForEach-Object { $disks[[string]$_.DeviceId] = "
        "@{Media=[string]$_.MediaType; Bus=[string]$_.BusType; Model=$_.FriendlyName} }; "
        "Get-Partition | Where-Object DriveLetter | ForEach-Object { $d = $disks[[string]$_.DiskNumber]; "
        "[pscustomobject]@{Letter=[string]$_.DriveLetter; Media=$d.Media; Bus=$d.Bus; Model=$d.Model} } "
        "| ConvertTo-Json -Compress", timeout=45)
    mapping: dict[str, str] = {}
    for item in as_list(data):
        letter = str(item.get("Letter") or "").upper()
        media = str(item.get("Media") or "")
        bus = str(item.get("Bus") or "")
        if media == "SSD" or bus == "NVMe":
            mapping[letter] = "SSD"
        elif media == "HDD":
            mapping[letter] = "HDD"
        else:
            mapping[letter] = "Desconhecido"
        mapping[letter + ":model"] = str(item.get("Model") or "")
    _media_cache = mapping
    return mapping


def has_battery() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Temperaturas (melhor esforço)
# ---------------------------------------------------------------------------

def read_temperatures() -> dict[str, str]:
    """Temperaturas apenas quando há fonte confiável.

    - GPU NVIDIA: nvidia-smi (valor do próprio driver).
    - CPU: o Windows não expõe temperatura de CPU de forma padronizada; a
      zona térmica ACPI (requer Admin) é aproximada e muitas vezes fixa,
      então é exibida apenas como "zona térmica ACPI (aproximada)".
    """
    temps: dict[str, str] = {}
    smi = run_command(["nvidia-smi", "--query-gpu=name,temperature.gpu",
                       "--format=csv,noheader,nounits"], timeout=8)
    if smi.ok and smi.stdout.strip():
        for line in smi.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[1].isdigit():
                temps[f"GPU {parts[0]}"] = f"{parts[1]} °C"
    data = run_powershell_json(
        "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
        "-ErrorAction Stop | Select-Object InstanceName,CurrentTemperature | ConvertTo-Json -Compress",
        timeout=15)
    for i, item in enumerate(as_list(data)):
        try:
            celsius = float(item["CurrentTemperature"]) / 10 - 273.15
        except (KeyError, TypeError, ValueError):
            continue
        if 5 < celsius < 110:
            temps[f"Zona térmica ACPI {i + 1} (aproximada)"] = f"{celsius:.0f} °C"
    return temps


# ---------------------------------------------------------------------------
# Programas instalados
# ---------------------------------------------------------------------------

_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "Todos (64 bits)"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "Todos (32 bits)"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "Usuário atual"),
]


def installed_programs() -> list[InstalledProgram]:
    programs: dict[str, InstalledProgram] = {}
    for hive, path, scope in _UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = _reg_value(key, "DisplayName")
                        if not name or _reg_value(key, "SystemComponent") == 1:
                            continue
                        if _reg_value(key, "ParentKeyName"):  # atualizações/patches
                            continue
                        size_kb = _reg_value(key, "EstimatedSize") or 0
                        prog = InstalledProgram(
                            name=str(name).strip(),
                            version=str(_reg_value(key, "DisplayVersion") or ""),
                            publisher=str(_reg_value(key, "Publisher") or ""),
                            install_date=str(_reg_value(key, "InstallDate") or ""),
                            size_bytes=int(size_kb) * 1024 if isinstance(size_kb, int) else 0,
                            scope=scope,
                        )
                        programs.setdefault(prog.name.lower(), prog)
                except OSError:
                    continue
    return sorted(programs.values(), key=lambda p: p.name.lower())


# ---------------------------------------------------------------------------
# Segurança e Windows Update (somente leitura)
# ---------------------------------------------------------------------------

def security_status() -> SecurityStatus:
    status = SecurityStatus()
    data = run_powershell_json(
        "Get-MpComputerStatus -ErrorAction Stop | Select-Object AntivirusEnabled,"
        "RealTimeProtectionEnabled,AntivirusSignatureAge | ConvertTo-Json -Compress", timeout=30)
    if isinstance(data, dict):
        status.defender_available = True
        status.antivirus_enabled = bool(data.get("AntivirusEnabled"))
        status.realtime_enabled = bool(data.get("RealTimeProtectionEnabled"))
        age = data.get("AntivirusSignatureAge")
        status.signature_age_days = int(age) if isinstance(age, int) and age < 10000 else None
    av = run_powershell_json(
        "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct "
        "-ErrorAction Stop | Select-Object displayName,productState | ConvertTo-Json -Compress",
        timeout=20)
    for item in as_list(av):
        name = str(item.get("displayName") or "")
        if name and "defender" not in name.lower():
            status.other_antivirus.append(name)
    fw = run_powershell_json(
        "Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name,Enabled | ConvertTo-Json -Compress",
        timeout=20)
    profiles = as_list(fw)
    if profiles:
        status.firewall_enabled = all(bool(p.get("Enabled")) for p in profiles)
    if not status.defender_available and not status.other_antivirus:
        status.detail = "Não foi possível consultar o status do antivírus."
    return status


def _reboot_pending() -> bool:
    keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
    ]
    for path in keys:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
            return True
        except OSError:
            continue
    return False


def update_status() -> UpdateStatus:
    status = UpdateStatus()
    try:
        svc = psutil.win_service_get("wuauserv")
        status.service_state = {"running": "Em execução", "stopped": "Parado"}.get(
            svc.status(), svc.status())
        status.service_start = {"automatic": "Automático", "manual": "Manual (sob demanda)",
                                "disabled": "Desativado"}.get(svc.start_type(), svc.start_type())
    except Exception as exc:
        status.detail = f"Não foi possível consultar o serviço: {exc}"
    status.reboot_pending = _reboot_pending()
    return status


def search_pending_updates(timeout: float = 240) -> tuple[Optional[list[str]], str]:
    """Pesquisa atualizações pendentes via API do Windows Update (somente leitura).

    Não baixa nem instala nada. Pode levar alguns minutos.
    """
    data = run_powershell_json(
        "$s = New-Object -ComObject Microsoft.Update.Session; "
        "$r = $s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); "
        "@($r.Updates | ForEach-Object { $_.Title }) | ConvertTo-Json -Compress",
        timeout=timeout)
    if data is None:
        return None, "Não foi possível consultar o Windows Update (sem conexão ou tempo excedido)."
    titles = [str(t) for t in as_list(data)]
    return titles, f"{len(titles)} atualização(ões) pendente(s)."


# ---------------------------------------------------------------------------
# Snapshot completo
# ---------------------------------------------------------------------------

def hardware_profile(drives: Optional[list[dict]] = None) -> HardwareProfile:
    drives = drives if drives is not None else list_drives()
    system_letter = os.environ.get("SystemDrive", "C:").rstrip(":").upper()
    sys_drive = next((d for d in drives if d["letter"].upper() == system_letter), None)
    media = drive_media_types().get(system_letter, "Desconhecido")
    freq = psutil.cpu_freq()
    return HardwareProfile(
        cpu_name=cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        logical_cores=psutil.cpu_count() or 1,
        max_mhz=freq.max if freq else 0.0,
        ram_total=psutil.virtual_memory().total,
        system_drive=f"{system_letter}:",
        system_disk_type=media,
        system_free_pct=(100 - sys_drive["percent"]) if sys_drive else 100.0,
        has_battery=has_battery(),
    )


def collect_snapshot(include_temperatures: bool = True) -> SystemSnapshot:
    log.info("INÍCIO coleta de diagnóstico do sistema")
    psutil.cpu_percent(None)
    processes = sample_processes()
    cpu_total = psutil.cpu_percent(None)
    mem = psutil.virtual_memory()
    drives = list_drives()
    hardware = hardware_profile(drives)
    ignored = {"system idle process", "idle", "memory compression", "memcompression",
               "system", "registry", "secure system"}
    candidates = [p for p in processes if p.name.lower() not in ignored]
    snap = SystemSnapshot(
        windows_version=windows_version(),
        computer_name=os.environ.get("COMPUTERNAME", ""),
        cpu_name=hardware.cpu_name,
        cpu_percent=cpu_total,
        physical_cores=hardware.physical_cores,
        logical_cores=hardware.logical_cores,
        ram_total=mem.total,
        ram_used=mem.total - mem.available,
        ram_percent=mem.percent,
        uptime_seconds=uptime_seconds(),
        boot_time=psutil.boot_time(),
        drives=drives,
        top_cpu=sorted(candidates, key=lambda p: p.cpu_percent, reverse=True)[:10],
        top_ram=sorted(candidates, key=lambda p: p.memory_bytes, reverse=True)[:10],
        temperatures=read_temperatures() if include_temperatures else {},
        hardware=hardware,
    )
    log.info(f"FIM coleta: {snap.windows_version}, RAM {mem.percent}%, CPU {cpu_total}%, "
             f"disco do sistema {hardware.system_disk_type}")
    return snap

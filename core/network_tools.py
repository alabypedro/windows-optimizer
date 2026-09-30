"""Ferramentas de rede (opcionais). Nenhuma configuração é alterada de forma
permanente: flush de DNS e renovação de IP são operações temporárias que o
próprio Windows refaz automaticamente."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

import psutil

from utils.helpers import CommandResult, run_command
from utils.logger import get_logger

log = get_logger("network")


@dataclass
class Adapter:
    name: str
    is_up: bool
    speed_mbps: int
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    mac: str = ""


def list_adapters() -> list[Adapter]:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    adapters = []
    for name, addr_list in addrs.items():
        st = stats.get(name)
        a = Adapter(name, bool(st and st.isup), int(st.speed) if st else 0)
        for addr in addr_list:
            if addr.family == socket.AF_INET:
                a.ipv4.append(addr.address)
            elif addr.family == socket.AF_INET6:
                a.ipv6.append(addr.address.split("%")[0])
            elif addr.family == psutil.AF_LINK:
                a.mac = addr.address
        adapters.append(a)
    return sorted(adapters, key=lambda a: (not a.is_up, a.name.lower()))


def _logged(action: str, result: CommandResult) -> CommandResult:
    if result.ok:
        log.info(f"{action}: OK")
    else:
        log.error(f"ERRO em {action}: {result.summary()}")
    return result


def flush_dns() -> CommandResult:
    return _logged("Flush DNS (ipconfig /flushdns)", run_command(["ipconfig", "/flushdns"], timeout=30))


def release_ip() -> CommandResult:
    """Libera o IP (a conexão cai até o renew!). Use com confirmação."""
    return _logged("Liberar IP (ipconfig /release)", run_command(["ipconfig", "/release"], timeout=60))


def renew_ip() -> CommandResult:
    return _logged("Renovar IP (ipconfig /renew)", run_command(["ipconfig", "/renew"], timeout=120))


def release_and_renew() -> tuple[CommandResult, CommandResult]:
    rel = release_ip()
    time.sleep(1)
    ren = renew_ip()
    return rel, ren


def test_connection(host: str = "8.8.8.8", count: int = 4) -> CommandResult:
    return run_command(["ping", "-n", str(count), "-w", "2000", host], timeout=30)


def test_dns(hostnames: tuple[str, ...] = ("www.microsoft.com", "www.google.com", "cloudflare.com")) -> list[str]:
    lines = []
    for host in hostnames:
        start = time.perf_counter()
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            ms = (time.perf_counter() - start) * 1000
            ips = sorted({i[4][0] for i in infos})[:3]
            lines.append(f"OK   {host} -> {', '.join(ips)} ({ms:.0f} ms)")
        except socket.gaierror as exc:
            lines.append(f"FALHA {host}: {exc}")
    return lines


def test_tcp(host: str = "www.microsoft.com", port: int = 443, timeout: float = 5) -> str:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return f"Conexão TCP com {host}:{port} OK ({(time.perf_counter() - start) * 1000:.0f} ms)"
    except OSError as exc:
        return f"Falha na conexão TCP com {host}:{port}: {exc}"

"""Relatório da sessão: problemas, espaço recuperável, inicialização,
processos pesados, recomendações, otimizações aplicadas e erros.

O relatório é salvo em logs/report_YYYY-MM-DD_HHMMSS.txt (UTF-8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.recommendations import Analysis, build_recommendations, compute_health
from utils.helpers import format_duration, format_size, logs_dir


@dataclass
class SessionLog:
    """Acumula o que foi aplicado e os erros durante a sessão atual."""
    applied: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, text: str, ok: bool = True, freed: int = 0) -> None:
        with self._lock:
            (self.applied if ok else self.errors).append(f"{datetime.now():%H:%M:%S} {text}")
            self.freed_bytes += freed


SESSION = SessionLog()


def build_report(analysis: Optional[Analysis], session: SessionLog = SESSION) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 70)
    add("RELATÓRIO — WINDOWS OPTIMIZER")
    add(f"Gerado em: {datetime.now():%d/%m/%Y %H:%M:%S}")
    add("=" * 70)

    if analysis is None:
        add("\nNenhuma análise foi executada nesta sessão (use 'ANALISAR PC').")
    else:
        s = analysis.snapshot
        score, factors = compute_health(analysis)
        add(f"\nSistema: {s.windows_version} | {s.computer_name}")
        add(f"CPU: {s.cpu_name} ({s.physical_cores} núcleos / {s.logical_cores} threads)")
        add(f"RAM: {format_size(s.ram_used)} de {format_size(s.ram_total)} ({s.ram_percent:.0f}%)")
        add(f"Disco do sistema: {s.hardware.system_disk_type}, {s.hardware.system_free_pct:.0f}% livre")
        add(f"Ligado há: {format_duration(s.uptime_seconds)}")
        add(f"\nPONTUAÇÃO DE SAÚDE: {score}/100")
        for f in factors:
            mark = f"-{f.penalty}" if f.penalty else " ok"
            add(f"  [{mark:>4}] {f.name}: {f.detail}")

        add("\nPROBLEMAS ENCONTRADOS")
        problems = [f for f in factors if f.level in ("atenção", "crítico")]
        if not problems:
            add("  Nenhum problema relevante.")
        for f in problems:
            add(f"  - ({f.level}) {f.name}: {f.detail}")

        add("\nESPAÇO QUE PODE SER LIBERADO")
        for scan in analysis.temp_scans:
            add(f"  - {scan.category.name}: {format_size(scan.total_bytes)} ({scan.file_count} arquivos)")
        for b, scan in analysis.browser_scans:
            add(f"  - Cache do {b.name}: {format_size(scan.total_bytes)}")
        add(f"  - Lixeira: {format_size(analysis.recycle_bin[1])} ({analysis.recycle_bin[0]} itens)")
        add(f"  TOTAL temporários + navegadores: "
            f"{format_size(analysis.temp_bytes + analysis.browser_bytes)}")

        add("\nPROGRAMAS DE INICIALIZAÇÃO (ativos)")
        for item in analysis.enabled_startup:
            prot = " [protegido]" if item.protected else ""
            add(f"  - {item.name} | {item.publisher or '?'} | {item.impact}{prot}")

        add("\nPROCESSOS MAIS PESADOS (RAM)")
        for p in s.top_ram[:8]:
            add(f"  - {p.name} (PID {p.pid}): {format_size(p.memory_bytes)}")
        add("\nPROCESSOS MAIS PESADOS (CPU)")
        for p in s.top_cpu[:8]:
            add(f"  - {p.name} (PID {p.pid}): {p.cpu_percent:.1f}%")

        add("\nRECOMENDAÇÕES")
        for r in build_recommendations(analysis):
            add(f"  - [{r.priority}] {r.title}: {r.detail}")
        if analysis.errors:
            add("\nERROS DURANTE A ANÁLISE")
            for e in analysis.errors:
                add(f"  - {e}")

    add("\nOTIMIZAÇÕES APLICADAS NESTA SESSÃO")
    if not session.applied:
        add("  Nenhuma.")
    for a in session.applied:
        add(f"  - {a}")
    add(f"  Espaço liberado na sessão: {format_size(session.freed_bytes)}")
    add("\nERROS ENCONTRADOS NESTA SESSÃO")
    if not session.errors:
        add("  Nenhum.")
    for e in session.errors:
        add(f"  - {e}")
    return "\n".join(line for line in lines if line is not None)


def save_report(text: str) -> Path:
    path = logs_dir() / f"report_{datetime.now():%Y-%m-%d_%H%M%S}.txt"
    path.write_text(text, encoding="utf-8")
    return path

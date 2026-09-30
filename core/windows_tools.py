"""Ferramentas nativas do Windows: planos de energia e manutenção (SFC/DISM/CHKDSK).

Somente comandos oficiais e não destrutivos:
  * SFC /scannow                          – verifica/repara arquivos do sistema
  * DISM /CheckHealth | /ScanHealth       – somente verificação
  * DISM /RestoreHealth                   – repara a imagem do Windows
  * DISM /StartComponentCleanup           – limpeza segura do WinSxS
      (NUNCA /ResetBase, que torna atualizações irreversíveis)
  * CHKDSK <unidade>  (somente leitura) ou /scan (verificação online NTFS)
      (NUNCA /f ou /r automaticamente: exigem reinício e bloqueiam a unidade)
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from core import restore_manager
from utils.admin import is_admin
from utils.helpers import run_command, stream_command
from utils.logger import get_logger

log = get_logger("windows_tools")

_GUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
                      r"\s+\((.+?)\)(\s*\*)?")

KNOWN_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Equilibrado — recomendado para a maioria dos PCs.",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "Alto desempenho — CPU sempre pronta; consome mais energia.",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Economia de energia — reduz desempenho para poupar bateria.",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "Desempenho máximo — para estações de trabalho; mais consumo.",
}


@dataclass
class PowerPlan:
    guid: str
    name: str
    active: bool

    @property
    def description(self) -> str:
        return KNOWN_PLANS.get(self.guid.lower(), "Plano personalizado ou do fabricante.")


def list_power_plans() -> list[PowerPlan]:
    result = run_command(["powercfg", "/list"], timeout=15)
    plans = []
    for match in _GUID_RE.finditer(result.stdout):
        plans.append(PowerPlan(match.group(1).lower(), match.group(2).strip(), bool(match.group(3))))
    return plans


def active_power_plan() -> Optional[PowerPlan]:
    return next((p for p in list_power_plans() if p.active), None)


def set_power_plan(plan: PowerPlan) -> tuple[bool, str]:
    """Ativa um plano de energia (após confirmação). Reversível."""
    previous = active_power_plan()
    result = run_command(["powercfg", "/setactive", plan.guid], timeout=15)
    if not result.ok:
        log.error(f"ERRO ao ativar plano {plan.name}: {result.summary()}")
        return False, result.summary()
    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="power_plan",
        description=f"Plano de energia: {previous.name if previous else '?'} → {plan.name}",
        previous_state=previous.guid if previous else None, new_state=plan.guid,
        undoable=previous is not None, undo_type="power_plan",
        undo_data={"guid": previous.guid if previous else "", "name": previous.name if previous else ""},
    ))
    return True, f"Plano '{plan.name}' ativado"


def _undo_power_plan(data: dict) -> tuple[bool, str]:
    result = run_command(["powercfg", "/setactive", data["guid"]], timeout=15)
    return result.ok, f"plano '{data.get('name')}' reativado" if result.ok else result.summary()


restore_manager.register_undo_handler("power_plan", _undo_power_plan)


# ---------------------------------------------------------------------------
# Manutenção
# ---------------------------------------------------------------------------

@dataclass
class MaintenanceTool:
    key: str
    name: str
    description: str
    args: list[str]
    duration: str
    requires_admin: bool = True
    confirm_text: str = ""


def maintenance_tools(drive_letter: str = "C") -> list[MaintenanceTool]:
    drive = f"{drive_letter.rstrip(':')}:"
    return [
        MaintenanceTool("sfc", "SFC — Verificar arquivos do sistema",
                        "Verifica a integridade dos arquivos protegidos do Windows e repara os corrompidos "
                        "usando cópias em cache.", ["sfc.exe", "/scannow"], "10–30 min"),
        MaintenanceTool("dism_check", "DISM — Verificação rápida (CheckHealth)",
                        "Verifica se a imagem do Windows foi marcada como corrompida. Não altera nada.",
                        ["dism.exe", "/Online", "/Cleanup-Image", "/CheckHealth"], "< 1 min"),
        MaintenanceTool("dism_scan", "DISM — Verificação completa (ScanHealth)",
                        "Varre a imagem do Windows em busca de corrupção. Não altera nada.",
                        ["dism.exe", "/Online", "/Cleanup-Image", "/ScanHealth"], "5–20 min"),
        MaintenanceTool("dism_restore", "DISM — Reparar imagem (RestoreHealth)",
                        "Repara a imagem do Windows baixando componentes do Windows Update. "
                        "Recomendado quando SFC não consegue reparar.",
                        ["dism.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"], "10–40 min",
                        confirm_text="O DISM pode usar a internet e levar vários minutos. "
                                     "Não desligue o computador durante o processo."),
        MaintenanceTool("dism_cleanup", "Limpeza de componentes do Windows (WinSxS)",
                        "Remove versões antigas de componentes substituídas por atualizações "
                        "(/StartComponentCleanup, sem /ResetBase — atualizações continuam desinstaláveis).",
                        ["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], "5–30 min",
                        confirm_text="A limpeza de componentes pode demorar e usar bastante disco."),
        MaintenanceTool("chkdsk_ro", f"CHKDSK — Verificação somente leitura ({drive})",
                        "Verifica o sistema de arquivos sem corrigir nada e sem reiniciar.",
                        ["chkdsk.exe", drive], "5–30 min",
                        confirm_text="O CHKDSK em modo somente leitura não corrige erros; se encontrar "
                                     "problemas, ele indicará como agendar a correção."),
        MaintenanceTool("chkdsk_scan", f"CHKDSK — Verificação online ({drive} /scan)",
                        "Verificação online do NTFS; problemas encontrados são enfileirados para reparo "
                        "(spotfix) sem desmontar a unidade.",
                        ["chkdsk.exe", drive, "/scan"], "5–30 min",
                        confirm_text="Executa a verificação online do CHKDSK na unidade selecionada."),
    ]


def run_maintenance(tool: MaintenanceTool, on_line: Callable[[str], None],
                    cancel: Optional[threading.Event] = None) -> int:
    if tool.requires_admin and not is_admin():
        on_line("Esta ferramenta requer Administrador. Use 'Reiniciar como Administrador'.")
        return -1
    log.info(f"INÍCIO manutenção: {tool.name} ({' '.join(tool.args)})")
    lines: list[str] = []

    def collect(line: str) -> None:
        lines.append(line)
        on_line(line)

    code = stream_command(tool.args, collect, cancel)
    tail = " | ".join(lines[-3:])
    level = log.info if code == 0 else log.warning
    level(f"FIM manutenção: {tool.name}: código {code}. Final da saída: {tail}")
    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="maintenance", description=f"Executado: {tool.name}",
        new_state=f"código de saída {code}", undoable=False, result=tail[:500],
    ))
    return code

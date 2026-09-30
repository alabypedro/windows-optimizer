"""Análise de serviços do Windows.

Este módulo NÃO desativa nada automaticamente. Ele:
  * lista os serviços e o tipo de inicialização;
  * explica a função dos serviços mais comuns (base de conhecimento abaixo);
  * sugere mudanças conservadoras (em geral "Manual", que mantém o serviço
    disponível sob demanda) apenas quando fazem sentido;
  * aplica mudanças somente após confirmação, com registro para desfazer.

Serviços de segurança, atualização e do núcleo do sistema são protegidos
(core.security.PROTECTED_SERVICES) e não podem ser alterados.
"""

from __future__ import annotations

import os
import winreg
from dataclasses import dataclass
from typing import Optional

import psutil

from core import restore_manager
from core.security import is_service_protected
from utils.admin import is_admin
from utils.helpers import run_command
from utils.logger import get_logger

log = get_logger("services")

START_LABELS = {
    "automatic": "Automático",
    "delayed": "Automático (atraso)",
    "manual": "Manual",
    "disabled": "Desativado",
}
_SC_START = {"automatic": "auto", "delayed": "delayed-auto", "manual": "demand", "disabled": "disabled"}


@dataclass
class ServiceKnowledge:
    explanation: str
    suggestion: str = ""             # texto da recomendação
    suggested_start: Optional[str] = None  # "manual" etc.; None = manter


# Base de conhecimento: função do serviço + recomendação conservadora.
KNOWLEDGE: dict[str, ServiceKnowledge] = {
    "fax": ServiceKnowledge("Envio e recebimento de fax.",
                            "Se você não usa fax, pode ficar em Manual.", "manual"),
    "xblauthmanager": ServiceKnowledge("Autenticação do Xbox Live (jogos da Microsoft Store/Game Pass).",
                                       "Se não joga pelo app Xbox, pode ficar em Manual.", "manual"),
    "xblgamesave": ServiceKnowledge("Sincronização de jogos salvos do Xbox Live.",
                                    "Se não joga pelo app Xbox, pode ficar em Manual.", "manual"),
    "xboxnetapisvc": ServiceKnowledge("Rede do Xbox Live.",
                                      "Se não joga pelo app Xbox, pode ficar em Manual.", "manual"),
    "xboxgipsvc": ServiceKnowledge("Acessórios Xbox (controles).",
                                   "Necessário para atualizar controles Xbox; Manual é suficiente.", "manual"),
    "mapsbroker": ServiceKnowledge("Gerenciador de mapas baixados (app Mapas).",
                                   "Raramente usado; Manual (ou Automático com atraso) é suficiente.", "manual"),
    "retaildemo": ServiceKnowledge("Modo de demonstração de lojas.",
                                   "Não é necessário em computadores pessoais.", "manual"),
    "wmpnetworksvc": ServiceKnowledge("Compartilhamento de rede do Windows Media Player.",
                                      "Se não compartilha mídia pela rede, pode ficar em Manual.", "manual"),
    "remoteregistry": ServiceKnowledge("Permite editar o Registro deste PC remotamente.",
                                       "Recomendado manter desativado/Manual em PCs domésticos.", "manual"),
    "diagtrack": ServiceKnowledge("Experiências do Usuário Conectado e Telemetria (envio de diagnósticos à Microsoft).",
                                  "Opcional. Colocar em Manual reduz atividade em segundo plano; não afeta "
                                  "segurança nem atualizações.", "manual"),
    "dmwappushservice": ServiceKnowledge("Roteamento de mensagens WAP (dispositivos gerenciados).",
                                         "Normalmente dispensável em PCs domésticos.", "manual"),
    "phonesvc": ServiceKnowledge("Telefonia (chamadas via celular vinculado).",
                                 "Manual é suficiente.", "manual"),
    "lfsvc": ServiceKnowledge("Serviço de geolocalização.",
                              "Necessário para apps que usam localização. Manual é suficiente.", "manual"),
    "semgrsvc": ServiceKnowledge("Pagamentos e NFC/SE.", "Raramente usado em desktops.", "manual"),
    "wpcmonsvc": ServiceKnowledge("Controle dos Pais (Family Safety).",
                                  "Se não usa controle parental, Manual é suficiente.", "manual"),
    "icssvc": ServiceKnowledge("Hotspot móvel do Windows.", "Manual é suficiente.", "manual"),
    "scardsvr": ServiceKnowledge("Leitor de cartão inteligente (smart card / certificado digital em cartão).",
                                 "Mantenha se usa certificado digital em cartão/token."),
    "trkwks": ServiceKnowledge("Rastreamento de links distribuídos (atalhos para arquivos em rede NTFS).",
                               "Em PCs domésticos pode ficar em Manual.", "manual"),
    "wisvc": ServiceKnowledge("Programa Windows Insider.", "Se não participa do Insider, pode ficar em Manual.",
                              "manual"),
    "ajrouter": ServiceKnowledge("Roteador AllJoyn (IoT).", "Raramente usado.", "manual"),
    "alg": ServiceKnowledge("Gateway de camada de aplicativo (compartilhamento de internet legado).",
                            "Manual é suficiente.", "manual"),
    "sharedaccess": ServiceKnowledge("Compartilhamento de Conexão com a Internet (ICS).",
                                     "Necessário apenas se compartilha a internet deste PC.", "manual"),
    "spooler": ServiceKnowledge("Spooler de impressão (impressoras e 'Microsoft Print to PDF').",
                                "Mantenha: é necessário para imprimir e gerar PDFs."),
    "sysmain": ServiceKnowledge("SysMain (antigo Superfetch): pré-carrega apps usados com frequência na RAM.",
                                "Mantenha ativo, especialmente em HDD; o Windows gerencia a memória usada."),
    "wsearch": ServiceKnowledge("Windows Search: indexação para buscas rápidas no Menu Iniciar e Explorer.",
                                "Mantenha ativo; em CPUs modestas, 'Automático (atraso)' pode suavizar o boot."),
    "bthserv": ServiceKnowledge("Suporte a Bluetooth.", "Mantenha se usa dispositivos Bluetooth."),
    "tabletinputservice": ServiceKnowledge("Teclado virtual e escrita à tinta.", "Mantenha (usado pelo teclado de toque)."),
    "wbiosrvc": ServiceKnowledge("Biometria do Windows (Windows Hello: digital/rosto).",
                                 "Mantenha se usa Windows Hello."),
    "stisvc": ServiceKnowledge("Aquisição de imagens (scanners e câmeras).", "Mantenha se usa scanner."),
    "wersvc": ServiceKnowledge("Relatórios de erros do Windows.", "Mantenha (já é iniciado sob demanda)."),
    "edgeupdate": ServiceKnowledge("Atualizador do Microsoft Edge.",
                                   "Mantenha: garante atualizações de segurança do navegador."),
    "gupdate": ServiceKnowledge("Atualizador do Google Chrome.",
                                "Mantenha: garante atualizações de segurança do navegador."),
}


@dataclass
class ServiceInfo:
    name: str
    display_name: str
    status: str
    start_type: str           # automatic | delayed | manual | disabled
    binpath: str
    description: str
    third_party: bool
    protected: bool
    protected_reason: str
    explanation: str
    suggestion: str
    suggested_start: Optional[str]

    @property
    def start_label(self) -> str:
        return START_LABELS.get(self.start_type, self.start_type)

    @property
    def status_label(self) -> str:
        return {"running": "Em execução", "stopped": "Parado", "paused": "Pausado",
                "start_pending": "Iniciando", "stop_pending": "Parando"}.get(self.status, self.status)


def _is_delayed(name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"SYSTEM\\CurrentControlSet\\Services\\{name}") as key:
            return winreg.QueryValueEx(key, "DelayedAutostart")[0] == 1
    except OSError:
        return False


def list_services() -> list[ServiceInfo]:
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    services = []
    for svc in psutil.win_service_iter():
        try:
            info = svc.as_dict()
        except Exception:
            continue
        name = info.get("name", "")
        start = info.get("start_type") or "unknown"
        if start == "automatic" and _is_delayed(name):
            start = "delayed"
        binpath = (info.get("binpath") or "")
        lbin = binpath.lower().strip('"')
        third_party = bool(lbin) and windir not in lbin and "\\windows\\" not in lbin
        protected, reason = is_service_protected(name, info.get("display_name", ""))
        kb = KNOWLEDGE.get(name.lower())
        explanation = kb.explanation if kb else (info.get("description") or "").strip()
        suggestion, suggested = "", None
        if kb:
            suggestion = kb.suggestion
            if kb.suggested_start and start in ("automatic", "delayed") and not protected:
                suggested = kb.suggested_start
        elif third_party and start == "automatic" and not protected:
            suggestion = ("Serviço de terceiros com início automático. Se o programa é pouco usado, "
                          "'Automático (atraso)' ou 'Manual' podem reduzir o tempo de boot.")
        services.append(ServiceInfo(
            name=name, display_name=info.get("display_name", name), status=info.get("status", ""),
            start_type=start, binpath=binpath, description=(info.get("description") or "").strip(),
            third_party=third_party, protected=protected, protected_reason=reason,
            explanation=explanation, suggestion=suggestion, suggested_start=suggested,
        ))
    return sorted(services, key=lambda s: s.display_name.lower())


def suggested_changes(services: list[ServiceInfo]) -> list[ServiceInfo]:
    return [s for s in services if s.suggested_start and s.suggested_start != s.start_type]


def _apply_start_type(name: str, start: str) -> tuple[bool, str]:
    result = run_command(["sc.exe", "config", name, "start=", _SC_START[start]], timeout=30)
    if result.ok:
        return True, "ok"
    return False, result.summary()


def change_start_type(service: ServiceInfo, new_start: str) -> tuple[bool, str]:
    """Altera o tipo de inicialização (após confirmação). Requer Administrador."""
    if new_start not in _SC_START:
        return False, "tipo de inicialização inválido"
    if service.protected:
        return False, f"serviço protegido ({service.protected_reason})"
    if not is_admin():
        return False, "requer Administrador"
    if new_start == service.start_type:
        return True, "nenhuma alteração necessária"
    log.info(f"INÍCIO alteração de serviço {service.name}: {service.start_type} -> {new_start}")
    ok, msg = _apply_start_type(service.name, new_start)
    if not ok:
        log.error(f"ERRO ao alterar serviço {service.name}: {msg}")
        return False, msg
    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="service_start_type",
        description=f"Serviço '{service.display_name}' ({service.name}): "
                    f"{START_LABELS.get(service.start_type)} → {START_LABELS[new_start]}",
        previous_state=service.start_type, new_state=new_start,
        undoable=service.start_type in _SC_START, undo_type="service_start_type",
        undo_data={"name": service.name, "previous": service.start_type},
    ))
    previous = service.start_type
    service.start_type = new_start
    return True, f"{service.display_name}: {START_LABELS.get(previous)} → {START_LABELS[new_start]}"


def _undo_service(data: dict) -> tuple[bool, str]:
    if not is_admin():
        return False, "requer Administrador"
    ok, msg = _apply_start_type(data["name"], data["previous"])
    return ok, (f"serviço {data['name']} restaurado para {START_LABELS.get(data['previous'])}"
                if ok else msg)


restore_manager.register_undo_handler("service_start_type", _undo_service)

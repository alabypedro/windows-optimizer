"""Análise completa, pontuação de saúde, recomendações e plano de otimização.

Fluxo: run_full_analysis() -> compute_health() / build_recommendations()
       -> build_plan(modo) -> usuário escolhe/confirma -> execute_plan().

As recomendações são adaptadas ao hardware detectado (RAM, CPU, SSD/HDD,
espaço livre) — nada é sugerido só porque "parece bom".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from core import (browser_cleaner, network_tools, restore_manager, service_manager,
                  startup_manager, storage_analyzer, system_info, temp_cleaner, windows_tools)
from core.temp_cleaner import CleanResult, ScanResult
from utils.admin import is_admin
from utils.helpers import format_duration, format_size
from utils.logger import get_logger

log = get_logger("analysis")

ProgressFn = Optional[Callable[[str], None]]


# ---------------------------------------------------------------------------
# Análise
# ---------------------------------------------------------------------------

@dataclass
class Analysis:
    snapshot: system_info.SystemSnapshot
    temp_scans: list[ScanResult]
    browser_scans: list[tuple[browser_cleaner.Browser, ScanResult]]
    recycle_bin: tuple[int, int]
    startup_items: list[startup_manager.StartupItem]
    security: system_info.SecurityStatus
    updates: system_info.UpdateStatus
    service_suggestions: list[service_manager.ServiceInfo]
    trim: dict
    power_plan: Optional[windows_tools.PowerPlan]
    errors: list[str] = field(default_factory=list)

    @property
    def temp_bytes(self) -> int:
        return sum(s.total_bytes for s in self.temp_scans)

    @property
    def temp_files(self) -> int:
        return sum(s.file_count for s in self.temp_scans)

    @property
    def browser_bytes(self) -> int:
        return sum(s.total_bytes for _, s in self.browser_scans)

    @property
    def enabled_startup(self) -> list[startup_manager.StartupItem]:
        return [i for i in self.startup_items if i.enabled]


def _safe(step: str, fn, default, errors: list[str]):
    try:
        return fn()
    except Exception as exc:  # cada etapa falha isoladamente
        log.error(f"ERRO na etapa '{step}': {exc}")
        errors.append(f"{step}: {exc}")
        return default


def run_full_analysis(progress: ProgressFn = None, cancel: Optional[threading.Event] = None) -> Analysis:
    errors: list[str] = []

    def step(msg: str) -> None:
        log.info(f"Análise: {msg}")
        if progress:
            progress(msg)

    log.info("INÍCIO análise completa do PC")
    step("Coletando informações do sistema…")
    snapshot = system_info.collect_snapshot()
    step("Procurando arquivos temporários…")
    temp_scans = []
    for cat in _safe("categorias", temp_cleaner.build_categories, [], errors):
        if cancel is not None and cancel.is_set():
            break
        step(f"Analisando: {cat.name}")
        scan = _safe(cat.name, lambda c=cat: temp_cleaner.scan_category(c, cancel), None, errors)
        if scan is not None:
            temp_scans.append(scan)
    step("Analisando caches de navegadores…")
    browser_scans = []
    for b in _safe("navegadores", browser_cleaner.detect_browsers, [], errors):
        scan = _safe(b.name, lambda br=b: browser_cleaner.scan_browser(br), None, errors)
        if scan is not None:
            browser_scans.append((b, scan))
    step("Verificando a Lixeira…")
    recycle = _safe("lixeira", temp_cleaner.query_recycle_bin, (0, 0), errors)
    step("Lendo programas de inicialização…")
    startup = _safe("inicialização", startup_manager.list_startup_items, [], errors)
    step("Verificando segurança (Defender/Firewall)…")
    security = _safe("segurança", system_info.security_status, system_info.SecurityStatus(), errors)
    step("Verificando Windows Update…")
    updates = _safe("windows update", system_info.update_status, system_info.UpdateStatus(), errors)
    step("Analisando serviços…")
    services = _safe("serviços", service_manager.list_services, [], errors)
    step("Verificando TRIM e plano de energia…")
    trim = _safe("trim", storage_analyzer.trim_status, {}, errors)
    plan = _safe("plano de energia", windows_tools.active_power_plan, None, errors)
    analysis = Analysis(snapshot, temp_scans, browser_scans, recycle, startup, security, updates,
                        service_manager.suggested_changes(services), trim, plan, errors)
    log.info(f"FIM análise: {format_size(analysis.temp_bytes)} em temporários, "
             f"{len(analysis.enabled_startup)} itens de inicialização ativos, {len(errors)} erro(s)")
    return analysis


# ---------------------------------------------------------------------------
# Pontuação de saúde
# ---------------------------------------------------------------------------

@dataclass
class HealthFactor:
    name: str
    penalty: int
    detail: str
    level: str  # "ok" | "atenção" | "crítico" | "info"


def compute_health(a: Analysis) -> tuple[int, list[HealthFactor]]:
    s = a.snapshot
    hw = s.hardware
    factors: list[HealthFactor] = []

    def add(name, penalty, detail, level):
        factors.append(HealthFactor(name, penalty, detail, level))

    if s.ram_percent >= 90:
        add("Uso de memória", 20, f"RAM em {s.ram_percent:.0f}% — o sistema pode estar usando o arquivo de "
            "paginação, o que causa lentidão.", "crítico")
    elif s.ram_percent >= 75:
        add("Uso de memória", 8, f"RAM em {s.ram_percent:.0f}% — margem pequena para novos programas.", "atenção")
    else:
        add("Uso de memória", 0, f"RAM em {s.ram_percent:.0f}% — adequado.", "ok")

    free = hw.system_free_pct
    if free < 10:
        add("Espaço no disco do sistema", 20, f"Apenas {free:.0f}% livre em {hw.system_drive}. "
            "Pouco espaço prejudica atualizações, cache e memória virtual.", "crítico")
    elif free < 20:
        add("Espaço no disco do sistema", 10, f"{free:.0f}% livre em {hw.system_drive}.", "atenção")
    else:
        add("Espaço no disco do sistema", 0, f"{free:.0f}% livre em {hw.system_drive}.", "ok")

    tb = a.temp_bytes
    if tb > 5 * 1024 ** 3:
        add("Arquivos temporários", 10, f"{format_size(tb)} em temporários podem ser liberados.", "atenção")
    elif tb > 1024 ** 3:
        add("Arquivos temporários", 5, f"{format_size(tb)} em temporários podem ser liberados.", "atenção")
    else:
        add("Arquivos temporários", 0, f"{format_size(tb)} em temporários.", "ok")

    n = len(a.enabled_startup)
    limit_high, limit_mid = (10, 6) if (hw.low_ram or hw.weak_cpu) else (15, 9)
    if n > limit_high:
        add("Programas na inicialização", 10, f"{n} programas iniciam com o Windows.", "atenção")
    elif n > limit_mid:
        add("Programas na inicialização", 5, f"{n} programas iniciam com o Windows.", "atenção")
    else:
        add("Programas na inicialização", 0, f"{n} programas iniciam com o Windows.", "ok")

    sec = a.security
    if sec.defender_available and sec.realtime_enabled is False and not sec.other_antivirus:
        add("Proteção antivírus", 15, "A proteção em tempo real do Microsoft Defender está desligada e "
            "nenhum outro antivírus foi detectado.", "crítico")
    elif sec.other_antivirus or sec.realtime_enabled:
        name = sec.other_antivirus[0] if sec.other_antivirus else "Microsoft Defender"
        add("Proteção antivírus", 0, f"Proteção ativa ({name}).", "ok")
    else:
        add("Proteção antivírus", 0, "Não foi possível confirmar o status do antivírus.", "info")
    if sec.firewall_enabled is False:
        add("Firewall", 10, "Ao menos um perfil do Firewall do Windows está desativado.", "crítico")

    if a.updates.reboot_pending:
        add("Reinicialização pendente", 5, "Atualizações aguardam reinício para concluir a instalação.", "atenção")
    if s.uptime_seconds > 7 * 86400:
        add("Tempo ligado", 5, f"Ligado há {format_duration(s.uptime_seconds)} sem reiniciar.", "atenção")

    if hw.system_disk_type == "HDD":
        add("Tipo de disco", 0, "Sistema instalado em HDD — este é o maior limitador de velocidade; "
            "um SSD traria o maior ganho perceptível.", "info")
    if a.trim.get("NTFS") is False and hw.system_disk_type == "SSD":
        add("TRIM", 5, "TRIM está desativado para NTFS em um SSD.", "atenção")

    score = max(0, 100 - sum(f.penalty for f in factors))
    return score, factors


# ---------------------------------------------------------------------------
# Recomendações adaptadas ao hardware
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    title: str
    detail: str
    priority: str   # "alta" | "média" | "baixa"
    page: str = ""  # página da interface relacionada


def build_recommendations(a: Analysis) -> list[Recommendation]:
    s, hw = a.snapshot, a.snapshot.hardware
    recs: list[Recommendation] = []

    if hw.low_disk_space:
        recs.append(Recommendation(
            "Priorize liberar espaço", f"O disco do sistema tem {hw.system_free_pct:.0f}% livre. Limpe os "
            f"temporários ({format_size(a.temp_bytes)}) e use o scanner de arquivos grandes/duplicados.",
            "alta", "Armazenamento"))
    if a.temp_bytes > 500 * 1024 ** 2:
        recs.append(Recommendation("Limpar arquivos temporários",
                                   f"{format_size(a.temp_bytes)} podem ser liberados com segurança.",
                                   "média", "Limpeza"))
    if hw.low_ram:
        heavy = ", ".join(f"{p.name} ({format_size(p.memory_bytes)})" for p in s.top_ram[:3])
        recs.append(Recommendation(
            "Pouca RAM: priorize inicialização e processos",
            f"O PC tem {format_size(hw.ram_total)} de RAM. Reduza programas que iniciam com o Windows e "
            f"feche apps pesados em segundo plano. Maiores consumidores agora: {heavy}.",
            "alta" if hw.very_low_ram or s.ram_percent > 80 else "média", "Inicialização"))
    if hw.weak_cpu and len(a.enabled_startup) > 6:
        recs.append(Recommendation(
            "CPU modesta: evite processos em segundo plano",
            f"Processador com {hw.logical_cores} threads. Cada app de bandeja/atualizador disputa CPU no boot; "
            "revise a inicialização e os apps em segundo plano (Ferramentas).", "média", "Inicialização"))
    elif len(a.enabled_startup) > 10:
        recs.append(Recommendation("Revisar programas de inicialização",
                                   f"{len(a.enabled_startup)} itens ativos. Desative o que não precisa estar "
                                   "aberto desde o login.", "média", "Inicialização"))
    if hw.system_disk_type == "HDD":
        recs.append(Recommendation(
            "Disco rígido (HDD) detectado",
            "Mantenha o SysMain ativo (ajuda em HDD), execute a otimização da unidade periodicamente e "
            "considere migrar para SSD — é o maior ganho de desempenho possível.", "média", "Armazenamento"))
    elif hw.system_disk_type == "SSD":
        if a.trim.get("NTFS") is False:
            recs.append(Recommendation("Ativar TRIM",
                                       "O TRIM mantém o desempenho do SSD ao longo do tempo e está desativado.",
                                       "alta", "Armazenamento"))
        else:
            recs.append(Recommendation("SSD com TRIM ativo",
                                       "Nada a fazer: o Windows já otimiza o SSD semanalmente (ReTrim). "
                                       "Desfragmentação não é necessária nem recomendada.", "baixa",
                                       "Armazenamento"))
    if s.uptime_seconds > 7 * 86400:
        recs.append(Recommendation("Reiniciar o computador",
                                   f"Ligado há {format_duration(s.uptime_seconds)}. Reiniciar libera memória "
                                   "e conclui atualizações.", "média"))
    if a.updates.reboot_pending:
        recs.append(Recommendation("Concluir atualizações",
                                   "Há atualizações aguardando reinício.", "alta", "Windows"))
    sec = a.security
    if sec.defender_available and sec.realtime_enabled is False and not sec.other_antivirus:
        recs.append(Recommendation("Reative a proteção em tempo real",
                                   "Abra Segurança do Windows > Proteção contra vírus e ameaças. "
                                   "Este programa não altera configurações de segurança.", "alta", "Windows"))
    if a.recycle_bin[1] > 1024 ** 3:
        recs.append(Recommendation("Lixeira ocupando espaço",
                                   f"A Lixeira tem {format_size(a.recycle_bin[1])}. Revise e esvazie se não "
                                   "precisar dos arquivos.", "baixa", "Limpeza"))
    if a.service_suggestions:
        names = ", ".join(sv.display_name for sv in a.service_suggestions[:4])
        recs.append(Recommendation("Serviços opcionais em início automático",
                                   f"{len(a.service_suggestions)} serviço(s) podem ficar em 'Manual' sem perder "
                                   f"funcionalidade: {names}…", "baixa", "Serviços"))
    plan = a.power_plan
    if plan and plan.guid == "a1841308-3541-4fab-bc81-f71556f20b4a" and not hw.has_battery:
        recs.append(Recommendation("Plano 'Economia de energia' em um desktop",
                                   "Em PCs sem bateria, o plano 'Equilibrado' oferece melhor desempenho.",
                                   "média", "Windows"))
    if s.ram_percent > 85:
        top = s.top_ram[0] if s.top_ram else None
        if top:
            recs.append(Recommendation("Memória quase cheia",
                                       f"'{top.name}' usa {format_size(top.memory_bytes)}. Feche programas "
                                       "que não está usando.", "alta", "Processos"))
    order = {"alta": 0, "média": 1, "baixa": 2}
    return sorted(recs, key=lambda r: order.get(r.priority, 3))


# ---------------------------------------------------------------------------
# Plano de otimização (modo seguro / avançado)
# ---------------------------------------------------------------------------

@dataclass
class PlanItem:
    key: str
    title: str
    detail: str
    risk: str                     # "baixo" | "médio"
    default_selected: bool
    requires_admin: bool
    action: Callable[[ProgressFn], tuple[bool, str, int]]  # (ok, mensagem, bytes liberados)
    reversible: str = ""          # como desfazer (texto)
    order: int = 50


def _clean_action(scan: ScanResult):
    def run(progress: ProgressFn):
        res: CleanResult = temp_cleaner.clean_scan(scan, progress=progress)
        msg = (f"{res.removed_files} arquivo(s) removido(s), {format_size(res.freed_bytes)} liberados"
               + (f"; {res.in_use} em uso ignorado(s)" if res.in_use else "")
               + (f"; {res.denied} sem permissão" if res.denied else ""))
        return True, msg, res.freed_bytes
    return run


def build_plan(a: Analysis, advanced: bool) -> list[PlanItem]:
    admin = is_admin()
    items: list[PlanItem] = []

    for scan in a.temp_scans:
        cat = scan.category
        if scan.file_count == 0:
            continue
        if not advanced and not cat.safe_default:
            continue
        items.append(PlanItem(
            key=f"clean_{cat.key}",
            title=f"Limpar: {cat.name} ({format_size(scan.total_bytes)}, {scan.file_count} arquivos)",
            detail=cat.description + (f" Obs.: {cat.note}" if cat.note else ""),
            risk=cat.risk, default_selected=cat.safe_default and (admin or not cat.requires_admin),
            requires_admin=cat.requires_admin, action=_clean_action(scan),
            reversible="Não reversível (arquivos temporários são recriados pelos programas).", order=20,
        ))

    def flush(_p):
        r = network_tools.flush_dns()
        return r.ok, "Cache DNS limpo" if r.ok else r.summary(), 0

    items.append(PlanItem("flush_dns", "Limpar cache de DNS (ipconfig /flushdns)",
                          "Remove resoluções de nomes armazenadas; o Windows refaz o cache automaticamente. "
                          "Útil se sites não carregam após mudança de rede.", "baixo", False, False, flush,
                          "Não é necessário desfazer.", order=30))

    if not advanced:
        return sorted(items, key=lambda i: i.order)

    # ---- Somente no modo avançado --------------------------------------
    def restore_point(_p):
        ok, msg = restore_manager.create_restore_point("Windows Optimizer - antes da otimização")
        return ok, msg, 0

    items.append(PlanItem("restore_point", "Criar ponto de restauração do sistema",
                          "Recomendado antes de alterar serviços/inicialização. Executado primeiro.",
                          "baixo", admin, True, restore_point, order=0))

    for b, scan in a.browser_scans:
        if scan.file_count == 0:
            continue
        running = " — navegador ABERTO: feche-o para liberar mais" if b.running else ""
        items.append(PlanItem(f"browser_{b.key}", f"Limpar cache do {b.name} ({format_size(scan.total_bytes)})",
                              "Somente cache. Senhas, favoritos, histórico e cookies são preservados" + running + ".",
                              "baixo", False, False, _clean_action(scan), "Não reversível (cache é recriado).",
                              order=25))

    items_count, rb_size = a.recycle_bin
    if items_count:
        def empty(_p):
            ok, msg = temp_cleaner.empty_recycle_bin()
            return ok, msg, rb_size if ok else 0
        items.append(PlanItem("recycle_bin", f"Esvaziar a Lixeira ({items_count} itens, {format_size(rb_size)})",
                              "Os arquivos da Lixeira serão excluídos DEFINITIVAMENTE.", "médio", False, False,
                              empty, "NÃO reversível.", order=26))

    heavy_startup = sorted((i for i in a.enabled_startup if not i.protected),
                           key=lambda i: i.memory_bytes, reverse=True)[:8]
    for item in heavy_startup:
        def disable(_p, it=item):
            ok, msg = startup_manager.set_enabled(it, False)
            return ok, msg, 0
        items.append(PlanItem(
            f"startup_{item.key}", f"Desativar na inicialização: {item.name}",
            f"{item.publisher or 'Fabricante desconhecido'} — {item.location}. Impacto: {item.impact}. "
            "O programa continua instalado e pode ser aberto normalmente.",
            "baixo", False, item.needs_admin, disable, "Reversível pelo Histórico.", order=40))

    for svc in a.service_suggestions:
        def to_manual(_p, sv=svc):
            ok, msg = service_manager.change_start_type(sv, sv.suggested_start or "manual")
            return ok, msg, 0
        items.append(PlanItem(
            f"service_{svc.name}", f"Serviço '{svc.display_name}': {svc.start_label} → Manual",
            f"{svc.explanation} {svc.suggestion}", "médio", False, True, to_manual,
            "Reversível pelo Histórico.", order=45))

    if a.trim.get("NTFS") is False and a.snapshot.hardware.system_disk_type == "SSD":
        def trim(_p):
            ok, msg = storage_analyzer.enable_trim()
            return ok, msg, 0
        items.append(PlanItem("enable_trim", "Ativar TRIM para SSD (NTFS)",
                              "Configuração padrão do Windows para SSDs.", "baixo", admin, True, trim,
                              "Reversível pelo Histórico.", order=50))

    def component_cleanup(progress):
        tool = next(t for t in windows_tools.maintenance_tools() if t.key == "dism_cleanup")
        code = windows_tools.run_maintenance(tool, lambda line: progress(line) if progress else None)
        return code == 0, f"DISM finalizado (código {code})", 0

    items.append(PlanItem("dism_cleanup", "Limpeza de componentes do Windows (DISM /StartComponentCleanup)",
                          "Remove componentes substituídos por atualizações. Pode levar 5–30 min. "
                          "Não usa /ResetBase.", "baixo", False, True, component_cleanup,
                          "Não reversível (atualizações instaladas continuam desinstaláveis).", order=60))
    return sorted(items, key=lambda i: i.order)


@dataclass
class PlanResult:
    title: str
    ok: bool
    message: str
    freed: int


def execute_plan(items: list[PlanItem], progress: ProgressFn = None,
                 cancel: Optional[threading.Event] = None) -> list[PlanResult]:
    """Executa os itens CONFIRMADOS pelo usuário, um a um, isolando falhas."""
    results = []
    admin = is_admin()
    log.info(f"INÍCIO execução do plano: {len(items)} item(ns)")
    for item in sorted(items, key=lambda i: i.order):
        if cancel is not None and cancel.is_set():
            results.append(PlanResult(item.title, False, "cancelado pelo usuário", 0))
            continue
        if item.requires_admin and not admin:
            results.append(PlanResult(item.title, False, "ignorado: requer Administrador", 0))
            log.warning(f"IGNORADO (sem admin): {item.title}")
            continue
        if progress:
            progress(f"Executando: {item.title}")
        log.info(f"INÍCIO item: {item.title}")
        try:
            ok, msg, freed = item.action(progress)
        except Exception as exc:
            ok, msg, freed = False, f"erro inesperado: {exc}", 0
        (log.info if ok else log.error)(f"FIM item: {item.title}: {'OK' if ok else 'FALHA'} — {msg}")
        results.append(PlanResult(item.title, ok, msg, freed))
    total = sum(r.freed for r in results)
    log.info(f"FIM execução do plano: {sum(r.ok for r in results)}/{len(results)} OK, "
             f"{format_size(total)} liberados")
    return results

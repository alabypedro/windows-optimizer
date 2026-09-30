"""Limpeza de temporários pela linha de comando (sem interface gráfica).

Mesmo motor e mesmas proteções da página Limpeza: primeiro analisa tudo
(nada é apagado), mostra o resumo e só remove após confirmação [S/N]
ou com --yes (execução agendada/automática).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from core import restore_manager, temp_cleaner
from utils.admin import is_admin
from utils.helpers import format_duration, format_int, format_size, logs_dir
from utils.logger import current_log_file, get_logger

log = get_logger("cli_cleanup")


def _confirm(question: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{question} [S/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("s", "sim", "y", "yes")


def run_clean(dry_run: bool = False, yes: bool = False, include_optional: bool = False,
              recycle_bin: bool = True, quiet: bool = False) -> int:
    def say(*args) -> None:
        if not quiet:
            print(*args)

    start = time.time()
    admin = is_admin()
    say(f"Modo: {'análise (--dry-run)' if dry_run else 'limpeza'} • "
        f"Administrador: {'sim' if admin else 'não'} • Log: {current_log_file()}")
    if not admin:
        say("Sem Administrador: categorias que exigem privilégios são puladas.")
    say("")
    log.info(f"INÍCIO limpeza via linha de comando (dry_run={dry_run}, opcionais={include_optional}, admin={admin})")

    # ---- Fase 1: análise (nada é apagado) ---------------------------------
    scans: list[temp_cleaner.ScanResult] = []
    for cat in temp_cleaner.build_categories():
        if not include_optional and not cat.safe_default:
            continue
        if cat.requires_admin and not admin:
            continue
        say(f"Analisando: {cat.name}")
        try:
            scans.append(temp_cleaner.scan_category(cat))
        except Exception as exc:  # um local com problema nunca interrompe os demais
            log.error(f"ERRO ao analisar '{cat.name}': {exc}")
    scans = [s for s in scans if s.file_count > 0]

    rb_items, rb_size = temp_cleaner.query_recycle_bin() if recycle_bin else (0, 0)
    total = sum(s.total_bytes for s in scans)
    say("")
    for s in scans:
        say(f"  {s.category.name}: {format_size(s.total_bytes)} ({format_int(s.file_count)} arquivos)")
    if recycle_bin:
        say(f"  Lixeira: {format_size(rb_size)} ({format_int(rb_items)} itens)")
    say(f"\nEspaço recuperável: {format_size(total + rb_size)}\n")

    if dry_run:
        log.info("FIM análise via linha de comando (dry-run): nada foi removido")
        print("Nenhuma alteração foi feita (modo de teste --dry-run).")
        return 0
    if not scans and not rb_items:
        print("Nada a limpar.")
        return 0

    # ---- Fase 2: exclusão (só após confirmação) ---------------------------
    freed = 0
    removed = 0
    if scans:
        if not _confirm("Deseja iniciar a limpeza?", yes):
            log.info("Limpeza via linha de comando cancelada pelo usuário")
            print("Limpeza cancelada. Nenhum arquivo foi removido.")
            return 0
        for s in scans:
            say(f"Limpando: {s.category.name}")
            res = temp_cleaner.clean_scan(s)
            freed += res.freed_bytes
            removed += res.removed_files
            if res.in_use or res.denied or res.errors:
                say(f"   ignorados: {res.in_use} em uso, {res.denied} sem permissão, {res.errors} erro(s)")

    rb_msg = ""
    if rb_items and _confirm(f"Esvaziar DEFINITIVAMENTE a Lixeira ({format_size(rb_size)})?", yes):
        ok, rb_msg = temp_cleaner.empty_recycle_bin()
        if ok:
            freed += rb_size

    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="file_cleanup", description=f"Limpeza (linha de comando): {format_size(freed)} liberados",
        previous_state=[f"{s.category.name}: {format_size(s.total_bytes)}" for s in scans],
        new_state=f"{removed} arquivos removidos" + (f"; Lixeira: {rb_msg}" if rb_msg else ""),
        undoable=False, result="Arquivos temporários não podem ser restaurados."))
    log.info(f"FIM limpeza via linha de comando: {removed} arquivo(s), {format_size(freed)} liberados")
    print(f"\nLimpeza concluída: {format_int(removed)} arquivo(s) removido(s), {format_size(freed)} liberados "
          f"em {format_duration(time.time() - start)}.")
    return 0


def schedule_help() -> str:
    """Instruções para agendar a limpeza (o programa NÃO cria a tarefa sozinho)."""
    if getattr(sys, "frozen", False):
        program, args = sys.executable, "--clean --yes --quiet"
    else:
        program = sys.executable
        args = f'"{Path(sys.argv[0]).resolve()}" --clean --yes --quiet'
    tr_args = args.replace('"', '\\"')  # fora da f-string: Python < 3.12 não aceita "\" nela
    return f"""Como agendar a limpeza automática (Agendador de Tarefas do Windows)
====================================================================

O Windows Optimizer NÃO cria tarefas agendadas sozinho. Configure manualmente:

Método 1 - Interface gráfica
  1. Abra o "Agendador de Tarefas" no menu Iniciar e clique em "Criar Tarefa Básica...".
  2. Dê um nome (ex.: "Windows Optimizer - Limpeza") e escolha a frequência (ex.: Semanalmente).
  3. Ação "Iniciar um programa":
       Programa/script:      {program}
       Adicionar argumentos: {args}
  4. Para limpar também as categorias que exigem Administrador, abra as propriedades da tarefa
     e marque "Executar com privilégios mais altos".

Método 2 - Linha de comando (copie e rode num terminal como Administrador)
  schtasks /Create /SC WEEKLY /ST 09:00 /TN "WindowsOptimizerLimpeza" /TR "\\"{program}\\" {tr_args}"

Observações
  - --yes é obrigatório em execução agendada (não há ninguém para confirmar); ele também esvazia a
    Lixeira. Acrescente --no-recycle-bin para não mexer nela.
  - Somente categorias "Recomendadas" são limpas; acrescente --all para incluir as opcionais.
  - O resultado fica registrado em: {logs_dir()}"""

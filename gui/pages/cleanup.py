"""Limpeza de temporários/caches, Lixeira e cache de DNS."""

from __future__ import annotations

import customtkinter as ctk

from core import browser_cleaner, network_tools, restore_manager, temp_cleaner
from core.report import SESSION
from core.temp_cleaner import ScanResult
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, COLOR_OK, DANGER_BUTTON, SECONDARY_BUTTON, Column, DataTable, Row,
                         button_bar, confirm, font, show_error, show_info, show_text)
from utils.admin import is_admin
from utils.helpers import format_int, format_size


def scan_all(progress, cancel=None) -> list[ScanResult]:
    scans = []
    for cat in temp_cleaner.build_categories():
        progress(f"Analisando: {cat.name}")
        scans.append(temp_cleaner.scan_category(cat, cancel))
    for b in browser_cleaner.detect_browsers():
        progress(f"Analisando cache do {b.name}")
        scans.append(browser_cleaner.scan_browser(b))
    return scans


def run_cleanup(scans: list[ScanResult], progress, cancel) -> list[temp_cleaner.CleanResult]:
    results = []
    for scan in scans:
        if cancel is not None and cancel.is_set():
            break
        progress(f"Limpando: {scan.category.name}")
        results.append(temp_cleaner.clean_scan(scan, cancel, progress))
    return results


def cleanup_summary(results: list[temp_cleaner.CleanResult]) -> tuple[str, int]:
    freed = sum(r.freed_bytes for r in results)
    lines = [f"Espaço liberado: {format_size(freed)}", ""]
    for r in results:
        lines.append(f"{r.category_name}")
        lines.append(f"   removidos: {format_int(r.removed_files)} arquivo(s), {format_int(r.removed_dirs)} pasta(s) "
                     f"vazia(s) — {format_size(r.freed_bytes)}")
        if r.in_use or r.denied or r.skipped or r.errors:
            lines.append(f"   ignorados: {r.in_use} em uso, {r.denied} sem permissão, "
                         f"{r.skipped} já removidos/protegidos, {r.errors} erro(s)")
    lines += ["", "Arquivos em uso ou sem permissão foram ignorados e registrados no log."]
    return "\n".join(lines), freed


class CleanupPage(BasePage):
    title = "Limpeza"
    subtitle = ("Arquivos temporários e caches que podem ser removidos com segurança. Arquivos em uso são "
                "ignorados. Senhas, favoritos, histórico e cookies de navegadores nunca são apagados.")

    def build(self) -> None:
        self.scans: dict[str, ScanResult] = {}
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, corner_radius=10)
        top.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(0, weight=1)
        self.total_label = ctk.CTkLabel(top, text="Analisando…", font=font(22, "bold"), anchor="w")
        self.total_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        self.selected_label = ctk.CTkLabel(top, text="", font=font(12), text_color=COLOR_MUTED, anchor="w")
        self.selected_label.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))
        bar = button_bar(top)
        bar.grid(row=0, column=1, rowspan=2, padx=12)
        ctk.CTkButton(bar, text="[1] Limpar tudo que for seguro", command=self.clean_safe,
                      fg_color="#2e8b57", hover_color="#256f46", width=220).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="[2] Limpar categorias escolhidas", command=self.clean_selected,
                      width=220).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="[3] Cancelar", command=self.cancel, width=110,
                      **SECONDARY_BUTTON).pack(side="left", padx=4)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        cols = [Column("name", "Categoria", 330, stretch=True), Column("size", "Tamanho", 100, "e"),
                Column("files", "Arquivos", 80, "e"), Column("risk", "Risco", 70, "center"),
                Column("admin", "Observação", 170)]
        self.table = DataTable(body, cols, checkable=True, height=14, on_select=self._show_detail,
                               on_check=self._update_selected)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        side = ctk.CTkFrame(body, corner_radius=10)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(side, text="Detalhes", font=font(14, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
        self.detail = ctk.CTkTextbox(side, height=160, wrap="word", font=font(12))
        self.detail.grid(row=1, column=0, sticky="ew", padx=14)
        self.detail.insert("1.0", "Selecione uma categoria para ver o que será removido.")
        self.detail.configure(state="disabled")
        ctk.CTkButton(side, text="Analisar novamente", command=self.rescan, **SECONDARY_BUTTON).grid(
            row=2, column=0, sticky="w", padx=14, pady=8)

        # Lixeira
        ctk.CTkLabel(side, text="Lixeira", font=font(14, "bold"), anchor="w").grid(
            row=3, column=0, sticky="ew", padx=14, pady=(10, 0))
        self.recycle_label = ctk.CTkLabel(side, text="—", anchor="w", font=font(12))
        self.recycle_label.grid(row=4, column=0, sticky="ew", padx=14)
        ctk.CTkButton(side, text="Esvaziar Lixeira…", command=self.empty_recycle, **DANGER_BUTTON).grid(
            row=5, column=0, sticky="w", padx=14, pady=6)

        # DNS
        ctk.CTkLabel(side, text="Cache de DNS", font=font(14, "bold"), anchor="w").grid(
            row=6, column=0, sticky="ew", padx=14, pady=(10, 0))
        ctk.CTkLabel(side, text="Limpa nomes de sites memorizados (ipconfig /flushdns). Seguro e temporário.",
                     anchor="w", justify="left", wraplength=360, font=font(12), text_color=COLOR_MUTED).grid(
            row=7, column=0, sticky="ew", padx=14)
        ctk.CTkButton(side, text="Limpar cache DNS", command=self.flush_dns).grid(
            row=8, column=0, sticky="w", padx=14, pady=(6, 14))

    def load(self) -> None:
        if self.app.analysis is not None:
            self.on_analysis()
        elif not self.app.busy:
            self.rescan()
        self._refresh_recycle()

    def on_analysis(self) -> None:
        a = self.app.analysis
        if a is None:
            return
        scans = list(a.temp_scans) + [s for _, s in a.browser_scans]
        self._set_scans(scans)
        self._set_recycle(a.recycle_bin)

    # ------------------------------------------------------------------ dados
    def _set_scans(self, scans: list[ScanResult]) -> None:
        self.scans = {s.category.key: s for s in scans}
        admin = is_admin()
        rows = []
        for s in scans:
            cat = s.category
            note = "Requer Administrador" if cat.requires_admin and not admin else \
                ("Recomendado" if cat.safe_default else "Opcional")
            tags = ("muted",) if s.file_count == 0 else (("warn",) if cat.requires_admin and not admin else ())
            rows.append(Row(cat.key, [cat.name, format_size(s.total_bytes), format_int(s.file_count), cat.risk, note],
                            sort=[cat.name, s.total_bytes, s.file_count, cat.risk, note], tags=tags,
                            checkable=s.file_count > 0))
        self.table.set_rows(rows)
        total = sum(s.total_bytes for s in scans)
        safe = sum(s.total_bytes for s in scans if self._is_safe(s))
        self.total_label.configure(text=f"{format_size(total)} podem ser liberados.")
        self.selected_label.configure(text=f"Limpeza segura recomendada: {format_size(safe)} • "
                                           "marque categorias na tabela para escolher.")

    def _is_safe(self, s: ScanResult) -> bool:
        return (s.category.safe_default and s.file_count > 0
                and (is_admin() or not s.category.requires_admin))

    def _update_selected(self) -> None:
        checked = self.table.checked_rows()
        size = sum(self.scans[r.id].total_bytes for r in checked if r.id in self.scans)
        self.selected_label.configure(text=f"{len(checked)} categoria(s) selecionada(s): {format_size(size)}")

    def _show_detail(self, ids: list[str]) -> None:
        if not ids or ids[0] not in self.scans:
            return
        s = self.scans[ids[0]]
        cat = s.category
        lines = [cat.description, ""]
        if cat.note:
            lines += [f"Obs.: {cat.note}", ""]
        lines.append("Pastas analisadas:")
        lines += [f"  {loc.path}" for loc in cat.locations]
        if s.inaccessible:
            lines.append(f"\n{s.inaccessible} item(ns) sem acesso de leitura.")
        if s.unavailable_reason:
            lines.append(f"Atenção: {s.unavailable_reason}.")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")

    def rescan(self) -> None:
        self.app.run_exclusive("Analisando arquivos temporários…", scan_all, self._set_scans, cancellable=True)
        self._refresh_recycle()

    def _refresh_recycle(self) -> None:
        self.app.runner.submit(lambda _p: temp_cleaner.query_recycle_bin(), on_done=self._set_recycle)

    def _set_recycle(self, info) -> None:
        items, size = info
        self.recycle_bin = info
        self.recycle_label.configure(text=f"{format_int(items)} item(ns) — aproximadamente {format_size(size)}")

    # --------------------------------------------------------------- ações
    def clean_safe(self) -> None:
        scans = [s for s in self.scans.values() if self._is_safe(s)]
        self._confirm_and_clean(scans, "Limpeza segura")

    def clean_selected(self) -> None:
        scans = [self.scans[r.id] for r in self.table.checked_rows() if r.id in self.scans]
        if not scans:
            show_info("Nenhuma categoria", "Marque ao menos uma categoria na tabela (clique na caixa ☐).")
            return
        self._confirm_and_clean(scans, "Limpeza das categorias escolhidas")

    def _confirm_and_clean(self, scans: list[ScanResult], label: str) -> None:
        scans = [s for s in scans if s.file_count > 0]
        if not scans:
            show_info("Nada a limpar", "Não há arquivos a remover nas categorias escolhidas.")
            return
        total = sum(s.total_bytes for s in scans)
        details = [f"{s.category.name}: {format_size(s.total_bytes)} ({format_int(s.file_count)} arquivos)"
                   for s in scans]
        needs_admin = [s for s in scans if s.category.requires_admin and not is_admin()]
        warning = ("Os arquivos removidos NÃO vão para a Lixeira (são temporários e recriados pelos programas). "
                   "Arquivos em uso serão ignorados.")
        if needs_admin:
            warning += " Algumas categorias requerem Administrador: arquivos sem permissão serão ignorados."
        if not confirm(label, f"{format_size(total)} podem ser liberados. Confirma a limpeza abaixo?",
                       details, confirm_text="Limpar agora", warning=warning):
            return

        def done(results):
            text, freed = cleanup_summary(results)
            SESSION.add(f"{label}: {format_size(freed)} liberados", freed=freed)
            restore_manager.record_action(restore_manager.HistoryEntry(
                action_type="file_cleanup", description=f"{label}: {format_size(freed)} liberados",
                previous_state=[f"{s.category.name}: {format_size(s.total_bytes)}" for s in scans],
                new_state=f"{sum(r.removed_files for r in results)} arquivos removidos",
                undoable=False, result="Arquivos temporários não podem ser restaurados."))
            self.app.set_status(f"Limpeza concluída: {format_size(freed)} liberados.")
            show_text("Resultado da limpeza", text)
            self.rescan()

        self.app.run_exclusive("Limpando…", lambda p, c: run_cleanup(scans, p, c), done, cancellable=True)

    def cancel(self) -> None:
        if self.app.busy:
            self.app.cancel_task()
        else:
            self.table.check_all(False)
            self.app.set_status("Seleção limpa. Nenhuma alteração foi feita.")

    def empty_recycle(self) -> None:
        items, size = temp_cleaner.query_recycle_bin()
        if items == 0:
            show_info("Lixeira vazia", "A Lixeira já está vazia.")
            return
        if not confirm("Esvaziar Lixeira", f"Excluir DEFINITIVAMENTE {format_int(items)} item(ns) "
                                           f"({format_size(size)}) da Lixeira?",
                       ["Os arquivos não poderão ser recuperados depois.",
                        "Dica: abra a Lixeira antes e restaure o que quiser manter."],
                       confirm_text="Esvaziar definitivamente", danger=True):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Lixeira: {msg}", ok=ok, freed=size if ok else 0)
            restore_manager.record_action(restore_manager.HistoryEntry(
                action_type="recycle_bin", description=f"Lixeira esvaziada ({format_size(size)})" if ok
                else "Falha ao esvaziar Lixeira", previous_state=f"{items} itens", new_state="vazia" if ok else "",
                undoable=False, result=msg))
            (show_info if ok else show_error)("Lixeira", msg)
            self._refresh_recycle()

        self.app.run_exclusive("Esvaziando a Lixeira…", lambda _p: temp_cleaner.empty_recycle_bin(), done)

    def flush_dns(self) -> None:
        if not confirm("Limpar cache DNS", "Executar 'ipconfig /flushdns'?",
                       ["Remove resoluções de nomes armazenadas.", "Não altera nenhuma configuração de rede."]):
            return

        def done(result):
            SESSION.add("Cache DNS limpo" if result.ok else f"Flush DNS falhou: {result.summary()}", ok=result.ok)
            (show_info if result.ok else show_error)("DNS", result.stdout.strip() or result.summary())

        self.app.run_exclusive("Limpando cache DNS…", lambda _p: network_tools.flush_dns(), done)

"""Limpeza seletiva de cache de navegadores."""

from __future__ import annotations

import customtkinter as ctk

from core import browser_cleaner, restore_manager
from core.report import SESSION
from gui.pages.base import BasePage
from gui.pages.cleanup import cleanup_summary, run_cleanup
from gui.widgets import (COLOR_MUTED, COLOR_WARN, SECONDARY_BUTTON, Column, DataTable, Row, button_bar,
                         confirm, font, show_info, show_text)
from utils.helpers import format_int, format_size


class BrowsersPage(BasePage):
    title = "Navegadores"
    subtitle = ("Somente o CACHE é limpo (imagens, scripts e shaders baixados). Senhas, favoritos, histórico, "
                "cookies, extensões e sessões abertas NUNCA são apagados.")

    def build(self) -> None:
        self.data: dict[str, tuple] = {}
        self.grid_rowconfigure(2, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkButton(bar, text="Detectar e analisar", command=self.refresh, **SECONDARY_BUTTON).pack(side="left")
        ctk.CTkButton(bar, text="Limpar cache dos marcados…", command=self.clean).pack(side="left", padx=8)
        self.info_label = ctk.CTkLabel(bar, text="", font=font(12), text_color=COLOR_MUTED)
        self.info_label.pack(side="left", padx=8)
        cols = [Column("name", "Navegador", 200), Column("profiles", "Perfis", 70, "e"),
                Column("cache", "Cache", 110, "e"), Column("files", "Arquivos", 90, "e"),
                Column("running", "Em execução", 110, "center"), Column("paths", "Pastas de cache", 520, stretch=True)]
        self.table = DataTable(self, cols, checkable=True, height=10)
        self.table.grid(row=2, column=0, sticky="nsew")
        ctk.CTkLabel(self, text="Dica: feche o navegador antes de limpar — arquivos em uso são ignorados. "
                                "Após a limpeza, os sites podem carregar um pouco mais devagar na primeira visita.",
                     font=font(12), text_color=COLOR_MUTED, anchor="w", wraplength=1000, justify="left").grid(
            row=3, column=0, sticky="ew", pady=(6, 0))

    def load(self) -> None:
        a = self.app.analysis
        if a is not None:
            self._populate([(b, s, b.running) for b, s in a.browser_scans])
        else:
            self.refresh()

    def on_analysis(self) -> None:
        if self.app.analysis is not None:
            self._populate([(b, s, b.running) for b, s in self.app.analysis.browser_scans])

    def refresh(self) -> None:
        def work(progress):
            out = []
            for b in browser_cleaner.detect_browsers():
                progress(f"Analisando {b.name}…")
                out.append((b, browser_cleaner.scan_browser(b), b.running))
            return out
        self.app.run_exclusive("Detectando navegadores…", work, self._populate)

    def _populate(self, items) -> None:
        self.data = {b.key: (b, s) for b, s, _ in items}
        if not items:
            self.table.set_rows([])
            self.info_label.configure(text="Nenhum navegador suportado foi encontrado (Chrome, Edge, Firefox, "
                                           "Brave, Opera, Vivaldi).")
            return
        rows = []
        for b, s, running in items:
            paths = "; ".join(str(loc.path) for loc in b.cache_locations[:3])
            if len(b.cache_locations) > 3:
                paths += f" (+{len(b.cache_locations) - 3})"
            rows.append(Row(b.key, [b.name, len(b.profiles), format_size(s.total_bytes), format_int(s.file_count),
                                    "⚠ Sim" if running else "Não", paths],
                            sort=[b.name, len(b.profiles), s.total_bytes, s.file_count, running, paths],
                            tags=("warn",) if running else (), checkable=s.file_count > 0))
        self.table.set_rows(rows)
        total = sum(s.total_bytes for _, s, _ in items)
        self.info_label.configure(text=f"{len(items)} navegador(es) — {format_size(total)} em cache.")

    def clean(self) -> None:
        checked = [self.data[r.id] for r in self.table.checked_rows() if r.id in self.data]
        if not checked:
            show_info("Nenhum navegador marcado", "Marque ao menos um navegador na tabela (caixa ☐).")
            return
        details = [f"{b.name}: {format_size(s.total_bytes)} de cache ({format_int(s.file_count)} arquivos)"
                   for b, s in checked]
        details += ["Preservados: senhas, favoritos, histórico, cookies, extensões, sessões e preferências."]
        running = [b.name for b, _ in checked if b.running]
        warning = ""
        if running:
            warning = f"Em execução: {', '.join(running)}. Feche para liberar mais espaço (arquivos em uso são ignorados)."
        if not confirm("Limpar cache de navegadores", "Limpar o cache dos navegadores marcados?", details,
                       confirm_text="Limpar cache", warning=warning):
            return
        scans = [s for _, s in checked]

        def done(results):
            text, freed = cleanup_summary(results)
            SESSION.add(f"Cache de navegadores: {format_size(freed)} liberados", freed=freed)
            restore_manager.record_action(restore_manager.HistoryEntry(
                action_type="file_cleanup", description=f"Cache de navegadores limpo: {format_size(freed)}",
                previous_state=details[:-1], undoable=False))
            show_text("Resultado", text)
            self.refresh()

        self.app.run_exclusive("Limpando cache de navegadores…", lambda p, c: run_cleanup(scans, p, c), done,
                               cancellable=True)

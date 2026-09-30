"""Gerenciador de processos simples."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from core import process_manager
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, DANGER_BUTTON, SECONDARY_BUTTON, Column, DataTable, Row, button_bar,
                         confirm, font, show_error, show_info)
from utils.helpers import format_size, open_in_explorer


class ProcessesPage(BasePage):
    title = "Processos"
    subtitle = ("Lista atualizada somente quando você pede (o programa não monitora em segundo plano). "
                "Processos críticos do Windows e de segurança são protegidos (🔒) e não podem ser finalizados.")

    def build(self) -> None:
        self.rows_data: dict[str, process_manager.ProcessRow] = {}
        self.grid_rowconfigure(2, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkButton(bar, text="Atualizar lista", command=self.refresh, width=120).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Ordenar por CPU", command=lambda: self._sort_desc(2),
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Ordenar por RAM", command=lambda: self._sort_desc(3),
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Finalizar processo…", command=self.kill, **DANGER_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Abrir local", command=self.open_location,
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        self.filter_var = ctk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(bar, textvariable=self.filter_var, placeholder_text="Filtrar por nome…", width=200).pack(
            side="right")
        self.count_label = ctk.CTkLabel(bar, text="", text_color=COLOR_MUTED, font=font(12))
        self.count_label.pack(side="right", padx=10)

        cols = [Column("name", "Nome", 230), Column("pid", "PID", 80, "e"), Column("cpu", "CPU", 80, "e"),
                Column("ram", "RAM", 110, "e"), Column("path", "Caminho do executável", 520, stretch=True)]
        self.table = DataTable(self, cols, height=18, on_double=lambda _id: self.open_location())
        self.table.grid(row=2, column=0, sticky="nsew")

    def _sort_desc(self, col: int) -> None:
        self.table._sort_state = (col, False)  # próximo sort_by na mesma coluna => decrescente
        self.table.sort_by(col)

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.app.set_status("Medindo processos…")
        self.app.runner.submit(lambda _p: process_manager.list_processes(), on_done=self._populate)

    def _populate(self, rows) -> None:
        self.rows_data = {str(r.pid): r for r in rows}
        self._apply_filter()
        if self.table._sort_state[0] < 0:
            self._sort_desc(3)
        self.app.set_status(f"{len(rows)} processos.")

    def _apply_filter(self) -> None:
        text = self.filter_var.get().strip().lower()
        rows = []
        for r in self.rows_data.values():
            if text and text not in r.name.lower() and text not in r.exe.lower():
                continue
            name = ("🔒 " if r.protected else "") + r.name
            rows.append(Row(str(r.pid), [name, r.pid, f"{r.cpu_percent:.1f}%", format_size(r.memory_bytes),
                                         r.exe or "(sem acesso)"],
                            sort=[r.name.lower(), r.pid, r.cpu_percent, r.memory_bytes, r.exe],
                            tags=("muted",) if r.protected else ()))
        self.table.set_rows(rows)
        self.count_label.configure(text=f"{len(rows)} processo(s)")

    def _selected(self):
        ids = self.table.selected_ids()
        return self.rows_data.get(ids[0]) if ids else None

    def kill(self) -> None:
        r = self._selected()
        if r is None:
            show_info("Selecione um processo", "Selecione um processo na lista.")
            return
        if r.protected:
            show_info("Processo protegido", f"'{r.name}' não pode ser finalizado: {r.protected_reason}.")
            return
        warning = "Dados não salvos neste programa serão perdidos. Esta ação não pode ser desfeita."
        if r.sensitive:
            warning += (f" Atenção: '{r.name}' é um componente do Windows/sistema; finalizá-lo pode fechar a "
                        "barra de tarefas ou a sincronização até ser reaberto.")
        if not confirm("Finalizar processo", f"Finalizar '{r.name}' (PID {r.pid})?",
                       [f"Caminho: {r.exe or 'desconhecido'}", f"RAM: {format_size(r.memory_bytes)}",
                        f"CPU: {r.cpu_percent:.1f}%"], confirm_text="Finalizar", danger=True, warning=warning):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Processo: {msg}", ok=ok)
            if not ok:
                show_error("Não foi possível finalizar", msg)
            self.refresh()

        self.app.run_exclusive(f"Finalizando {r.name}…",
                               lambda _p: process_manager.terminate_process(r.pid, r.name), done)

    def open_location(self) -> None:
        r = self._selected()
        if r and r.exe:
            open_in_explorer(Path(r.exe))

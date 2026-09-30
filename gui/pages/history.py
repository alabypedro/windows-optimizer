"""Histórico de ações, desfazer e acesso a logs/backups."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

from core import restore_manager
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, SECONDARY_BUTTON, Column, DataTable, Row, button_bar, confirm, font,
                         show_error, show_info)
from utils.helpers import backups_dir, logs_dir, open_in_explorer
from utils.logger import current_log_file


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return iso


def _short(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        return f"{len(value)} item(ns)" if len(value) > 2 else "; ".join(map(str, value))
    return str(value)


class HistoryPage(BasePage):
    title = "Histórico"
    subtitle = ("Todas as alterações feitas pelo programa, com data/hora e estado anterior. Itens de "
                "inicialização, serviços, plano de energia e TRIM podem ser desfeitos. Arquivos enviados para a "
                "Lixeira podem ser restaurados pela própria Lixeira do Windows.")

    def build(self) -> None:
        self.entries: dict[str, restore_manager.HistoryEntry] = {}
        self.grid_rowconfigure(2, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkButton(bar, text="Desfazer selecionado…", command=self.undo).pack(side="left")
        ctk.CTkButton(bar, text="Atualizar", command=self.refresh, **SECONDARY_BUTTON).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Abrir log de hoje", command=lambda: open_in_explorer(current_log_file()),
                      **SECONDARY_BUTTON).pack(side="left", padx=(24, 8))
        ctk.CTkButton(bar, text="Pasta de logs", command=lambda: open_in_explorer(logs_dir()),
                      **SECONDARY_BUTTON).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Pasta de backups", command=lambda: open_in_explorer(backups_dir()),
                      **SECONDARY_BUTTON).pack(side="left", padx=8)
        cols = [Column("when", "Data/hora", 150), Column("desc", "Alteração realizada", 460, stretch=True),
                Column("prev", "Estado anterior", 160), Column("new", "Novo estado", 160),
                Column("undo", "Desfazer", 130, "center")]
        self.table = DataTable(self, cols, height=16, on_select=self._detail)
        self.table.grid(row=2, column=0, sticky="nsew")
        self.detail = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000, font=font(12),
                                   text_color=COLOR_MUTED)
        self.detail.grid(row=3, column=0, sticky="ew", pady=(6, 0))

    def on_show(self) -> None:
        self.refresh()  # sempre recarrega (outras páginas podem ter registrado ações)

    def refresh(self) -> None:
        entries = list(reversed(restore_manager.load_history()))
        self.entries = {e.id: e for e in entries}
        rows = []
        for e in entries:
            if e.undone:
                status = "Desfeito"
            elif e.undoable:
                status = "Disponível"
            else:
                status = "Não aplicável"
            rows.append(Row(e.id, [_fmt_time(e.timestamp), e.description, _short(e.previous_state),
                                   _short(e.new_state), status],
                            sort=[e.timestamp, e.description, "", "", status],
                            tags=("muted",) if e.undone or not e.undoable else ()))
        self.table.set_rows(rows)

    def _detail(self, ids) -> None:
        e = self.entries.get(ids[0]) if ids else None
        if not e:
            return
        text = f"Tipo: {e.action_type}"
        if e.result:
            text += f" | Resultado: {e.result}"
        if e.undone:
            text += f" | Desfeito em {_fmt_time(e.undone_at)}"
        if isinstance(e.previous_state, list) and len(e.previous_state) > 2:
            text += "\nEstado anterior: " + "; ".join(map(str, e.previous_state[:8]))
        self.detail.configure(text=text)

    def undo(self) -> None:
        ids = self.table.selected_ids()
        e = self.entries.get(ids[0]) if ids else None
        if e is None:
            show_info("Selecione uma ação", "Selecione uma ação na lista.")
            return
        if e.undone:
            show_info("Já desfeito", "Esta ação já foi desfeita.")
            return
        if not e.undoable:
            show_info("Não pode ser desfeito", "Esta ação não pode ser desfeita pelo programa.\n\n"
                      + (e.result or "Arquivos temporários removidos não podem ser restaurados."))
            return
        if not confirm("Desfazer alteração", f"Desfazer: {e.description}?",
                       [f"Estado atual: {_short(e.new_state)}", f"Será restaurado para: {_short(e.previous_state)}"],
                       confirm_text="Desfazer"):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Desfeito: {e.description} ({msg})", ok=ok)
            (show_info if ok else show_error)("Desfazer", msg)
            self.refresh()
            for name in ("Inicialização", "Serviços", "Windows", "Armazenamento"):
                page = self.app.pages.get(name)
                if page is not None and hasattr(page, "_loaded"):
                    page._loaded = False  # recarrega ao abrir

        self.app.run_exclusive("Desfazendo alteração…", lambda _p: restore_manager.undo_action(e.id), done)

"""Gerenciador de inicialização."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from core import startup_manager
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, SECONDARY_BUTTON, Column, DataTable, Row, button_bar, confirm, font,
                         show_error, show_info)
from utils.admin import is_admin
from utils.helpers import format_size, open_in_explorer


class StartupPage(BasePage):
    title = "Inicialização"
    subtitle = ("Programas que abrem junto com o Windows. Desativar NÃO desinstala nem apaga nada — usa o "
                "mesmo mecanismo do Gerenciador de Tarefas e pode ser desfeito no Histórico. "
                "Itens de segurança, do Windows e de drivers/hardware são protegidos (🔒).")

    def build(self) -> None:
        self.items: dict[str, startup_manager.StartupItem] = {}
        self.grid_rowconfigure(2, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkButton(bar, text="Atualizar", command=self.refresh, width=110, **SECONDARY_BUTTON).pack(
            side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Desativar selecionado", command=lambda: self.toggle(False)).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Ativar selecionado", command=lambda: self.toggle(True),
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Abrir local do arquivo", command=self.open_location,
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        self.count_label = ctk.CTkLabel(bar, text="", text_color=COLOR_MUTED, font=font(12))
        self.count_label.pack(side="right")

        cols = [Column("name", "Nome", 200), Column("state", "Estado", 90, "center"),
                Column("impact", "Impacto (aprox.)", 190), Column("publisher", "Fabricante", 170),
                Column("location", "Local de inicialização", 230), Column("path", "Caminho", 380, stretch=True)]
        self.table = DataTable(self, cols, height=16, on_select=self._show_detail)
        self.table.grid(row=2, column=0, sticky="nsew")
        self.detail = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=1000, font=font(12),
                                   text_color=COLOR_MUTED)
        self.detail.grid(row=3, column=0, sticky="ew", pady=(6, 0))

    def load(self) -> None:
        a = self.app.analysis
        if a is not None and a.startup_items:
            self._populate(a.startup_items)
        else:
            self.refresh()

    def on_analysis(self) -> None:
        if self.app.analysis is not None:
            self._populate(self.app.analysis.startup_items)

    def refresh(self) -> None:
        self.app.set_status("Lendo programas de inicialização…")
        self.app.runner.submit(lambda _p: startup_manager.list_startup_items(), on_done=self._populate)

    def _populate(self, items) -> None:
        self.items = {it.key: it for it in items}
        rows = []
        for it in items:
            state = "Ativado" if it.enabled else "Desativado"
            tags = ("muted",) if it.protected else (() if it.enabled else ("muted",))
            name = ("🔒 " if it.protected else "") + it.name
            rows.append(Row(it.key, [name, state, it.impact, it.publisher or "—", it.location, it.exe_path],
                            sort=[it.name.lower(), state, it.memory_bytes, it.publisher, it.location, it.exe_path],
                            tags=tags))
        self.table.set_rows(rows)
        enabled = sum(1 for i in items if i.enabled)
        self.count_label.configure(text=f"{enabled} ativo(s) de {len(items)}")
        self.app.set_status("Lista de inicialização atualizada.")

    def _selected(self):
        ids = self.table.selected_ids()
        return self.items.get(ids[0]) if ids else None

    def _show_detail(self, _ids) -> None:
        it = self._selected()
        if not it:
            return
        text = f"Comando: {it.command}"
        if it.running:
            text += f"   |   Em execução agora, usando {format_size(it.memory_bytes)} de RAM"
        if it.protected:
            text += f"\n🔒 Protegido: {it.protected_reason}. Este programa não permite desativá-lo."
        if it.needs_admin and not is_admin():
            text += "\nEste item vale para todos os usuários: alterar requer Administrador."
        self.detail.configure(text=text)

    def toggle(self, enable: bool) -> None:
        it = self._selected()
        if it is None:
            show_info("Selecione um item", "Selecione um programa na lista.")
            return
        if it.enabled == enable:
            show_info("Sem alteração", f"'{it.name}' já está {'ativado' if enable else 'desativado'}.")
            return
        if not enable and it.protected:
            show_info("Item protegido", f"'{it.name}' é protegido ({it.protected_reason}) e não pode ser "
                                        "desativado por este programa.")
            return
        if it.needs_admin and not is_admin():
            show_info("Requer Administrador", "Este item é de todos os usuários. Use 'Reiniciar como "
                                              "Administrador' no topo da janela.")
            return
        action = "Ativar" if enable else "Desativar"
        details = [f"Programa: {it.name}", f"Fabricante: {it.publisher or 'desconhecido'}",
                   f"Local: {it.location}", f"Caminho: {it.exe_path}",
                   "O programa NÃO será desinstalado nem apagado; apenas deixará de abrir com o Windows."
                   if not enable else "O programa voltará a abrir junto com o Windows.",
                   "Um backup da chave StartupApproved do Registro será salvo em backups/.",
                   "Reversível em Histórico › Desfazer."]
        if not confirm(f"{action} na inicialização", f"{action} '{it.name}' na inicialização?", details,
                       confirm_text=action):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Inicialização: {msg}", ok=ok)
            if not ok:
                show_error("Não foi possível alterar", msg)
            self.refresh()

        self.app.run_exclusive(f"{action}: {it.name}…", lambda _p: startup_manager.set_enabled(it, enable), done)

    def open_location(self) -> None:
        it = self._selected()
        if it and it.exe_path:
            path = Path(it.exe_path)
            if path.exists():
                open_in_explorer(path)
            else:
                show_info("Arquivo não encontrado", f"O caminho não existe:\n{path}")

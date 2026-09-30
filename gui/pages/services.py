"""Análise de serviços do Windows (nada é alterado automaticamente)."""

from __future__ import annotations

import customtkinter as ctk

from core import service_manager
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, COLOR_WARN, DANGER_BUTTON, SECONDARY_BUTTON, Column, DataTable, Row,
                         button_bar, confirm, font, show_error, show_info)
from utils.admin import is_admin


class ServicesPage(BasePage):
    title = "Serviços"
    subtitle = ("Análise e sugestões conservadoras. Nenhum serviço é alterado automaticamente. Serviços de "
                "segurança (Defender, Firewall), Windows Update e do núcleo do sistema são protegidos (🔒). "
                "Alterações exigem Administrador e podem ser desfeitas no Histórico.")

    FILTERS = ["Sugestões", "Início automático", "Terceiros", "Todos"]

    def build(self) -> None:
        self.services: dict[str, service_manager.ServiceInfo] = {}
        self.grid_rowconfigure(2, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.filter = ctk.CTkSegmentedButton(bar, values=self.FILTERS, command=lambda _v: self._apply_filter())
        self.filter.set("Sugestões")
        self.filter.pack(side="left")
        ctk.CTkButton(bar, text="Atualizar", command=self.refresh, width=100, **SECONDARY_BUTTON).pack(
            side="left", padx=10)
        self.search = ctk.StringVar()
        self.search.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(bar, textvariable=self.search, placeholder_text="Buscar serviço…", width=200).pack(side="right")
        self.count_label = ctk.CTkLabel(bar, text="", text_color=COLOR_MUTED, font=font(12))
        self.count_label.pack(side="right", padx=10)

        cols = [Column("display", "Serviço", 300), Column("name", "Nome", 150), Column("status", "Estado", 100),
                Column("start", "Inicialização", 150), Column("suggestion", "Sugestão", 420, stretch=True)]
        self.table = DataTable(self, cols, height=14, on_select=self._show_detail)
        self.table.grid(row=2, column=0, sticky="nsew")

        panel = ctk.CTkFrame(self, corner_radius=10)
        panel.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        panel.grid_columnconfigure(0, weight=1)
        self.detail = ctk.CTkLabel(panel, text="Selecione um serviço para ver sua função e a recomendação.",
                                   anchor="w", justify="left", wraplength=1000, font=font(12))
        self.detail.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        actions = button_bar(panel)
        actions.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
        ctk.CTkLabel(actions, text="Alterar inicialização para:", font=font(12)).pack(side="left", padx=4)
        for label, key, style in [("Manual", "manual", {}), ("Automático (atraso)", "delayed", SECONDARY_BUTTON),
                                  ("Automático", "automatic", SECONDARY_BUTTON),
                                  ("Desativado", "disabled", DANGER_BUTTON)]:
            ctk.CTkButton(actions, text=label, width=140, command=lambda k=key: self.change(k), **style).pack(
                side="left", padx=4)
        if not is_admin():
            ctk.CTkLabel(actions, text="  (requer Administrador)", text_color=COLOR_WARN, font=font(12)).pack(
                side="left")

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.app.set_status("Lendo serviços…")
        self.app.runner.submit(lambda _p: service_manager.list_services(), on_done=self._populate)

    def _populate(self, services) -> None:
        self.services = {s.name: s for s in services}
        self._apply_filter()
        self.app.set_status(f"{len(services)} serviços analisados.")

    def _apply_filter(self) -> None:
        mode = self.filter.get()
        text = self.search.get().strip().lower()
        rows = []
        for s in self.services.values():
            if mode == "Sugestões" and not (s.suggested_start and s.suggested_start != s.start_type):
                continue
            if mode == "Início automático" and s.start_type not in ("automatic", "delayed"):
                continue
            if mode == "Terceiros" and not s.third_party:
                continue
            if text and text not in s.display_name.lower() and text not in s.name.lower():
                continue
            suggestion = s.suggestion or ("Manter como está." if s.protected else "")
            name = ("🔒 " if s.protected else "") + s.display_name
            tags = ("muted",) if s.protected else (("warn",) if s.suggested_start else ())
            rows.append(Row(s.name, [name, s.name, s.status_label, s.start_label, suggestion],
                            sort=[s.display_name.lower(), s.name.lower(), s.status, s.start_type, suggestion],
                            tags=tags))
        self.table.set_rows(rows)
        self.count_label.configure(text=f"{len(rows)} serviço(s)")
        if mode == "Sugestões" and not rows and self.services:
            self.detail.configure(text="Nenhuma sugestão de alteração: os serviços opcionais conhecidos já estão "
                                       "em Manual/Desativado ou são necessários.")

    def _selected(self):
        ids = self.table.selected_ids()
        return self.services.get(ids[0]) if ids else None

    def _show_detail(self, _ids) -> None:
        s = self._selected()
        if not s:
            return
        lines = [f"{s.display_name} ({s.name}) — {s.status_label}, {s.start_label}",
                 f"Função: {s.explanation or 'sem descrição disponível.'}"]
        if s.suggestion:
            lines.append(f"Recomendação: {s.suggestion}")
        if s.protected:
            lines.append(f"🔒 Protegido: {s.protected_reason}. Não pode ser alterado por este programa.")
        if s.binpath:
            lines.append(f"Executável: {s.binpath}")
        self.detail.configure(text="\n".join(lines))

    def change(self, new_start: str) -> None:
        s = self._selected()
        if s is None:
            show_info("Selecione um serviço", "Selecione um serviço na lista.")
            return
        if s.protected:
            show_info("Serviço protegido", f"'{s.display_name}' é protegido ({s.protected_reason}).")
            return
        if not is_admin():
            show_info("Requer Administrador", "Alterar serviços requer Administrador. Use 'Reiniciar como "
                                              "Administrador' no topo da janela.")
            return
        if new_start == s.start_type:
            show_info("Sem alteração", "O serviço já usa esse tipo de inicialização.")
            return
        label = service_manager.START_LABELS[new_start]
        details = [f"Serviço: {s.display_name} ({s.name})", f"Função: {s.explanation or '—'}",
                   f"Inicialização atual: {s.start_label}", f"Nova inicialização: {label}",
                   "O serviço não é parado agora; a mudança vale a partir do próximo início.",
                   "Reversível em Histórico › Desfazer."]
        warning = ""
        if new_start == "disabled":
            warning = ("Desativar impede que o serviço seja iniciado mesmo quando algum recurso precisar dele. "
                       "Prefira 'Manual', que mantém o serviço disponível sob demanda.")
        if not confirm("Alterar serviço", f"Alterar '{s.display_name}' para '{label}'?", details,
                       confirm_text=f"Alterar para {label}", danger=new_start == "disabled", warning=warning):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Serviço: {msg}", ok=ok)
            if not ok:
                show_error("Não foi possível alterar", msg)
            self.refresh()

        self.app.run_exclusive(f"Alterando {s.name}…", lambda _p: service_manager.change_start_type(s, new_start),
                               done)

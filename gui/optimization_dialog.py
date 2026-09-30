"""Diálogo de otimização (Modo Seguro / Modo Avançado).

Fluxo: plano baseado na análise -> usuário marca os itens -> revisão com a
lista EXATA do que será feito -> confirmação -> execução -> resultado.
Não existe "modo milagroso": nada é aplicado sem estar marcado e confirmado.
"""

from __future__ import annotations

import customtkinter as ctk

from core.recommendations import PlanItem, build_plan, execute_plan
from core.report import SESSION
from gui.widgets import (COLOR_MUTED, COLOR_OK, COLOR_WARN, SECONDARY_BUTTON, OutputConsole, _Modal, confirm,
                         font)
from utils.admin import is_admin
from utils.helpers import format_size

RISK_COLORS = {"baixo": COLOR_OK, "médio": COLOR_WARN}


class OptimizationDialog(_Modal):
    def __init__(self, app):
        super().__init__("Otimização", 920, 680)
        self.app = app
        self.items: list[PlanItem] = []
        self.vars: dict[str, ctk.BooleanVar] = {}
        self.running = False
        self.executed = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="Otimização", font=font(20, "bold")).grid(row=0, column=0, sticky="w")
        self.mode = ctk.CTkSegmentedButton(head, values=["Modo Seguro", "Modo Avançado"],
                                           command=lambda _v: self._render())
        self.mode.set("Modo Seguro")
        self.mode.grid(row=0, column=2, sticky="e")
        self.mode_hint = ctk.CTkLabel(self, text="", font=font(12), text_color=COLOR_MUTED, anchor="w",
                                      justify="left", wraplength=880)
        self.mode_hint.grid(row=1, column=0, sticky="ew", padx=18)

        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=18, pady=8)
        self.list_frame.grid_columnconfigure(1, weight=1)
        self.console = OutputConsole(self, height=120)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=18, pady=(4, 14))
        self.summary = ctk.CTkLabel(bar, text="", font=font(12, "bold"), anchor="w")
        self.summary.pack(side="left")
        self.close_btn = ctk.CTkButton(bar, text="Fechar", width=110, command=self._close, **SECONDARY_BUTTON)
        self.close_btn.pack(side="right", padx=(8, 0))
        self.run_btn = ctk.CTkButton(bar, text="Revisar e executar…", width=180, command=self._review,
                                     fg_color="#2e8b57", hover_color="#256f46")
        self.run_btn.pack(side="right")
        self._render()

    def _close(self) -> None:
        if self.running:
            return  # não fecha no meio da execução
        self._cancel()

    def _cancel(self) -> None:
        if not self.running:
            super()._cancel()

    # ----------------------------------------------------------------- plano
    def _render(self) -> None:
        advanced = self.mode.get() == "Modo Avançado"
        self.mode_hint.configure(text=(
            "MODO AVANÇADO: mostra otimizações adicionais (inicialização, serviços, caches extras, manutenção). "
            "Todas começam DESMARCADAS, exceto as de baixo risco — revise cada uma."
            if advanced else
            "MODO SEGURO: apenas otimizações de baixo risco (limpeza de temporários e caches do sistema). "
            "Nenhuma configuração do Windows é alterada."))
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.items = build_plan(self.app.analysis, advanced)
        self.vars = {}
        admin = is_admin()
        for i, item in enumerate(self.items):
            var = ctk.BooleanVar(value=item.default_selected)
            self.vars[item.key] = var
            blocked = item.requires_admin and not admin
            cb = ctk.CTkCheckBox(self.list_frame, text="", variable=var, width=24, command=self._update_summary,
                                 state="disabled" if blocked else "normal")
            if blocked:
                var.set(False)
            cb.grid(row=i * 2, column=0, rowspan=2, sticky="n", padx=(4, 8), pady=(8, 0))
            title = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            title.grid(row=i * 2, column=1, sticky="ew", pady=(8, 0))
            ctk.CTkLabel(title, text=item.title, font=font(13, "bold"), anchor="w").pack(side="left")
            ctk.CTkLabel(title, text=f"  risco {item.risk}", font=font(11),
                         text_color=RISK_COLORS.get(item.risk, COLOR_WARN)).pack(side="left")
            if item.requires_admin:
                ctk.CTkLabel(title, text="  • requer Administrador" if blocked else "  • Administrador",
                             font=font(11), text_color=COLOR_WARN if blocked else COLOR_MUTED).pack(side="left")
            detail = item.detail + (f"  ({item.reversible})" if item.reversible else "")
            ctk.CTkLabel(self.list_frame, text=detail, font=font(12), text_color=COLOR_MUTED, anchor="w",
                         justify="left", wraplength=780).grid(row=i * 2 + 1, column=1, sticky="ew")
        if not self.items:
            ctk.CTkLabel(self.list_frame, text="Nada a otimizar neste modo — o sistema já está em ordem.").grid(
                row=0, column=1, sticky="w", pady=10)
        self._update_summary()

    def _selected(self) -> list[PlanItem]:
        return [i for i in self.items if self.vars.get(i.key) and self.vars[i.key].get()]

    def _update_summary(self) -> None:
        sel = self._selected()
        self.summary.configure(text=f"{len(sel)} de {len(self.items)} item(ns) selecionado(s)")

    # --------------------------------------------------------------- execução
    def _review(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if self.app.busy:
            return
        details = [f"{i.title} — {i.reversible or 'sem alteração de configuração'}" for i in sel]
        needs_rp = any(i.key.startswith(("startup_", "service_", "enable_trim")) for i in sel)
        warning = ""
        if needs_rp and not any(i.key == "restore_point" for i in sel):
            warning = ("Recomendado: marque também 'Criar ponto de restauração' antes de alterar "
                       "inicialização/serviços.")
        confirmed = confirm("Confirmar otimização", "Serão executadas EXATAMENTE as ações abaixo:", details,
                            confirm_text="Executar agora", warning=warning)
        self._grab()  # devolve o foco modal a este diálogo
        if not confirmed:
            return
        self.running = True
        self.run_btn.configure(state="disabled")
        self.close_btn.configure(state="disabled")
        self.mode.configure(state="disabled")
        self.console.grid(row=3, column=0, sticky="ew", padx=18)
        self.console.clear()

        def on_progress(msg: str) -> None:
            self.console.append(msg)

        self.app.run_exclusive("Executando otimização…", lambda p, c: execute_plan(sel, p, c), self._finished,
                               on_error=self._failed, cancellable=True, on_progress=on_progress)

    def _finished(self, results) -> None:
        self.running = False
        self.executed = True
        self.close_btn.configure(state="normal")
        freed = sum(r.freed for r in results)
        for r in results:
            SESSION.add(f"{r.title}: {r.message}", ok=r.ok, freed=r.freed)
            self.console.append(f"{'✔' if r.ok else '✖'} {r.title}: {r.message}")
        ok = sum(1 for r in results if r.ok)
        self.console.append(f"\nConcluído: {ok}/{len(results)} otimização(ões) realizada(s); "
                            f"{format_size(freed)} liberados. Detalhes no log e em Histórico.")
        self.summary.configure(text=f"Concluído — {format_size(freed)} liberados")
        self.app.set_status(f"Otimização concluída: {format_size(freed)} liberados.")

    def _failed(self, exc) -> None:
        self.running = False
        self.close_btn.configure(state="normal")
        self.console.append(f"Erro: {exc}")

"""Windows Update, segurança, plano de energia, ponto de restauração e manutenção."""

from __future__ import annotations

import customtkinter as ctk

from core import restore_manager, system_info, windows_tools
from core.report import SESSION
from core.system_info import list_drives
from gui.pages.base import BasePage
from gui.widgets import (COLOR_DANGER, COLOR_MUTED, COLOR_OK, COLOR_TEXT, COLOR_WARN, SECONDARY_BUTTON, Column, DataTable,
                         OutputConsole, Row, button_bar, confirm, font, show_error, show_info)
from utils.admin import is_admin
from utils.helpers import open_uri


class WindowsPage(BasePage):
    title = "Windows"
    subtitle = ("Somente leitura para Windows Update e segurança: este programa nunca desativa o Windows Update, "
                "o Defender ou o Firewall. Ferramentas de manutenção são as nativas do Windows.")

    def build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        tabs = ctk.CTkTabview(self)
        tabs.grid(row=1, column=0, sticky="nsew")
        self._build_status(tabs.add("Atualizações e segurança"))
        self._build_power(tabs.add("Plano de energia"))
        self._build_maintenance(tabs.add("Manutenção"))

    # ---------------------------------------------------------- atualização
    def _build_status(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        self.update_label = ctk.CTkLabel(tab, text="Windows Update: verificando…", anchor="w", justify="left",
                                         font=font(13))
        self.update_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        bar = button_bar(tab)
        bar.grid(row=1, column=0, sticky="w", padx=10)
        ctk.CTkButton(bar, text="Procurar atualizações pendentes", command=self.search_updates).pack(side="left")
        ctk.CTkButton(bar, text="Abrir Windows Update", command=lambda: open_uri("ms-settings:windowsupdate"),
                      **SECONDARY_BUTTON).pack(side="left", padx=8)
        self.pending_box = OutputConsole(tab, height=140)
        self.pending_box.grid(row=2, column=0, sticky="ew", padx=10, pady=8)

        self.security_label = ctk.CTkLabel(tab, text="Segurança: verificando…", anchor="w", justify="left",
                                           font=font(13))
        self.security_label.grid(row=3, column=0, sticky="ew", padx=10, pady=(12, 4))
        bar2 = button_bar(tab)
        bar2.grid(row=4, column=0, sticky="w", padx=10)
        ctk.CTkButton(bar2, text="Abrir Segurança do Windows", command=lambda: open_uri("windowsdefender:"),
                      **SECONDARY_BUTTON).pack(side="left")
        ctk.CTkButton(bar2, text="Atualizar status", command=self.refresh_status, **SECONDARY_BUTTON).pack(
            side="left", padx=8)

        ctk.CTkLabel(tab, text="Ponto de restauração", font=font(14, "bold"), anchor="w").grid(
            row=5, column=0, sticky="ew", padx=10, pady=(18, 2))
        ctk.CTkLabel(tab, text="Cria um ponto de restauração do sistema (requer Administrador e Proteção do "
                               "Sistema ativa). O Windows permite 1 ponto a cada 24h por padrão.",
                     anchor="w", justify="left", wraplength=900, text_color=COLOR_MUTED, font=font(12)).grid(
            row=6, column=0, sticky="ew", padx=10)
        bar3 = button_bar(tab)
        bar3.grid(row=7, column=0, sticky="w", padx=10, pady=6)
        ctk.CTkButton(bar3, text="Criar ponto de restauração…", command=self.create_restore_point).pack(side="left")
        ctk.CTkButton(bar3, text="Abrir Restauração do Sistema", command=self._open_rstrui,
                      **SECONDARY_BUTTON).pack(side="left", padx=8)

    def load(self) -> None:
        self.refresh_status()
        self.refresh_power()

    def refresh_status(self) -> None:
        def work(_p):
            return system_info.update_status(), system_info.security_status()
        self.app.runner.submit(work, on_done=self._show_status)

    def _show_status(self, data) -> None:
        upd, sec = data
        text = (f"Windows Update — serviço: {upd.service_state} | inicialização: {upd.service_start}\n"
                + ("⚠ Há atualizações aguardando reinício do computador." if upd.reboot_pending
                   else "Nenhum reinício pendente detectado."))
        self.update_label.configure(text=text, text_color=COLOR_WARN if upd.reboot_pending else COLOR_TEXT)
        lines = []
        if sec.defender_available:
            rt = "ativa" if sec.realtime_enabled else "DESATIVADA"
            lines.append(f"Microsoft Defender: proteção em tempo real {rt}"
                         + (f", definições com {sec.signature_age_days} dia(s)" if sec.signature_age_days is not None
                            else ""))
        if sec.other_antivirus:
            lines.append("Outros antivírus: " + ", ".join(sec.other_antivirus))
        if sec.firewall_enabled is not None:
            lines.append("Firewall do Windows: " + ("ativo em todos os perfis" if sec.firewall_enabled
                                                     else "DESATIVADO em ao menos um perfil"))
        if sec.detail:
            lines.append(sec.detail)
        problem = (sec.defender_available and sec.realtime_enabled is False and not sec.other_antivirus) \
            or sec.firewall_enabled is False
        if problem:
            lines.append("Recomendação: reative a proteção em 'Segurança do Windows'. Este programa não altera "
                         "configurações de segurança.")
        self.security_label.configure(text="\n".join(lines) or "Status de segurança indisponível.",
                                      text_color=COLOR_DANGER if problem else COLOR_OK)

    def search_updates(self) -> None:
        self.pending_box.clear()
        self.pending_box.append("Consultando o Windows Update (somente leitura; pode levar alguns minutos)…")

        def done(res):
            titles, msg = res
            self.pending_box.append(msg)
            for t in titles or []:
                self.pending_box.append(f"  • {t}")
            if titles:
                self.pending_box.append("\nInstale pelo Windows Update (botão 'Abrir Windows Update'). "
                                        "Este programa apenas informa.")
            self.app.set_status(msg)

        self.app.runner.submit(lambda _p: system_info.search_pending_updates(), on_done=done)

    def create_restore_point(self) -> None:
        if not is_admin():
            show_info("Requer Administrador", "Use 'Reiniciar como Administrador' no topo da janela.")
            return
        if not confirm("Ponto de restauração", "Criar um ponto de restauração do sistema agora?",
                       ["Usa o recurso nativo 'Checkpoint-Computer'.", "Pode levar alguns minutos."]):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Ponto de restauração: {msg}", ok=ok)
            restore_manager.record_action(restore_manager.HistoryEntry(
                action_type="restore_point", description="Criação de ponto de restauração", new_state=msg,
                undoable=False, result=msg))
            (show_info if ok else show_error)("Ponto de restauração", msg)

        self.app.run_exclusive("Criando ponto de restauração…",
                               lambda _p: restore_manager.create_restore_point("Windows Optimizer - manual"), done)

    @staticmethod
    def _open_rstrui() -> None:
        import subprocess
        try:
            subprocess.Popen(["rstrui.exe"])
        except OSError:
            pass

    # --------------------------------------------------------------- energia
    def _build_power(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        self.power_label = ctk.CTkLabel(tab, text="Plano ativo: —", anchor="w", font=font(13, "bold"))
        self.power_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        cols = [Column("name", "Plano", 260), Column("active", "Ativo", 70, "center"),
                Column("desc", "Descrição", 520, stretch=True), Column("guid", "GUID", 290)]
        self.power_table = DataTable(tab, cols, height=8)
        self.power_table.grid(row=1, column=0, sticky="nsew", padx=10)
        bar = button_bar(tab)
        bar.grid(row=2, column=0, sticky="w", padx=10, pady=8)
        ctk.CTkButton(bar, text="Ativar plano selecionado…", command=self.activate_plan).pack(side="left")
        ctk.CTkButton(bar, text="Atualizar", command=self.refresh_power, **SECONDARY_BUTTON).pack(side="left", padx=8)
        ctk.CTkLabel(bar, text="Nada é alterado automaticamente. Em notebooks, planos de alto desempenho "
                               "reduzem a duração da bateria.", text_color=COLOR_MUTED, font=font(12)).pack(
            side="left", padx=8)

    def refresh_power(self) -> None:
        def done(plans):
            self.plans = {p.guid: p for p in plans}
            active = next((p for p in plans if p.active), None)
            self.power_label.configure(text=f"Plano ativo: {active.name if active else 'desconhecido'}")
            self.power_table.set_rows([Row(p.guid, [p.name, "✔" if p.active else "", p.description, p.guid])
                                       for p in plans])
        self.app.runner.submit(lambda _p: windows_tools.list_power_plans(), on_done=done)

    def activate_plan(self) -> None:
        ids = self.power_table.selected_ids()
        plan = getattr(self, "plans", {}).get(ids[0]) if ids else None
        if plan is None:
            show_info("Selecione um plano", "Selecione um plano de energia na lista.")
            return
        if plan.active:
            show_info("Já ativo", f"'{plan.name}' já é o plano ativo.")
            return
        if not confirm("Plano de energia", f"Ativar o plano '{plan.name}'?",
                       [plan.description, f"Comando: powercfg /setactive {plan.guid}",
                        "Reversível em Histórico › Desfazer."]):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"Energia: {msg}", ok=ok)
            if not ok:
                show_error("Plano de energia", msg)
            self.refresh_power()

        self.app.run_exclusive("Alterando plano de energia…", lambda _p: windows_tools.set_power_plan(plan), done)

    # ------------------------------------------------------------ manutenção
    def _build_maintenance(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        top = button_bar(tab)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(top, text="Unidade para o CHKDSK:", font=font(12)).pack(side="left")
        letters = [d["letter"] for d in list_drives() if not d["removable"]] or ["C"]
        self.drive_menu = ctk.CTkOptionMenu(top, values=[f"{x}:" for x in letters], width=80,
                                            command=lambda _v: self._fill_tools())
        self.drive_menu.pack(side="left", padx=8)
        if not is_admin():
            ctk.CTkLabel(top, text="Todas as ferramentas de manutenção requerem Administrador.",
                         text_color=COLOR_WARN, font=font(12)).pack(side="left", padx=10)
        cols = [Column("name", "Ferramenta", 330), Column("time", "Duração típica", 120),
                Column("desc", "O que faz", 600, stretch=True)]
        self.tools_table = DataTable(tab, cols, height=7)
        self.tools_table.grid(row=1, column=0, sticky="ew", padx=10)
        bar = button_bar(tab)
        bar.grid(row=2, column=0, sticky="w", padx=10, pady=8)
        ctk.CTkButton(bar, text="Executar ferramenta selecionada…", command=self.run_tool).pack(side="left")
        ctk.CTkLabel(bar, text="A saída aparece abaixo em tempo real. Não desligue o PC durante SFC/DISM.",
                     text_color=COLOR_MUTED, font=font(12)).pack(side="left", padx=10)
        self.maint_console = OutputConsole(tab, height=200)
        self.maint_console.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self._fill_tools()

    def _fill_tools(self) -> None:
        self.tools = {t.key: t for t in windows_tools.maintenance_tools(self.drive_menu.get())}
        self.tools_table.set_rows([Row(t.key, [t.name, t.duration, t.description]) for t in self.tools.values()])

    def run_tool(self) -> None:
        ids = self.tools_table.selected_ids()
        tool = self.tools.get(ids[0]) if ids else None
        if tool is None:
            show_info("Selecione uma ferramenta", "Selecione uma ferramenta na lista.")
            return
        if tool.requires_admin and not is_admin():
            show_info("Requer Administrador", "Use 'Reiniciar como Administrador' no topo da janela.")
            return
        details = [tool.description, f"Comando: {' '.join(tool.args)}", f"Duração típica: {tool.duration}"]
        if not confirm(tool.name, f"Executar '{tool.name}'?", details, confirm_text="Executar",
                       warning=tool.confirm_text):
            return
        self.maint_console.clear()
        self.maint_console.append(f"> {' '.join(tool.args)}")

        def done(code):
            msg = f"{tool.name}: finalizado com código {code}"
            self.maint_console.append(f"\n{msg}")
            SESSION.add(msg, ok=code == 0)
            self.app.set_status(msg)

        def on_line(line: str) -> None:
            self.maint_console.append(line)
            self.app.set_status(line[:150])

        self.app.run_exclusive(f"Executando {tool.name}…",
                               lambda progress: windows_tools.run_maintenance(tool, progress), done,
                               on_progress=on_line)

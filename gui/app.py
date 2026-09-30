"""Janela principal: cabeçalho com ações rápidas, menu lateral, páginas e barra de status."""

from __future__ import annotations

import threading
from typing import Callable, Optional

import customtkinter as ctk

from core.recommendations import Analysis, run_full_analysis
from core.report import SESSION
from gui.widgets import (COLOR_OK, COLOR_WARN, SECONDARY_BUTTON, TaskRunner, confirm, font,
                         show_error, show_info, style_treeview)
from utils.admin import is_admin, relaunch_as_admin
from utils.helpers import APP_NAME, APP_VERSION, UPDATE_REPO, format_size
from utils.logger import get_logger, shutdown

log = get_logger("app")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}" + ("  —  Administrador" if is_admin() else ""))
        self.geometry("1280x800")
        self.minsize(1080, 680)
        style_treeview(self)

        self.runner = TaskRunner(self)
        self.analysis: Optional[Analysis] = None
        self._exclusive_running = False
        self._cancel_event: Optional[threading.Event] = None
        self._analysis_listeners: list[Callable[[], None]] = []

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self._build_statusbar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=16, pady=(8, 4))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        from gui.pages import (browsers, cleanup, dashboard, history, network, processes, services,
                               startup, storage, tools, windows_page)
        self.page_classes = {
            "Saúde do PC": dashboard.DashboardPage,
            "Limpeza": cleanup.CleanupPage,
            "Inicialização": startup.StartupPage,
            "Processos": processes.ProcessesPage,
            "Serviços": services.ServicesPage,
            "Armazenamento": storage.StoragePage,
            "Rede": network.NetworkPage,
            "Windows": windows_page.WindowsPage,
            "Navegadores": browsers.BrowsersPage,
            "Ferramentas": tools.ToolsPage,
            "Histórico": history.HistoryPage,
        }
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.current_page = ""
        self.show_page("Saúde do PC")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        log.info(f"Programa iniciado (admin={is_admin()})")
        # Fluxo principal: ao abrir, analisa o PC (somente leitura).
        self.after(300, self.analyze)
        # Checagem silenciosa de atualização: só avisa (nada é baixado sem confirmação).
        self.after(4000, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------------ layout
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=0, height=64)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="⚙  Windows Optimizer", font=font(20, "bold")).grid(
            row=0, column=0, padx=18, pady=12, sticky="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="w", padx=10)
        buttons = [
            ("ANALISAR PC", self.analyze, {}),
            ("OTIMIZAÇÃO SEGURA", self.open_optimization, {"fg_color": "#2e8b57", "hover_color": "#256f46"}),
            ("LIMPEZA", lambda: self.show_page("Limpeza"), SECONDARY_BUTTON),
            ("FERRAMENTAS", lambda: self.show_page("Ferramentas"), SECONDARY_BUTTON),
            ("HISTÓRICO", lambda: self.show_page("Histórico"), SECONDARY_BUTTON),
        ]
        self.analyze_button = None
        for text, cmd, style in buttons:
            b = ctk.CTkButton(actions, text=text, command=cmd, height=34, font=font(12, "bold"), **style)
            b.pack(side="left", padx=4)
            if text == "ANALISAR PC":
                self.analyze_button = b

        admin_frame = ctk.CTkFrame(header, fg_color="transparent")
        admin_frame.grid(row=0, column=2, padx=14, sticky="e")
        if is_admin():
            ctk.CTkLabel(admin_frame, text="🛡 Administrador", text_color=COLOR_OK, font=font(12, "bold")).pack()
        else:
            ctk.CTkLabel(admin_frame, text="Modo usuário (sem privilégios elevados)", text_color=COLOR_WARN,
                         font=font(11)).pack()
            ctk.CTkButton(admin_frame, text="Reiniciar como Administrador", height=26, font=font(11),
                          command=self.restart_as_admin, **SECONDARY_BUTTON).pack(pady=(2, 0))

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, corner_radius=0, width=200)
        side.grid(row=1, column=0, rowspan=2, sticky="nsw")
        side.grid_propagate(False)
        icons = ["❤", "🧹", "🚀", "📊", "🔧", "💾", "🌐", "🪟", "🌍", "🧰", "🕘"]
        names = ["Saúde do PC", "Limpeza", "Inicialização", "Processos", "Serviços", "Armazenamento",
                 "Rede", "Windows", "Navegadores", "Ferramentas", "Histórico"]
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        ctk.CTkLabel(side, text="MENU", font=font(11, "bold"), text_color=("gray45", "gray60")).pack(
            anchor="w", padx=18, pady=(14, 4))
        for icon, name in zip(icons, names):
            b = ctk.CTkButton(side, text=f"{icon}  {name}", anchor="w", height=36, corner_radius=6,
                              fg_color="transparent", text_color=("gray10", "gray90"),
                              hover_color=("gray75", "gray28"), font=font(13),
                              command=lambda n=name: self.show_page(n))
            b.pack(fill="x", padx=8, pady=1)
            self.nav_buttons[name] = b
        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=12, pady=12)
        # Destaque que só aparece quando a checagem ao abrir encontra versão nova.
        self.update_badge = ctk.CTkButton(bottom, text="", height=30, font=font(11, "bold"),
                                          fg_color="#2e8b57", hover_color="#256f46",
                                          command=lambda: self.check_updates(manual=True))
        self.check_update_button = ctk.CTkButton(bottom, text="⟳  Verificar atualizações", height=28, font=font(11),
                                                 command=lambda: self.check_updates(manual=True), **SECONDARY_BUTTON)
        self.check_update_button.pack(fill="x")
        ctk.CTkLabel(bottom, text=f"Versão {APP_VERSION}", font=font(10), anchor="w",
                     text_color=("gray45", "gray60")).pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(bottom, text="Aparência", font=font(11), anchor="w").pack(fill="x")
        mode = ctk.CTkOptionMenu(bottom, values=["Sistema", "Escuro", "Claro"], command=self._set_mode,
                                 height=28)
        mode.pack(fill="x", pady=(2, 0))

    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=0, height=34)
        bar.grid(row=2, column=1, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="Pronto.", anchor="w", font=font(12))
        self.status_label.grid(row=0, column=0, sticky="ew", padx=12)
        self.progress = ctk.CTkProgressBar(bar, width=180, mode="indeterminate")
        self.cancel_button = ctk.CTkButton(bar, text="Cancelar", width=90, height=24, command=self.cancel_task,
                                           **SECONDARY_BUTTON)

    def _set_mode(self, choice: str) -> None:
        ctk.set_appearance_mode({"Sistema": "system", "Escuro": "dark", "Claro": "light"}[choice])
        style_treeview(self)

    # --------------------------------------------------------------- navegação
    def show_page(self, name: str) -> None:
        if name not in self.pages:
            try:
                page = self.page_classes[name](self.content, self)
            except Exception as exc:
                log.exception(f"ERRO ao abrir página {name}")
                show_error("Erro", f"Não foi possível abrir a página '{name}':\n{exc}")
                return
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        for n, b in self.nav_buttons.items():
            b.configure(fg_color=("gray78", "gray25") if n == name else "transparent")
        self.pages[name].tkraise()
        self.current_page = name
        self.pages[name].on_show()

    # ------------------------------------------------------------ status/tarefas
    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def run_exclusive(self, label: str, fn, on_done=None, on_error=None, cancellable: bool = False,
                      on_progress=None) -> bool:
        """Executa uma operação que altera o sistema — apenas uma por vez.

        ``on_progress`` recebe as mensagens de progresso (padrão: barra de status).
        """
        if self._exclusive_running:
            show_info("Aguarde", "Outra operação ainda está em andamento. Aguarde a conclusão.")
            return False
        self._exclusive_running = True
        self._cancel_event = threading.Event() if cancellable else None
        self.set_status(label)
        self.progress.grid(row=0, column=1, padx=8)
        self.progress.start()
        if cancellable:
            self.cancel_button.grid(row=0, column=2, padx=(0, 10))

        def finish():
            self._exclusive_running = False
            self.progress.stop()
            self.progress.grid_remove()
            self.cancel_button.grid_remove()

        def done(result):
            finish()
            if on_done:
                on_done(result)

        def error(exc):
            finish()
            self.set_status("Erro na operação.")
            SESSION.add(f"{label}: {exc}", ok=False)
            (on_error or (lambda e: show_error("Erro", str(e))))(exc)

        cancel = self._cancel_event
        self.runner.submit(lambda progress: fn(progress, cancel) if cancellable else fn(progress),
                           on_done=done, on_error=error, on_progress=on_progress or self.set_status)
        return True

    @property
    def busy(self) -> bool:
        return self._exclusive_running

    def cancel_task(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.set_status("Cancelando…")

    # ---------------------------------------------------------------- análise
    def add_analysis_listener(self, callback: Callable[[], None]) -> None:
        self._analysis_listeners.append(callback)

    def analyze(self) -> None:
        def work(progress, cancel):
            return run_full_analysis(progress, cancel)

        def done(analysis: Analysis):
            self.analysis = analysis
            total = analysis.temp_bytes + analysis.browser_bytes
            self.set_status(f"Análise concluída — {format_size(total)} podem ser liberados. "
                            "Revise as recomendações antes de aplicar qualquer alteração.")
            for page in self.pages.values():
                try:
                    page.on_analysis()
                except Exception as exc:
                    log.exception(f"ERRO ao atualizar página após análise: {exc}")

        self.run_exclusive("Analisando o PC (somente leitura)…", work, done, cancellable=True)

    def open_optimization(self) -> None:
        if self.analysis is None:
            if self.busy:
                show_info("Aguarde", "A análise ainda está em andamento.")
            else:
                show_info("Análise necessária", "Execute 'ANALISAR PC' primeiro.")
            return
        from gui.optimization_dialog import OptimizationDialog
        dialog = OptimizationDialog(self)
        dialog.run()
        if dialog.executed:
            self.analyze()  # atualiza o diagnóstico após as alterações

    # ---------------------------------------------------------- atualizações
    def check_updates(self, manual: bool = True) -> None:
        from core import updater

        if not manual and not UPDATE_REPO:
            return  # origem ainda não configurada: a checagem silenciosa não incomoda
        if manual:
            self.set_status("Verificando atualizações…")

        def done(info: "updater.UpdateInfo") -> None:
            if not info.available:
                if manual:
                    self.set_status("O programa está atualizado.")
                    show_info("Atualizações", f"Você já tem a versão mais recente ({APP_VERSION}).")
                return
            self.update_badge.configure(text=f"⬆  Atualizar para {info.version}")
            if not self.update_badge.winfo_manager():  # ainda não está no layout
                self.update_badge.pack(fill="x", pady=(0, 6), before=self.check_update_button)
            if manual:
                self._offer_update(info)
            else:
                log.info(f"Atualização disponível: {info.version} (atual {APP_VERSION})")
                self.set_status(f"Nova versão {info.version} disponível — clique em "
                                f"'Atualizar para {info.version}' no menu lateral.")

        def error(exc: BaseException) -> None:
            log.warning(f"Verificação de atualização falhou: {exc}")
            if manual:
                self.set_status("Não foi possível verificar atualizações.")
                show_error("Atualizações", str(exc) if isinstance(exc, updater.UpdateError)
                           else f"Não foi possível verificar atualizações:\n{exc}")

        self.runner.submit(lambda _p: updater.check_for_update(), on_done=done, on_error=error)

    def _offer_update(self, info) -> None:
        from core import updater

        notes = [line.strip() for line in info.notes.splitlines() if line.strip()][:12]
        details = [f"Versão atual: {APP_VERSION}  →  nova: {info.version}"]
        if info.installer_size:
            details.append(f"Download: {format_size(info.installer_size)} (o código SHA-256 é conferido)")
        details += notes or ["(sem notas de versão)"]
        if not updater.can_self_update():
            if confirm("Atualização disponível", f"A versão {info.version} está disponível.", details +
                       ["Esta cópia não foi instalada pelo instalador (ex.: rodando pelo Python ou pela pasta "
                        "dist), então a atualização não pode ser automática. Baixe o instalador na página "
                        "da versão."], confirm_text="Abrir página de download"):
                updater.open_release_page(info)
            return
        if not confirm("Atualização disponível", f"Baixar e instalar a versão {info.version} agora?",
                       details + ["O programa vai fechar, instalar a nova versão e abrir de novo sozinho. "
                                  "Histórico, backups e logs são mantidos."],
                       confirm_text="Atualizar agora"):
            return

        def work(progress, cancel):
            return updater.download_installer(info, progress, cancel)

        def done(installer_path) -> None:
            try:
                updater.launch_installer(installer_path)
            except (OSError, updater.UpdateError) as exc:
                show_error("Atualização", f"Não foi possível abrir o instalador:\n{exc}")
                return
            log.info(f"Atualizando para {info.version}: programa será fechado")
            self._shutdown()

        def error(exc: BaseException) -> None:
            self.set_status("Atualização não concluída.")
            show_error("Atualização", str(exc))

        self.run_exclusive(f"Baixando a versão {info.version}…", work, done, on_error=error, cancellable=True)

    # ---------------------------------------------------------------- admin
    def restart_as_admin(self) -> None:
        if self.busy:
            show_info("Aguarde", "Há uma operação em andamento. Aguarde antes de reiniciar.")
            return
        if not confirm("Reiniciar como Administrador",
                       "O programa será reaberto com privilégios de Administrador (o Windows mostrará o "
                       "aviso do Controle de Conta de Usuário).",
                       ["Necessário para: limpar C:\\Windows\\Temp, alterar serviços, itens de inicialização "
                        "de todos os usuários, SFC/DISM/CHKDSK, pontos de restauração e otimização de unidades.",
                        "Nenhuma alteração é feita automaticamente ao reabrir."], confirm_text="Reiniciar"):
            return
        if relaunch_as_admin():
            log.info("Reiniciando como Administrador")
            self._shutdown()
        else:
            show_info("Não elevado", "A elevação foi cancelada ou não foi possível. O programa continua "
                                     "no modo usuário.")

    def _on_close(self) -> None:
        if self.busy and not confirm("Operação em andamento",
                                     "Há uma operação em andamento. Deseja realmente sair?",
                                     ["Limpezas em andamento são interrompidas com segurança "
                                      "(arquivos já processados continuam processados).",
                                      "Ferramentas do Windows (SFC/DISM/CHKDSK) continuam rodando "
                                      "até terminar, mesmo com o programa fechado."],
                                     confirm_text="Sair mesmo assim", danger=True):
            return
        self._shutdown()

    def _shutdown(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        log.info("Programa encerrado")
        shutdown()
        self.destroy()

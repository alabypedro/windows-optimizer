"""Ferramentas: arquivos grandes, duplicados, apps em segundo plano, programas instalados e relatório."""

from __future__ import annotations

import os
import time
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core import process_manager, storage_analyzer, system_info
from core.report import SESSION, build_report, save_report
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, DANGER_BUTTON, SECONDARY_BUTTON, Column, DataTable, Row, button_bar,
                         confirm, font, show_error, show_info, show_text)
from utils.helpers import format_int, format_size, logs_dir, open_in_explorer, open_uri

KIND_OPTIONS = ["Imagem de disco (ISO/VHD)", "Instalador baixado", "Dump de memória", "Temporário/log/backup",
                "Arquivo compactado"]


def _folder_picker(parent, var: ctk.StringVar) -> ctk.CTkFrame:
    bar = button_bar(parent)
    ctk.CTkLabel(bar, text="Pasta:", font=font(12)).pack(side="left")
    ctk.CTkEntry(bar, textvariable=var, width=380).pack(side="left", padx=6)

    def browse():
        path = filedialog.askdirectory(initialdir=var.get() or "C:\\")
        if path:
            var.set(os.path.normpath(path))

    ctk.CTkButton(bar, text="Procurar…", width=90, command=browse, **SECONDARY_BUTTON).pack(side="left")
    return bar


class ToolsPage(BasePage):
    title = "Ferramentas"
    subtitle = ("Scanners opcionais. Nada é excluído automaticamente: você escolhe os arquivos e eles vão para a "
                "LIXEIRA (podem ser restaurados). Pastas do Windows e de programas nunca são analisadas.")

    def build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        tabs = ctk.CTkTabview(self)
        tabs.grid(row=1, column=0, sticky="nsew")
        self._build_large(tabs.add("Arquivos grandes"))
        self._build_duplicates(tabs.add("Duplicados"))
        self._build_background(tabs.add("Apps em segundo plano"))
        self._build_programs(tabs.add("Programas instalados"))
        self._build_report(tabs.add("Relatório"))
        self.tabs = tabs

    # ------------------------------------------------------- arquivos grandes
    def _build_large(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        self.large_folder = ctk.StringVar(value=os.environ.get("USERPROFILE", "C:\\"))
        _folder_picker(tab, self.large_folder).grid(row=0, column=0, sticky="ew", pady=(4, 4))
        opts = button_bar(tab)
        opts.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(opts, text="Tamanho mínimo (MB):", font=font(12)).pack(side="left")
        self.min_mb = ctk.CTkEntry(opts, width=70)
        self.min_mb.insert(0, "500")
        self.min_mb.pack(side="left", padx=(4, 14))
        ctk.CTkLabel(opts, text="Sem modificação há (dias, 0 = qualquer):", font=font(12)).pack(side="left")
        self.min_age = ctk.CTkEntry(opts, width=60)
        self.min_age.insert(0, "0")
        self.min_age.pack(side="left", padx=(4, 14))
        self.kind_vars = {}
        kinds_bar = button_bar(tab)
        kinds_bar.grid(row=2, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(kinds_bar, text="Incluir também (>10 MB):", font=font(12)).pack(side="left")
        for k in KIND_OPTIONS:
            var = ctk.BooleanVar(value=k in ("Imagem de disco (ISO/VHD)", "Instalador baixado", "Dump de memória"))
            ctk.CTkCheckBox(kinds_bar, text=k, variable=var, font=font(12)).pack(side="left", padx=6)
            self.kind_vars[k] = var
        cols = [Column("path", "Arquivo", 420, stretch=True), Column("size", "Tamanho", 100, "e"),
                Column("date", "Modificado", 110), Column("age", "Dias", 60, "e"), Column("kind", "Tipo", 180),
                Column("folder", "Pasta", 300)]
        self.large_table = DataTable(tab, cols, checkable=True, height=12, on_check=self._large_sel,
                                     on_double=lambda rid: self._open(self.large_table, rid))
        self.large_table.grid(row=3, column=0, sticky="nsew", pady=4)
        actions = button_bar(tab)
        actions.grid(row=4, column=0, sticky="ew", pady=4)
        ctk.CTkButton(actions, text="Buscar", command=self.scan_large, width=100).pack(side="left")
        ctk.CTkButton(actions, text="Cancelar", command=self.app.cancel_task, width=90,
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="Enviar marcados para a Lixeira…", command=self.recycle_large,
                      **DANGER_BUTTON).pack(side="left", padx=6)
        self.large_label = ctk.CTkLabel(actions, text="Duplo clique abre o local do arquivo.", font=font(12),
                                        text_color=COLOR_MUTED)
        self.large_label.pack(side="left", padx=10)

    def _int(self, entry, default: int) -> int:
        try:
            return max(0, int(entry.get().strip()))
        except ValueError:
            return default

    def scan_large(self) -> None:
        root = Path(self.large_folder.get().strip())
        if not root.is_dir():
            show_error("Pasta inválida", f"A pasta não existe:\n{root}")
            return
        min_mb, age = self._int(self.min_mb, 500), self._int(self.min_age, 0)
        kinds = {k for k, v in self.kind_vars.items() if v.get()}

        def done(files):
            self.large_table.set_rows([
                Row(str(i), [str(f.path), format_size(f.size), time.strftime("%d/%m/%Y", time.localtime(f.modified)),
                             f.age_days, f.kind, str(f.path.parent)],
                    sort=[str(f.path).lower(), f.size, f.modified, f.age_days, f.kind, str(f.path.parent).lower()],
                    data=f.path)
                for i, f in enumerate(files)])
            total = sum(f.size for f in files)
            self.large_label.configure(text=f"{len(files)} arquivo(s), {format_size(total)}. Marque o que deseja remover.")
            self.app.set_status("Busca de arquivos grandes concluída.")

        self.app.run_exclusive(f"Procurando arquivos grandes em {root}…",
                               lambda p, c: storage_analyzer.find_large_files(root, min_mb, age, kinds, c, p),
                               done, cancellable=True)

    def _large_sel(self) -> None:
        rows = self.large_table.checked_rows()
        size = sum(Path(r.data).stat().st_size for r in rows if Path(r.data).exists())
        self.large_label.configure(text=f"{len(rows)} marcado(s): {format_size(size)}")

    def recycle_large(self) -> None:
        paths = [Path(r.data) for r in self.large_table.checked_rows()]
        self._recycle(paths, lambda: self.scan_large())

    def _recycle(self, paths: list[Path], after) -> None:
        if not paths:
            show_info("Nada marcado", "Marque os arquivos na tabela (clique na caixa ☐).")
            return
        total = 0
        for p in paths:
            try:
                total += p.stat().st_size
            except OSError:
                pass
        details = [str(p) for p in paths[:200]]
        if len(paths) > 200:
            details.append(f"… e mais {len(paths) - 200} arquivo(s)")
        if not confirm("Enviar para a Lixeira", f"Enviar {len(paths)} arquivo(s) ({format_size(total)}) para a "
                                                "Lixeira?", details, confirm_text="Enviar para a Lixeira", danger=True,
                       warning="Os arquivos podem ser restaurados pela Lixeira do Windows. Se algum não couber na "
                               "Lixeira, o próprio Windows perguntará antes de excluí-lo definitivamente."):
            return

        def done(res):
            count, freed, errors = res
            SESSION.add(f"{count} arquivo(s) enviados para a Lixeira ({format_size(freed)})", freed=freed)
            for e in errors:
                SESSION.add(e, ok=False)
            text = f"{count} arquivo(s) enviados para a Lixeira ({format_size(freed)})."
            if errors:
                text += "\n\nNão enviados:\n" + "\n".join(errors[:30])
            show_text("Resultado", text)
            after()

        self.app.run_exclusive("Enviando para a Lixeira…", lambda p: storage_analyzer.recycle_files(paths, p), done)

    def _open(self, table: DataTable, row_id: str) -> None:
        row = table.row(row_id)
        if row and row.data:
            open_in_explorer(Path(row.data))

    # ------------------------------------------------------------ duplicados
    def _build_duplicates(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        self.dup_folder = ctk.StringVar(value=os.path.join(os.environ.get("USERPROFILE", "C:\\"), "Downloads"))
        _folder_picker(tab, self.dup_folder).grid(row=0, column=0, sticky="ew", pady=(4, 4))
        opts = button_bar(tab)
        opts.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkLabel(opts, text="Ignorar arquivos menores que (KB):", font=font(12)).pack(side="left")
        self.dup_min = ctk.CTkEntry(opts, width=80)
        self.dup_min.insert(0, "1024")
        self.dup_min.pack(side="left", padx=6)
        ctk.CTkLabel(opts, text="Comparação por conteúdo (hash BLAKE2), não apenas por nome.",
                     text_color=COLOR_MUTED, font=font(12)).pack(side="left", padx=10)
        cols = [Column("group", "Grupo", 70, "e"), Column("name", "Arquivo", 260),
                Column("size", "Tamanho", 100, "e"), Column("folder", "Localização", 560, stretch=True)]
        self.dup_table = DataTable(tab, cols, checkable=True, height=14, on_check=self._dup_sel,
                                   on_double=lambda rid: self._open(self.dup_table, rid))
        self.dup_table.grid(row=2, column=0, sticky="nsew", pady=4)
        actions = button_bar(tab)
        actions.grid(row=3, column=0, sticky="ew", pady=4)
        ctk.CTkButton(actions, text="Buscar duplicados", command=self.scan_dups).pack(side="left")
        ctk.CTkButton(actions, text="Cancelar", command=self.app.cancel_task, width=90,
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(actions, text="Enviar marcados para a Lixeira…", command=self.recycle_dups,
                      **DANGER_BUTTON).pack(side="left", padx=6)
        self.dup_label = ctk.CTkLabel(actions, text="Pelo menos uma cópia de cada grupo sempre é mantida.",
                                      font=font(12), text_color=COLOR_MUTED)
        self.dup_label.pack(side="left", padx=10)
        self.dup_groups: list[storage_analyzer.DuplicateGroup] = []

    def scan_dups(self) -> None:
        root = Path(self.dup_folder.get().strip())
        if not root.is_dir():
            show_error("Pasta inválida", f"A pasta não existe:\n{root}")
            return
        min_kb = self._int(self.dup_min, 1024)

        def done(groups):
            self.dup_groups = groups
            rows = []
            for gi, g in enumerate(groups, 1):
                for fi, f in enumerate(g.files):
                    rows.append(Row(f"{gi}:{fi}", [gi, f.name, format_size(g.size), str(f.parent)],
                                    sort=[gi * 10000 + fi, f.name.lower(), g.size, str(f.parent).lower()],
                                    data=f))
            self.dup_table.set_rows(rows)
            wasted = sum(g.wasted for g in groups)
            self.dup_label.configure(text=f"{len(groups)} grupo(s) de duplicados — {format_size(wasted)} "
                                          "ocupados por cópias extras.")
            self.app.set_status("Busca de duplicados concluída.")

        self.app.run_exclusive(f"Procurando duplicados em {root}…",
                               lambda p, c: storage_analyzer.find_duplicates(root, min_kb, c, p), done,
                               cancellable=True)

    def _dup_sel(self) -> None:
        rows = self.dup_table.checked_rows()
        self.dup_label.configure(text=f"{len(rows)} arquivo(s) marcado(s).")

    def recycle_dups(self) -> None:
        selected = {Path(r.data) for r in self.dup_table.checked_rows()}
        problem = storage_analyzer.validate_duplicate_selection(self.dup_groups, selected)
        if problem:
            show_error("Seleção inválida", problem)
            return
        self._recycle(sorted(selected), lambda: self.scan_dups())

    # ----------------------------------------------------- segundo plano
    def _build_background(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        bar = button_bar(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        ctk.CTkButton(bar, text="Analisar apps em segundo plano", command=self.scan_background).pack(side="left")
        ctk.CTkButton(bar, text="Fechar app selecionado…", command=self.close_background,
                      **DANGER_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Abrir configurações de apps", command=lambda: open_uri("ms-settings:appsfeatures"),
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkLabel(bar, text="Apps do usuário sem janela visível. Nada é bloqueado automaticamente.",
                     text_color=COLOR_MUTED, font=font(12)).pack(side="left", padx=10)
        cols = [Column("name", "App", 200), Column("inst", "Processos", 80, "e"), Column("ram", "RAM", 100, "e"),
                Column("cpu", "CPU", 70, "e"), Column("rec", "Recomendação", 480, stretch=True),
                Column("path", "Caminho", 360)]
        self.bg_table = DataTable(tab, cols, height=14)
        self.bg_table.grid(row=1, column=0, sticky="nsew")
        self.bg_apps: dict[str, process_manager.BackgroundApp] = {}

    def scan_background(self) -> None:
        def done(apps):
            self.bg_apps = {a.exe.lower(): a for a in apps}
            self.bg_table.set_rows([
                Row(a.exe.lower(), [a.name, a.instances, format_size(a.memory_bytes), f"{a.cpu_percent:.1f}%",
                                    a.recommendation, a.exe],
                    sort=[a.name.lower(), a.instances, a.memory_bytes, a.cpu_percent, "", a.exe.lower()],
                    tags=("warn",) if a.memory_bytes > 300 * 1024 ** 2 else ())
                for a in apps])
            self.app.set_status(f"{len(apps)} app(s) em segundo plano encontrados.")
        self.app.set_status("Analisando apps em segundo plano…")
        self.app.runner.submit(lambda _p: process_manager.list_background_apps(), on_done=done)

    def close_background(self) -> None:
        ids = self.bg_table.selected_ids()
        app = self.bg_apps.get(ids[0]) if ids else None
        if app is None:
            show_info("Selecione um app", "Selecione um app na lista.")
            return
        if not confirm("Fechar app", f"Fechar '{app.name}' ({app.instances} processo(s))?",
                       [f"Caminho: {app.exe}", f"RAM total: {format_size(app.memory_bytes)}",
                        "O app será aberto novamente no próximo login se estiver na inicialização."],
                       confirm_text="Fechar", danger=True,
                       warning="Dados não salvos neste app serão perdidos."):
            return

        def work(_p):
            return [process_manager.terminate_process(pid, app.name) for pid in app.pids]

        def done(results):
            ok = sum(1 for r in results if r[0])
            SESSION.add(f"Fechado {app.name}: {ok}/{len(results)} processo(s)", ok=ok > 0)
            self.scan_background()

        self.app.run_exclusive(f"Fechando {app.name}…", work, done)

    # --------------------------------------------------- programas instalados
    def _build_programs(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        bar = button_bar(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        ctk.CTkButton(bar, text="Carregar lista", command=self.load_programs).pack(side="left")
        ctk.CTkButton(bar, text="Abrir 'Aplicativos instalados' do Windows",
                      command=lambda: open_uri("ms-settings:appsfeatures"), **SECONDARY_BUTTON).pack(side="left", padx=6)
        self.prog_filter = ctk.StringVar()
        self.prog_filter.trace_add("write", lambda *_: self._filter_programs())
        ctk.CTkEntry(bar, textvariable=self.prog_filter, placeholder_text="Filtrar…", width=200).pack(side="right")
        self.prog_label = ctk.CTkLabel(bar, text="Desinstalações devem ser feitas pelo Windows.",
                                       text_color=COLOR_MUTED, font=font(12))
        self.prog_label.pack(side="left", padx=10)
        cols = [Column("name", "Programa", 340, stretch=True), Column("version", "Versão", 120),
                Column("publisher", "Fabricante", 220), Column("size", "Tamanho", 100, "e"),
                Column("date", "Instalado em", 100), Column("scope", "Escopo", 120)]
        self.prog_table = DataTable(tab, cols, height=16)
        self.prog_table.grid(row=1, column=0, sticky="nsew")
        self.programs: list[system_info.InstalledProgram] = []

    def load_programs(self) -> None:
        def done(programs):
            self.programs = programs
            self._filter_programs()
        self.app.runner.submit(lambda _p: system_info.installed_programs(), on_done=done)

    def _filter_programs(self) -> None:
        text = self.prog_filter.get().strip().lower()
        rows = []
        for i, p in enumerate(self.programs):
            if text and text not in p.name.lower() and text not in p.publisher.lower():
                continue
            date = f"{p.install_date[6:8]}/{p.install_date[4:6]}/{p.install_date[:4]}" \
                if len(p.install_date) == 8 and p.install_date.isdigit() else ""
            rows.append(Row(str(i), [p.name, p.version, p.publisher, format_size(p.size_bytes) if p.size_bytes else "—",
                                     date, p.scope],
                            sort=[p.name.lower(), p.version, p.publisher.lower(), p.size_bytes, p.install_date,
                                  p.scope]))
        self.prog_table.set_rows(rows)
        self.prog_label.configure(text=f"{len(rows)} programa(s). Desinstalações devem ser feitas pelo Windows.")

    # --------------------------------------------------------------- relatório
    def _build_report(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tab, text="Gera um relatório com problemas encontrados, espaço recuperável, programas de "
                               "inicialização, processos pesados, recomendações, otimizações aplicadas nesta sessão "
                               "e erros. O arquivo é salvo na pasta de logs.",
                     wraplength=900, justify="left", anchor="w", font=font(12)).grid(row=0, column=0, sticky="ew",
                                                                                     padx=10, pady=10)
        bar = button_bar(tab)
        bar.grid(row=1, column=0, sticky="w", padx=10)
        ctk.CTkButton(bar, text="Gerar relatório", command=self.make_report).pack(side="left")
        ctk.CTkButton(bar, text="Abrir pasta de logs", command=lambda: open_in_explorer(logs_dir()),
                      **SECONDARY_BUTTON).pack(side="left", padx=8)

    def make_report(self) -> None:
        if self.app.analysis is None:
            show_info("Análise necessária", "Execute 'ANALISAR PC' antes de gerar o relatório.")
            return
        text = build_report(self.app.analysis)
        path = save_report(text)
        show_text(f"Relatório — salvo em {path.name}", text,
                  extra_button=("Abrir pasta", lambda: open_in_explorer(path)))

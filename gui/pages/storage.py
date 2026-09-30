"""Armazenamento: unidades (SSD/HDD), TRIM, otimização e pastas que mais ocupam espaço."""

from __future__ import annotations

import os
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core import storage_analyzer
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, COLOR_OK, COLOR_WARN, SECONDARY_BUTTON, Column, DataTable, OutputConsole, Row,
                         button_bar, confirm, font, show_error, show_info)
from utils.admin import is_admin
from utils.helpers import format_int, format_size, open_in_explorer


class StoragePage(BasePage):
    title = "Armazenamento"
    subtitle = ("Espaço por unidade, tipo de disco e otimização nativa do Windows. Em SSD é executado apenas o "
                "ReTrim — desfragmentação tradicional nunca é usada em SSD.")

    def build(self) -> None:
        self.drives: dict[str, storage_analyzer.DriveInfo] = {}
        self.grid_rowconfigure(1, weight=1)
        tabs = ctk.CTkTabview(self)
        tabs.grid(row=1, column=0, sticky="nsew")
        self._build_drives(tabs.add("Unidades"))
        self._build_folders(tabs.add("Pastas que mais ocupam espaço"))

    # ------------------------------------------------------------- unidades
    def _build_drives(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        cols = [Column("drive", "Unidade", 80), Column("type", "Tipo", 110), Column("model", "Modelo", 220),
                Column("fs", "Sistema de arquivos", 130), Column("total", "Total", 100, "e"),
                Column("used", "Usado", 100, "e"), Column("free", "Livre", 100, "e"),
                Column("pct", "Uso", 70, "e")]
        self.drive_table = DataTable(tab, cols, height=6)
        self.drive_table.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        info = ctk.CTkFrame(tab, corner_radius=8)
        info.grid(row=1, column=0, sticky="ew")
        info.grid_columnconfigure(0, weight=1)
        self.trim_label = ctk.CTkLabel(info, text="TRIM: verificando…", anchor="w", font=font(12))
        self.trim_label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        self.trim_button = ctk.CTkButton(info, text="Ativar TRIM…", command=self.enable_trim, width=130)
        bar = button_bar(tab)
        bar.grid(row=2, column=0, sticky="ew", pady=8)
        ctk.CTkButton(bar, text="Atualizar", command=self.refresh, width=100, **SECONDARY_BUTTON).pack(
            side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Analisar unidade selecionada", command=lambda: self.optimize(True),
                      **SECONDARY_BUTTON).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Otimizar unidade selecionada…", command=lambda: self.optimize(False)).pack(
            side="left", padx=6)
        if not is_admin():
            ctk.CTkLabel(bar, text="Otimização e TRIM requerem Administrador.", text_color=COLOR_WARN,
                         font=font(12)).pack(side="left", padx=10)
        self.console = OutputConsole(tab, height=160)
        self.console.grid(row=3, column=0, sticky="nsew")

    def load(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        def work(_p):
            return storage_analyzer.drives_with_media(), storage_analyzer.trim_status()
        self.app.runner.submit(work, on_done=self._populate)

    def _populate(self, data) -> None:
        drives, trim = data
        self.drives = {d.letter: d for d in drives}
        self.drive_table.set_rows([
            Row(d.letter, [f"{d.letter}:", d.media + (" (removível)" if d.removable else ""), d.model or "—",
                           d.fstype, format_size(d.total), format_size(d.used), format_size(d.free),
                           f"{d.percent:.0f}%"],
                sort=[d.letter, d.media, d.model, d.fstype, d.total, d.used, d.free, d.percent],
                tags=("warn",) if d.percent >= 85 else ())
            for d in drives])
        ntfs = trim.get("NTFS")
        if ntfs is True:
            self.trim_label.configure(text="TRIM (NTFS): ✔ ativado — SSDs recebem TRIM automaticamente.",
                                      text_color=COLOR_OK)
            self.trim_button.grid_remove()
        elif ntfs is False:
            has_ssd = any(d.media == "SSD" for d in drives)
            self.trim_label.configure(text="TRIM (NTFS): desativado" + (" — recomendado ativar para SSD." if has_ssd
                                                                         else "."), text_color=COLOR_WARN)
            self.trim_button.grid(row=0, column=1, padx=12)
        else:
            self.trim_label.configure(text="TRIM: não foi possível verificar.", text_color=COLOR_MUTED)

    def _selected_drive(self):
        ids = self.drive_table.selected_ids()
        return self.drives.get(ids[0]) if ids else None

    def optimize(self, analyze_only: bool) -> None:
        d = self._selected_drive()
        if d is None:
            show_info("Selecione uma unidade", "Selecione uma unidade na tabela.")
            return
        if not is_admin():
            show_info("Requer Administrador", "Use 'Reiniciar como Administrador' no topo da janela.")
            return
        if analyze_only:
            op, desc = "Analisar", "Somente análise de fragmentação (não altera nada)."
        elif d.media == "SSD":
            op, desc = "Otimizar (ReTrim)", "SSD: envia TRIM para os blocos livres. Sem desfragmentação."
        elif d.media == "HDD":
            op, desc = "Otimizar (desfragmentar)", "HDD: desfragmentação/otimização padrão do Windows."
        else:
            op, desc = "Analisar", "Tipo de mídia desconhecido: por segurança será feita apenas a análise."
        if not confirm(f"{op} {d.letter}:", f"{op} a unidade {d.letter}: ({d.media})?",
                       [desc, "Ferramenta nativa: defrag.exe (Otimizar Unidades do Windows).",
                        "Pode levar vários minutos; o computador pode ser usado normalmente."],
                       confirm_text=op):
            return
        self.console.clear()

        def done(code):
            ok = code == 0
            SESSION.add(f"{op} {d.letter}: código {code}", ok=ok)
            self.app.set_status(f"{op} {d.letter}: concluído (código {code}).")

        self.app.run_exclusive(f"{op} {d.letter}:…",
                               lambda progress: storage_analyzer.optimize_drive(d, progress, analyze_only),
                               done, on_progress=self._on_line)

    def _on_line(self, line: str) -> None:
        self.console.append(line)
        self.app.set_status(line[:150])

    def enable_trim(self) -> None:
        if not is_admin():
            show_info("Requer Administrador", "Use 'Reiniciar como Administrador' no topo da janela.")
            return
        if not confirm("Ativar TRIM", "Ativar TRIM para NTFS (fsutil behavior set DisableDeleteNotify NTFS 0)?",
                       ["É a configuração padrão do Windows para SSDs.", "Reversível em Histórico › Desfazer."]):
            return

        def done(res):
            ok, msg = res
            SESSION.add(f"TRIM: {msg}", ok=ok)
            (show_info if ok else show_error)("TRIM", msg)
            self.refresh()

        self.app.run_exclusive("Ativando TRIM…", lambda _p: storage_analyzer.enable_trim(), done)

    # --------------------------------------------------------------- pastas
    def _build_folders(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        bar = button_bar(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(4, 8))
        self.folder_var = ctk.StringVar(value=os.environ.get("USERPROFILE", "C:\\"))
        ctk.CTkEntry(bar, textvariable=self.folder_var, width=420).pack(side="left")
        ctk.CTkButton(bar, text="Procurar…", width=100, command=self._browse, **SECONDARY_BUTTON).pack(
            side="left", padx=6)
        ctk.CTkButton(bar, text="Analisar pasta", width=130, command=self.analyze_folder).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Cancelar", width=90, command=self.app.cancel_task, **SECONDARY_BUTTON).pack(
            side="left", padx=6)
        ctk.CTkLabel(bar, text="Somente leitura. Duplo clique abre a pasta.", text_color=COLOR_MUTED,
                     font=font(12)).pack(side="left", padx=10)
        cols = [Column("path", "Pasta", 560, stretch=True), Column("size", "Tamanho", 120, "e"),
                Column("files", "Arquivos", 100, "e"), Column("pct", "% do total", 90, "e")]
        self.folder_table = DataTable(tab, cols, height=16, on_double=self._open_folder)
        self.folder_table.grid(row=1, column=0, sticky="nsew")

    def _browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.folder_var.get() or "C:\\")
        if path:
            self.folder_var.set(os.path.normpath(path))

    def analyze_folder(self) -> None:
        root = Path(self.folder_var.get().strip())
        if not root.is_dir():
            show_error("Pasta inválida", f"A pasta não existe:\n{root}")
            return

        def done(results):
            total = sum(r[1] for r in results) or 1
            self.folder_table.set_rows([
                Row(str(i), [str(p), format_size(size), format_int(files), f"{size * 100 / total:.1f}%"],
                    sort=[str(p).lower(), size, files, size], data=p)
                for i, (p, size, files) in enumerate(results)])
            self.app.set_status(f"Análise de {root} concluída: {format_size(total)}.")

        self.app.run_exclusive(f"Analisando {root}…",
                               lambda p, c: storage_analyzer.top_directories(root, c, p), done, cancellable=True)

    def _open_folder(self, row_id: str) -> None:
        row = self.folder_table.row(row_id)
        if row and row.data and Path(row.data).exists():
            open_in_explorer(row.data)

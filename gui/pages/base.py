"""Classe base das páginas."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from gui.widgets import section_title

if TYPE_CHECKING:
    from gui.app import App


class BasePage(ctk.CTkFrame):
    title = ""
    subtitle = ""

    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._loaded = False
        self.grid_columnconfigure(0, weight=1)
        section_title(self, self.title, self.subtitle).grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 10))
        self.build()

    def build(self) -> None:
        """Monta os widgets da página."""

    def on_show(self) -> None:
        """Chamado toda vez que a página é exibida; carrega dados na 1ª vez."""
        if not self._loaded:
            self._loaded = True
            self.load()

    def load(self) -> None:
        """Carregamento inicial (lazy) — nada roda até o usuário abrir a página."""

    def on_analysis(self) -> None:
        """Chamado quando uma nova análise completa termina."""

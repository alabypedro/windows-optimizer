"""Ferramentas de DNS e rede (opcionais)."""

from __future__ import annotations

import customtkinter as ctk

from core import network_tools
from core.report import SESSION
from gui.pages.base import BasePage
from gui.widgets import (COLOR_MUTED, SECONDARY_BUTTON, Column, DataTable, OutputConsole, Row, button_bar,
                         confirm, font)


class NetworkPage(BasePage):
    title = "Rede"
    subtitle = ("Ferramentas de diagnóstico. Nenhuma configuração de rede é alterada de forma permanente: "
                "o flush de DNS e a renovação de IP são refeitos automaticamente pelo Windows.")

    def build(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(4, weight=1)
        bar = button_bar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for text, cmd, style in [
            ("Atualizar adaptadores", self.load_adapters, SECONDARY_BUTTON),
            ("Testar conexão", self.test_connection, {}),
            ("Testar DNS", self.test_dns, {}),
            ("Flush DNS", self.flush_dns, {}),
            ("Renovar IP", self.renew_ip, SECONDARY_BUTTON),
            ("Liberar e renovar DHCP…", self.release_renew, SECONDARY_BUTTON),
        ]:
            ctk.CTkButton(bar, text=text, command=cmd, **style).pack(side="left", padx=(0, 8))

        cols = [Column("name", "Adaptador", 260), Column("state", "Estado", 90), Column("speed", "Velocidade", 110, "e"),
                Column("ipv4", "IPv4", 200), Column("ipv6", "IPv6", 300, stretch=True), Column("mac", "MAC", 150)]
        self.table = DataTable(self, cols, height=8)
        self.table.grid(row=2, column=0, sticky="nsew")
        ctk.CTkLabel(self, text="Resultado", font=font(14, "bold"), anchor="w").grid(
            row=3, column=0, sticky="ew", pady=(10, 2))
        self.console = OutputConsole(self, height=220)
        self.console.grid(row=4, column=0, sticky="nsew")

    def load(self) -> None:
        self.load_adapters()

    def load_adapters(self) -> None:
        def done(adapters):
            self.table.set_rows([
                Row(a.name, [a.name, "Conectado" if a.is_up else "Desconectado",
                             f"{a.speed_mbps} Mbps" if a.speed_mbps else "—", ", ".join(a.ipv4) or "—",
                             ", ".join(a.ipv6) or "—", a.mac or "—"],
                    sort=[a.name.lower(), a.is_up, a.speed_mbps, ",".join(a.ipv4), "", a.mac],
                    tags=() if a.is_up else ("muted",))
                for a in adapters])
        self.app.runner.submit(lambda _p: network_tools.list_adapters(), on_done=done)

    def _run(self, label: str, fn) -> None:
        self.console.append(f"\n> {label}")
        self.app.run_exclusive(f"{label}…", lambda _p: fn(), self._print)

    def _print(self, result) -> None:
        if isinstance(result, list):
            for line in result:
                self.console.append(line)
        elif isinstance(result, str):
            self.console.append(result)
        elif isinstance(result, tuple):
            for r in result:
                self._print(r)
        else:  # CommandResult
            self.console.append((result.stdout or result.stderr or result.summary()).strip())
            SESSION.add(f"Rede: {' '.join(result.args)} ({'OK' if result.ok else result.summary()})", ok=result.ok)
        self.app.set_status("Concluído.")

    def test_connection(self) -> None:
        self._run("ping 8.8.8.8 + TCP 443", lambda: (network_tools.test_connection().stdout.strip(),
                                                    network_tools.test_tcp()))

    def test_dns(self) -> None:
        self._run("Teste de resolução DNS", network_tools.test_dns)

    def flush_dns(self) -> None:
        if confirm("Flush DNS", "Executar 'ipconfig /flushdns'?",
                   ["Limpa o cache de nomes de sites.", "Não altera configurações de rede."]):
            self._run("ipconfig /flushdns", network_tools.flush_dns)

    def renew_ip(self) -> None:
        if confirm("Renovar IP", "Executar 'ipconfig /renew'?",
                   ["Solicita a renovação do endereço IP ao roteador (DHCP).",
                    "A conexão pode oscilar por alguns segundos."]):
            self._run("ipconfig /renew", network_tools.renew_ip)

    def release_renew(self) -> None:
        if confirm("Liberar e renovar DHCP", "Executar 'ipconfig /release' seguido de 'ipconfig /renew'?",
                   ["A conexão com a internet CAIRÁ por alguns segundos até o novo IP ser obtido.",
                    "Downloads e chamadas em andamento podem ser interrompidos.",
                    "Adaptadores com IP fixo não são afetados."],
                   confirm_text="Liberar e renovar", danger=True):
            self._run("ipconfig /release + /renew", network_tools.release_and_renew)

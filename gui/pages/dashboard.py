"""Dashboard: diagnóstico, pontuação de saúde explicada e recomendações."""

from __future__ import annotations

import customtkinter as ctk

from core import system_info
from core.recommendations import build_recommendations, compute_health
from gui.pages.base import BasePage
from gui.widgets import (COLOR_DANGER, COLOR_INFO, COLOR_MUTED, COLOR_OK, COLOR_TEXT, COLOR_WARN, Column, DataTable, Row,
                         StatCard, font)
from utils.helpers import format_duration, format_size

LEVEL_COLORS = {"ok": COLOR_OK, "atenção": COLOR_WARN, "crítico": COLOR_DANGER, "info": COLOR_INFO}
PRIORITY_COLORS = {"alta": COLOR_DANGER, "média": COLOR_WARN, "baixa": COLOR_INFO}


class DashboardPage(BasePage):
    title = "Saúde do PC"
    subtitle = ("Diagnóstico somente leitura. Nada é alterado sem que você escolha, revise e confirme.")

    def build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="cards")
        self.body = body

        # Pontuação + fatores
        score_box = ctk.CTkFrame(body, corner_radius=12)
        score_box.grid(row=0, column=0, columnspan=4, sticky="ew", padx=4, pady=(0, 10))
        score_box.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(score_box, fg_color="transparent")
        left.grid(row=0, column=0, padx=20, pady=14, sticky="n")
        ctk.CTkLabel(left, text="Pontuação de saúde", font=font(13), text_color=COLOR_MUTED).pack()
        self.score_label = ctk.CTkLabel(left, text="…", font=font(46, "bold"))
        self.score_label.pack()
        self.score_hint = ctk.CTkLabel(left, text="Analisando…", font=font(12), text_color=COLOR_MUTED)
        self.score_hint.pack()
        self.factors_frame = ctk.CTkFrame(score_box, fg_color="transparent")
        self.factors_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=14)

        # Cartões
        names = ["CPU", "Memória RAM", "Disco do sistema", "Tempo ligado", "Temporários",
                 "Temperaturas", "Antivírus / Firewall", "Windows Update", "Inicialização",
                 "Programas instalados", "Sistema", "Lixeira"]
        self.cards: dict[str, StatCard] = {}
        for i, name in enumerate(names):
            card = StatCard(body, name)
            card.grid(row=1 + i // 4, column=i % 4, sticky="nsew", padx=4, pady=4)
            self.cards[name] = card

        # Recomendações
        ctk.CTkLabel(body, text="Recomendações para este PC", font=font(16, "bold"), anchor="w").grid(
            row=4, column=0, columnspan=4, sticky="ew", padx=6, pady=(14, 4))
        self.recs_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.recs_frame.grid(row=5, column=0, columnspan=4, sticky="ew", padx=4)
        self.recs_frame.grid_columnconfigure(0, weight=1)

        # Unidades
        ctk.CTkLabel(body, text="Unidades", font=font(16, "bold"), anchor="w").grid(
            row=6, column=0, columnspan=4, sticky="ew", padx=6, pady=(14, 4))
        self.drives_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.drives_frame.grid(row=7, column=0, columnspan=4, sticky="ew", padx=4)
        self.drives_frame.grid_columnconfigure(1, weight=1)

        # Processos
        ctk.CTkLabel(body, text="Processos que mais consomem CPU", font=font(14, "bold"), anchor="w").grid(
            row=8, column=0, columnspan=2, sticky="ew", padx=6, pady=(14, 4))
        ctk.CTkLabel(body, text="Processos que mais consomem RAM", font=font(14, "bold"), anchor="w").grid(
            row=8, column=2, columnspan=2, sticky="ew", padx=6, pady=(14, 4))
        cols_cpu = [Column("name", "Processo", 200, stretch=True), Column("pid", "PID", 70, "e"),
                    Column("cpu", "CPU", 80, "e")]
        cols_ram = [Column("name", "Processo", 200, stretch=True), Column("pid", "PID", 70, "e"),
                    Column("ram", "RAM", 100, "e")]
        self.cpu_table = DataTable(body, cols_cpu, height=8)
        self.cpu_table.grid(row=9, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 10))
        self.ram_table = DataTable(body, cols_ram, height=8)
        self.ram_table.grid(row=9, column=2, columnspan=2, sticky="ew", padx=4, pady=(0, 10))

    def load(self) -> None:
        self.app.runner.submit(lambda _p: len(system_info.installed_programs()),
                               on_done=lambda n: self.cards["Programas instalados"].set(
                                   str(n), "Veja a lista em Ferramentas › Programas"))
        if self.app.analysis is not None:
            self.on_analysis()

    # ------------------------------------------------------------------
    def on_analysis(self) -> None:
        a = self.app.analysis
        if a is None:
            return
        s, hw = a.snapshot, a.snapshot.hardware
        score, factors = compute_health(a)
        color = COLOR_OK if score >= 80 else COLOR_WARN if score >= 60 else COLOR_DANGER
        self.score_label.configure(text=str(score), text_color=color)
        self.score_hint.configure(text="Bom" if score >= 80 else "Pode melhorar" if score >= 60 else "Atenção")
        for w in self.factors_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.factors_frame, text="Como a pontuação foi calculada (100 menos as penalidades):",
                     font=font(12, "bold"), anchor="w").pack(fill="x")
        for f in factors:
            penalty = f"−{f.penalty}" if f.penalty else "  0"
            ctk.CTkLabel(self.factors_frame, text=f"{penalty:>4}   {f.name}: {f.detail}", anchor="w",
                         justify="left", wraplength=820, font=font(12),
                         text_color=LEVEL_COLORS.get(f.level)).pack(fill="x")

        self.cards["CPU"].set(f"{s.cpu_percent:.0f}%", f"{s.cpu_name}\n{s.physical_cores} núcleos / "
                                                      f"{s.logical_cores} threads")
        ram_color = COLOR_DANGER if s.ram_percent >= 90 else COLOR_WARN if s.ram_percent >= 75 else COLOR_TEXT
        self.cards["Memória RAM"].set(f"{s.ram_percent:.0f}%", f"{format_size(s.ram_used)} em uso de "
                                                             f"{format_size(s.ram_total)}", ram_color)
        sys_drive = next((d for d in s.drives if f"{d['letter']}:".upper() == hw.system_drive.upper()), None)
        if sys_drive:
            free_color = COLOR_DANGER if hw.system_free_pct < 10 else COLOR_WARN if hw.system_free_pct < 20 else COLOR_TEXT
            self.cards["Disco do sistema"].set(
                f"{format_size(sys_drive['free'])} livres",
                f"{hw.system_drive} {hw.system_disk_type} — {hw.system_free_pct:.0f}% livre de "
                f"{format_size(sys_drive['total'])}", free_color)
        self.cards["Tempo ligado"].set(format_duration(s.uptime_seconds), "desde o último boot")
        self.cards["Temporários"].set(format_size(a.temp_bytes + a.browser_bytes),
                                      f"{a.temp_files} arquivos temporários + caches de navegador "
                                      f"({format_size(a.browser_bytes)})")
        if s.temperatures:
            first = next(iter(s.temperatures.items()))
            self.cards["Temperaturas"].set(first[1], "\n".join(f"{k}: {v}" for k, v in s.temperatures.items()))
        else:
            self.cards["Temperaturas"].set("Indisponível", "O Windows não expõe a temperatura de CPU de forma "
                                                           "confiável sem drivers do fabricante.")
        sec = a.security
        if sec.other_antivirus:
            av = sec.other_antivirus[0]
            av_ok = True
        else:
            av = "Microsoft Defender"
            av_ok = bool(sec.realtime_enabled)
        fw = "Firewall ativo" if sec.firewall_enabled else "Firewall: verificar" if sec.firewall_enabled is None \
            else "Firewall DESATIVADO"
        self.cards["Antivírus / Firewall"].set("Protegido" if av_ok and sec.firewall_enabled is not False
                                               else "Verificar", f"{av}\n{fw}",
                                               COLOR_OK if av_ok and sec.firewall_enabled else COLOR_WARN)
        upd = a.updates
        self.cards["Windows Update"].set(upd.service_state,
                                         f"Início: {upd.service_start}" +
                                         ("\nReinício pendente para concluir atualizações" if upd.reboot_pending
                                          else ""), COLOR_WARN if upd.reboot_pending else COLOR_TEXT)
        protected = sum(1 for i in a.enabled_startup if i.protected)
        self.cards["Inicialização"].set(f"{len(a.enabled_startup)} ativos",
                                        f"{len(a.startup_items)} itens no total, {protected} protegido(s)")
        self.cards["Sistema"].set(s.computer_name or "—", s.windows_version)
        self.cards["Lixeira"].set(format_size(a.recycle_bin[1]), f"{a.recycle_bin[0]} item(ns)")

        self._render_recommendations(build_recommendations(a))
        self._render_drives(s.drives)
        self.cpu_table.set_rows([Row(str(p.pid), [p.name, p.pid, f"{p.cpu_percent:.1f}%"],
                                     sort=[p.name, p.pid, p.cpu_percent]) for p in s.top_cpu])
        self.ram_table.set_rows([Row(str(p.pid), [p.name, p.pid, format_size(p.memory_bytes)],
                                     sort=[p.name, p.pid, p.memory_bytes]) for p in s.top_ram])

    def _render_recommendations(self, recs) -> None:
        for w in self.recs_frame.winfo_children():
            w.destroy()
        if not recs:
            ctk.CTkLabel(self.recs_frame, text="Nenhuma recomendação — o sistema está em bom estado.",
                         anchor="w").grid(row=0, column=0, sticky="ew")
            return
        for i, rec in enumerate(recs):
            card = ctk.CTkFrame(self.recs_frame, corner_radius=8)
            card.grid(row=i, column=0, sticky="ew", pady=3)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=f" {rec.priority.upper()} ", font=font(11, "bold"), corner_radius=6,
                         fg_color=PRIORITY_COLORS.get(rec.priority, COLOR_INFO), text_color="white").grid(
                row=0, column=0, rowspan=2, padx=12, pady=10)
            ctk.CTkLabel(card, text=rec.title, font=font(13, "bold"), anchor="w").grid(
                row=0, column=1, sticky="ew", pady=(8, 0))
            ctk.CTkLabel(card, text=rec.detail, font=font(12), anchor="w", justify="left", wraplength=780,
                         text_color=COLOR_MUTED).grid(row=1, column=1, sticky="ew", pady=(0, 8))
            if rec.page:
                ctk.CTkButton(card, text=f"Abrir {rec.page}", width=150,
                              command=lambda p=rec.page: self.app.show_page(p)).grid(
                    row=0, column=2, rowspan=2, padx=12)

    def _render_drives(self, drives) -> None:
        for w in self.drives_frame.winfo_children():
            w.destroy()
        for i, d in enumerate(drives):
            ctk.CTkLabel(self.drives_frame, text=f"{d['mountpoint']}  ({d['fstype']})", width=110,
                         anchor="w", font=font(12, "bold")).grid(row=i, column=0, sticky="w", pady=3)
            bar = ctk.CTkProgressBar(self.drives_frame, height=14)
            bar.set(d["percent"] / 100)
            if d["percent"] >= 90:
                bar.configure(progress_color=COLOR_DANGER)
            elif d["percent"] >= 80:
                bar.configure(progress_color=COLOR_WARN)
            bar.grid(row=i, column=1, sticky="ew", padx=10)
            ctk.CTkLabel(self.drives_frame, text=f"{format_size(d['free'])} livres de {format_size(d['total'])} "
                                                 f"({100 - d['percent']:.0f}% livre)", anchor="w",
                         font=font(12)).grid(row=i, column=2, sticky="w")

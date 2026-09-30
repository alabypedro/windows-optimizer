"""Componentes reutilizáveis da interface.

- TaskRunner: executa funções em threads e entrega resultados na thread da
  GUI (Tkinter não é thread-safe). Só faz polling enquanto há tarefas ativas,
  então o programa ocioso não consome CPU.
- DataTable: tabela (ttk.Treeview) com ordenação por coluna e caixas de seleção.
- ConfirmDialog / show_info / show_error: diálogos modais.
- OutputConsole: caixa de texto somente leitura para saída de comandos.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Any, Callable, Optional, Sequence

import customtkinter as ctk

from utils.logger import get_logger

log = get_logger("gui")

COLOR_OK = "#2e9e5b"
COLOR_WARN = "#d08a1e"
COLOR_DANGER = "#c2413b"
COLOR_INFO = "#3b82c4"
COLOR_MUTED = ("gray40", "gray65")
COLOR_TEXT = ("gray10", "gray90")  # cor padrão (CTk 6 não aceita text_color=None)
DANGER_BUTTON = {"fg_color": "#b83b36", "hover_color": "#962f2b"}
SECONDARY_BUTTON = {"fg_color": ("gray70", "gray30"), "hover_color": ("gray60", "gray25"),
                    "text_color": ("gray10", "gray95")}


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


# ---------------------------------------------------------------------------
# Execução em segundo plano
# ---------------------------------------------------------------------------

ProgressFn = Callable[[str], None]


class TaskRunner:
    """Executa ``fn(progress)`` em uma thread e chama callbacks na thread da GUI."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self._queue: queue.Queue = queue.Queue()
        self._active = 0
        self._polling = False

    @property
    def busy(self) -> bool:
        return self._active > 0

    def submit(self, fn: Callable[[ProgressFn], Any],
               on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[BaseException], None]] = None,
               on_progress: Optional[Callable[[str], None]] = None) -> None:
        self._active += 1

        def progress(msg: str) -> None:
            self._queue.put(("progress", on_progress, msg))

        def worker() -> None:
            try:
                result = fn(progress)
                self._queue.put(("done", on_done, result))
            except BaseException as exc:  # nunca derruba a aplicação
                log.exception(f"ERRO em tarefa de segundo plano: {exc}")
                self._queue.put(("error", on_error or _default_error, exc))
            finally:
                self._queue.put(("finish", None, None))

        threading.Thread(target=worker, daemon=True).start()
        if not self._polling:
            self._polling = True
            self.root.after(60, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, callback, payload = self._queue.get_nowait()
                if kind == "finish":
                    self._active -= 1
                    continue
                if callback is not None:
                    try:
                        callback(payload)
                    except Exception as exc:
                        log.exception(f"ERRO em callback da interface: {exc}")
        except queue.Empty:
            pass
        if self._active > 0 or not self._queue.empty():
            self.root.after(80, self._poll)
        else:
            self._polling = False


def _default_error(exc: BaseException) -> None:
    show_error("Erro", f"Ocorreu um erro inesperado:\n{exc}")


# ---------------------------------------------------------------------------
# Tabela
# ---------------------------------------------------------------------------

@dataclass
class Column:
    key: str
    title: str
    width: int = 120
    anchor: str = "w"
    stretch: bool = False


@dataclass
class Row:
    id: str
    values: Sequence[Any]
    sort: Optional[Sequence[Any]] = None  # valores brutos para ordenação
    tags: tuple = ()
    checked: bool = False
    checkable: bool = True
    data: Any = None


_UNCHECKED, _CHECKED, _LOCKED = "☐", "☑", "🔒"


def style_treeview(root: tk.Misc) -> None:
    """Aplica cores coerentes com o modo claro/escuro do CustomTkinter."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    dark = ctk.get_appearance_mode() == "Dark"
    bg = "#242424" if dark else "#ffffff"
    alt = "#2c2c2c" if dark else "#f3f5f7"
    fg = "#e6e6e6" if dark else "#1c1c1c"
    head = "#34373c" if dark else "#e2e5e9"
    style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg, rowheight=26,
                    borderwidth=0, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", background=head, foreground=fg, relief="flat",
                    font=("Segoe UI Semibold", 10), padding=(6, 4))
    style.map("Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", "#44484f" if dark else "#d2d6db")])
    root._tree_alt_bg = alt  # usado nas linhas alternadas
    root._tree_colors = {"muted": "#8a8a8a" if dark else "#7a7a7a", "warn": "#e0a040" if dark else "#a86200",
                         "danger": "#ef6b64" if dark else "#b3261e", "ok": "#5cc98a" if dark else "#1c7c43"}


class DataTable(ctk.CTkFrame):
    """Treeview com rolagem, ordenação por coluna e (opcional) caixas de seleção."""

    def __init__(self, master, columns: list[Column], checkable: bool = False, height: int = 10,
                 on_select: Optional[Callable[[list[str]], None]] = None,
                 on_double: Optional[Callable[[str], None]] = None,
                 on_check: Optional[Callable[[], None]] = None,
                 selectmode: str = "browse"):
        super().__init__(master, fg_color="transparent")
        self.columns = columns
        self.checkable = checkable
        self.on_select = on_select
        self.on_double = on_double
        self.on_check = on_check
        self._rows: dict[str, Row] = {}
        self._sort_state: tuple[int, bool] = (-1, False)

        show = "tree headings" if checkable else "headings"
        self.tree = ttk.Treeview(self, columns=[c.key for c in columns], show=show,
                                 height=height, selectmode=selectmode)
        if checkable:
            self.tree.heading("#0", text=_UNCHECKED, command=self._toggle_all)
            self.tree.column("#0", width=42, minwidth=42, stretch=False, anchor="center")
        for i, col in enumerate(columns):
            self.tree.heading(col.key, text=col.title, command=lambda i=i: self.sort_by(i))
            self.tree.column(col.key, width=col.width, minwidth=40, anchor=col.anchor, stretch=col.stretch)
        yscroll = ctk.CTkScrollbar(self, orientation="vertical", command=self.tree.yview)
        xscroll = ctk.CTkScrollbar(self, orientation="horizontal", command=self.tree.xview, height=14)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        colors = getattr(self.winfo_toplevel(), "_tree_colors", {})
        self.tree.tag_configure("odd", background=getattr(self.winfo_toplevel(), "_tree_alt_bg", ""))
        for tag, color in colors.items():
            self.tree.tag_configure(tag, foreground=color)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<space>", self._on_space)

    # -- dados ------------------------------------------------------------
    def set_rows(self, rows: list[Row]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows = {r.id: r for r in rows}
        for i, row in enumerate(rows):
            self._insert(row, i)
        if self._sort_state[0] >= 0:
            col, desc = self._sort_state
            self._sort_state = (col, not desc)
            self.sort_by(col)

    def _insert(self, row: Row, index: int) -> None:
        text = ""
        if self.checkable:
            text = _LOCKED if not row.checkable else (_CHECKED if row.checked else _UNCHECKED)
        tags = tuple(row.tags) + (("odd",) if index % 2 else ())
        self.tree.insert("", "end", iid=row.id, text=text,
                         values=[("" if v is None else v) for v in row.values], tags=tags)

    def row(self, row_id: str) -> Optional[Row]:
        return self._rows.get(row_id)

    def rows(self) -> list[Row]:
        return list(self._rows.values())

    def selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def selected_rows(self) -> list[Row]:
        return [self._rows[i] for i in self.tree.selection() if i in self._rows]

    def checked_rows(self) -> list[Row]:
        return [r for r in self._rows.values() if r.checked and r.checkable]

    def set_checked(self, row_id: str, checked: bool) -> None:
        row = self._rows.get(row_id)
        if row is None or not row.checkable:
            return
        row.checked = checked
        self.tree.item(row_id, text=_CHECKED if checked else _UNCHECKED)

    def check_all(self, checked: bool) -> None:
        for row_id in self._rows:
            self.set_checked(row_id, checked)
        if self.on_check:
            self.on_check()

    def _toggle_all(self) -> None:
        rows = [r for r in self._rows.values() if r.checkable]
        self.check_all(not all(r.checked for r in rows) if rows else False)

    # -- ordenação --------------------------------------------------------
    def sort_by(self, col_index: int) -> None:
        prev_col, prev_desc = self._sort_state
        desc = not prev_desc if prev_col == col_index else False

        def key(row_id: str):
            row = self._rows[row_id]
            source = row.sort if row.sort is not None else row.values
            value = source[col_index] if col_index < len(source) else ""
            if isinstance(value, (int, float)):
                return (0, value, "")
            return (1, 0, str(value).lower())

        ids = sorted(self.tree.get_children(), key=key, reverse=desc)
        for i, row_id in enumerate(ids):
            self.tree.move(row_id, "", i)
            tags = tuple(t for t in self.tree.item(row_id, "tags") if t != "odd")
            self.tree.item(row_id, tags=tags + (("odd",) if i % 2 else ()))
        self._sort_state = (col_index, desc)
        for i, col in enumerate(self.columns):
            arrow = (" ▼" if desc else " ▲") if i == col_index else ""
            self.tree.heading(col.key, text=col.title + arrow)

    # -- eventos ----------------------------------------------------------
    def _on_click(self, event) -> Optional[str]:
        if not self.checkable:
            return None
        if self.tree.identify_region(event.x, event.y) == "tree" or \
                self.tree.identify_column(event.x) == "#0":
            row_id = self.tree.identify_row(event.y)
            if row_id:
                row = self._rows.get(row_id)
                if row and row.checkable:
                    self.set_checked(row_id, not row.checked)
                    if self.on_check:
                        self.on_check()
        return None

    def _on_space(self, _event) -> str:
        if self.checkable:
            for row_id in self.tree.selection():
                row = self._rows.get(row_id)
                if row:
                    self.set_checked(row_id, not row.checked)
            if self.on_check:
                self.on_check()
        return "break"

    def _on_select(self, _event) -> None:
        if self.on_select:
            self.on_select(self.selected_ids())

    def _on_double(self, event) -> None:
        row_id = self.tree.identify_row(event.y)
        if row_id and self.on_double:
            self.on_double(row_id)


# ---------------------------------------------------------------------------
# Diálogos
# ---------------------------------------------------------------------------

class _Modal(ctk.CTkToplevel):
    def __init__(self, title: str, width: int = 620, height: int = 420):
        root = tk._default_root
        super().__init__(root)
        self.title(title)
        self.geometry(f"{width}x{height}")
        self.minsize(420, 260)
        if root is not None:
            self.transient(root)
            root.update_idletasks()
            x = root.winfo_rootx() + max(0, (root.winfo_width() - width) // 2)
            y = root.winfo_rooty() + max(0, (root.winfo_height() - height) // 3)
            self.geometry(f"+{x}+{y}")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.result = False

    def _cancel(self) -> None:
        self.result = False
        self.destroy()

    def _ok(self) -> None:
        self.result = True
        self.destroy()

    def run(self) -> bool:
        self.after(50, self._grab)
        self.wait_window()
        return self.result

    def _grab(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass


class ConfirmDialog(_Modal):
    """Mostra EXATAMENTE o que será feito e pede confirmação explícita."""

    def __init__(self, title: str, message: str, details: Sequence[str] = (),
                 confirm_text: str = "Confirmar", danger: bool = False,
                 warning: str = "", width: int = 680, height: int = 460):
        super().__init__(title, width, height)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=message, font=font(14, "bold"), wraplength=width - 40,
                     justify="left", anchor="w").grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        row = 1
        if details:
            box = ctk.CTkTextbox(self, font=font(12), wrap="word")
            box.insert("1.0", "\n".join(f"• {d}" for d in details))
            box.configure(state="disabled")
            box.grid(row=row, column=0, sticky="nsew", padx=20, pady=6)
            self.grid_rowconfigure(row, weight=1)
            row += 1
        if warning:
            ctk.CTkLabel(self, text=warning, text_color=COLOR_WARN, wraplength=width - 40, justify="left",
                         anchor="w", font=font(12)).grid(row=row, column=0, sticky="ew", padx=20, pady=4)
            row += 1
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="e", padx=20, pady=(8, 16))
        cancel = ctk.CTkButton(bar, text="Cancelar", command=self._cancel, width=120, **SECONDARY_BUTTON)
        cancel.pack(side="right", padx=(8, 0))
        style = DANGER_BUTTON if danger else {}
        ctk.CTkButton(bar, text=confirm_text, command=self._ok, width=160, **style).pack(side="right")
        cancel.focus_set()  # foco padrão em Cancelar (evita confirmar com Enter por engano)


def confirm(title: str, message: str, details: Sequence[str] = (), confirm_text: str = "Confirmar",
            danger: bool = False, warning: str = "") -> bool:
    return ConfirmDialog(title, message, details, confirm_text, danger, warning).run()


class TextDialog(_Modal):
    def __init__(self, title: str, text: str, width: int = 760, height: int = 520,
                 extra_button: Optional[tuple[str, Callable[[], None]]] = None):
        super().__init__(title, width, height)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12), wrap="word")
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.grid(row=0, column=0, sticky="nsew", padx=16, pady=(16, 8))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="e", padx=16, pady=(0, 14))
        ctk.CTkButton(bar, text="Fechar", command=self._ok, width=110).pack(side="right")
        if extra_button:
            ctk.CTkButton(bar, text=extra_button[0], command=extra_button[1], width=160,
                          **SECONDARY_BUTTON).pack(side="right", padx=8)


def show_text(title: str, text: str, extra_button=None) -> None:
    TextDialog(title, text, extra_button=extra_button).run()


class _MessageDialog(_Modal):
    def __init__(self, title: str, message: str, color=None):
        lines = message.count("\n") + len(message) // 70
        super().__init__(title, 520, min(520, 170 + lines * 20))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(self, text=message, wraplength=470, justify="left", anchor="nw", font=font(13),
                     text_color=color).grid(row=0, column=0, sticky="nsew", padx=22, pady=(20, 8))
        ok = ctk.CTkButton(self, text="OK", width=110, command=self._ok)
        ok.grid(row=1, column=0, sticky="e", padx=22, pady=(0, 16))
        ok.focus_set()
        self.bind("<Return>", lambda _e: self._ok())


def show_info(title: str, message: str) -> None:
    _MessageDialog(title, message).run()


def show_error(title: str, message: str) -> None:
    _MessageDialog(title, message, color=("#b3261e", "#ef6b64")).run()


# ---------------------------------------------------------------------------
# Outros componentes
# ---------------------------------------------------------------------------

class OutputConsole(ctk.CTkTextbox):
    """Saída de comandos (somente leitura)."""

    def __init__(self, master, height: int = 180, **kw):
        super().__init__(master, height=height, font=ctk.CTkFont(family="Consolas", size=12),
                         wrap="word", **kw)
        self.configure(state="disabled")

    def append(self, text: str) -> None:
        self.configure(state="normal")
        self.insert("end", text.rstrip("\n") + "\n")
        # Limita o tamanho para não crescer indefinidamente.
        if int(self.index("end-1c").split(".")[0]) > 3000:
            self.delete("1.0", "500.0")
        self.see("end")
        self.configure(state="disabled")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class StatCard(ctk.CTkFrame):
    def __init__(self, master, title: str, value: str = "—", subtitle: str = ""):
        super().__init__(master, corner_radius=10)
        self.title_label = ctk.CTkLabel(self, text=title, font=font(12), text_color=COLOR_MUTED, anchor="w")
        self.title_label.pack(fill="x", padx=14, pady=(10, 0))
        self.value_label = ctk.CTkLabel(self, text=value, font=font(20, "bold"), anchor="w")
        self.value_label.pack(fill="x", padx=14)
        self.sub_label = ctk.CTkLabel(self, text=subtitle, font=font(11), text_color=COLOR_MUTED,
                                      anchor="w", justify="left", wraplength=220)
        self.sub_label.pack(fill="x", padx=14, pady=(0, 10))

    def set(self, value: str, subtitle: str = "", color=None) -> None:
        self.value_label.configure(text=value, text_color=color or COLOR_TEXT)
        self.sub_label.configure(text=subtitle)


def section_title(master, text: str, subtitle: str = "") -> ctk.CTkFrame:
    frame = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(frame, text=text, font=font(20, "bold"), anchor="w").pack(fill="x")
    if subtitle:
        ctk.CTkLabel(frame, text=subtitle, font=font(12), text_color=COLOR_MUTED, anchor="w",
                     justify="left", wraplength=900).pack(fill="x")
    return frame


def button_bar(master) -> ctk.CTkFrame:
    return ctk.CTkFrame(master, fg_color="transparent")

"""Windows Optimizer — ponto de entrada.

Uso:
    python main.py            abre a interface gráfica
    python main.py --report   executa a análise (somente leitura) e imprime o relatório no console
    python main.py --clean    limpa temporários pelo console (analisa, mostra e pede confirmação)
        --dry-run             só analisa, não apaga nada
        --yes                 não pede confirmação (execução agendada); também esvazia a Lixeira
        --all                 inclui as categorias opcionais (não só as recomendadas)
        --no-recycle-bin      não mexe na Lixeira
        --quiet               saída mínima (o log continua completo)
    python main.py --schedule mostra como agendar a limpeza no Agendador de Tarefas

O programa roda sem privilégios elevados; operações que exigem
Administrador oferecem reiniciar o programa pelo UAC.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permite "python main.py" de qualquer pasta (imports absolutos core/, gui/, utils/).
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")  # console com code page antiga não derruba o programa


def _check_platform() -> bool:
    if sys.platform != "win32":
        print("Este programa foi projetado para Windows 10/11.")
        return False
    if sys.getwindowsversion().major < 10:  # type: ignore[attr-defined]
        print("Requer Windows 10 ou 11.")
        return False
    return True


def run_report() -> int:
    from core.recommendations import run_full_analysis
    from core.report import build_report, save_report

    analysis = run_full_analysis(progress=lambda msg: print(f"  {msg}"))
    text = build_report(analysis)
    print(text)
    print(f"\nRelatório salvo em: {save_report(text)}")
    return 0


def run_gui() -> int:
    import customtkinter as ctk

    from utils.logger import get_logger

    log = get_logger("main")
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    try:
        # Nitidez em telas com escala (DPI); falha silenciosa em versões antigas.
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    from gui.app import App

    try:
        app = App()
        app.mainloop()
    except Exception as exc:
        log.exception(f"ERRO fatal na interface: {exc}")
        raise
    return 0


def main(argv: list[str]) -> int:
    if not _check_platform():
        return 1
    if "--report" in argv:
        return run_report()
    if "--schedule" in argv:
        from core.cli_cleanup import schedule_help
        print(schedule_help())
        return 0
    if "--clean" in argv or "--dry-run" in argv:
        from core.cli_cleanup import run_clean
        return run_clean(dry_run="--dry-run" in argv, yes="--yes" in argv or "-y" in argv,
                         include_optional="--all" in argv, recycle_bin="--no-recycle-bin" not in argv,
                         quiet="--quiet" in argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

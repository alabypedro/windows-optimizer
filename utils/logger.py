"""Sistema de log: logs/optimization_YYYY-MM-DD.log (UTF-8, um arquivo por dia).

Uso:
    from utils.logger import get_logger
    log = get_logger("temp_cleaner")
    log.info("Início da operação: limpeza de temporários")

Convenções de mensagem (facilitam auditoria do arquivo):
    INÍCIO / FIM          -> início e resultado final de uma operação
    REMOVIDO / IGNORADO   -> arquivos processados
    ALTERAÇÃO             -> mudança de configuração (com estado anterior)
    ERRO                  -> falhas (nível ERROR/WARNING)
"""

from __future__ import annotations

import logging
import threading
from datetime import date

from utils.helpers import logs_dir

_ROOT_NAME = "optimizer"
_lock = threading.Lock()
_configured_for: date | None = None


def _configure() -> None:
    """(Re)configura o handler de arquivo para o dia atual."""
    global _configured_for
    today = date.today()
    with _lock:
        if _configured_for == today:
            return
        root = logging.getLogger(_ROOT_NAME)
        root.setLevel(logging.DEBUG)
        root.propagate = False
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        path = logs_dir() / f"optimization_{today:%Y-%m-%d}.log"
        handler = logging.FileHandler(path, encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        root.addHandler(handler)
        _configured_for = today


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


def current_log_file():
    _configure()
    return logs_dir() / f"optimization_{date.today():%Y-%m-%d}.log"


def shutdown() -> None:
    """Fecha os arquivos de log (chamado ao sair)."""
    global _configured_for
    with _lock:
        root = logging.getLogger(_ROOT_NAME)
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        _configured_for = None

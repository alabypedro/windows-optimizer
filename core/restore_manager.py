"""Histórico de ações, desfazer (undo), backups e pontos de restauração.

Toda alteração de configuração feita pelo programa é registrada em
``backups/history.json`` com data/hora, descrição, estado anterior e novo
estado. Ações reversíveis têm um ``undo_type`` com um manipulador registrado
por outro módulo (inicialização, serviços, plano de energia, TRIM).

Exclusão de arquivos temporários não é reversível (é informado ao usuário
antes da confirmação). Arquivos do usuário (grandes/duplicados) vão para a
Lixeira, de onde podem ser restaurados pelo próprio Windows.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from utils.admin import is_admin
from utils.helpers import backups_dir, run_command, run_powershell
from utils.logger import get_logger

log = get_logger("restore")

_lock = threading.Lock()
_undo_handlers: dict[str, Callable[[dict], tuple[bool, str]]] = {}


@dataclass
class HistoryEntry:
    action_type: str                      # ex.: "startup_toggle", "file_cleanup"
    description: str                      # texto legível
    previous_state: Any = None
    new_state: Any = None
    undoable: bool = False
    undo_type: str = ""                   # chave do manipulador de desfazer
    undo_data: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    undone: bool = False
    undone_at: str = ""
    result: str = ""


def history_file() -> Path:
    return backups_dir() / "history.json"


def load_history() -> list[HistoryEntry]:
    path = history_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error(f"ERRO ao ler histórico ({exc}); criando cópia de segurança.")
        try:
            path.replace(path.with_suffix(f".corrupt_{datetime.now():%Y%m%d%H%M%S}.json"))
        except OSError:
            pass
        return []
    entries = []
    for item in raw if isinstance(raw, list) else []:
        try:
            entries.append(HistoryEntry(**item))
        except TypeError:
            continue
    return entries


def _save_history(entries: list[HistoryEntry]) -> None:
    path = history_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)  # gravação atômica


def record_action(entry: HistoryEntry) -> HistoryEntry:
    with _lock:
        entries = load_history()
        entries.append(entry)
        _save_history(entries)
    log.info(f"ALTERAÇÃO registrada [{entry.action_type}] {entry.description} | "
             f"anterior={entry.previous_state!r} novo={entry.new_state!r}")
    return entry


def register_undo_handler(undo_type: str, handler: Callable[[dict], tuple[bool, str]]) -> None:
    _undo_handlers[undo_type] = handler


def _ensure_handlers() -> None:
    """Os módulos registram seus manipuladores ao serem importados."""
    from core import service_manager, startup_manager, storage_analyzer, windows_tools  # noqa: F401


def undo_action(entry_id: str) -> tuple[bool, str]:
    """Desfaz uma ação registrada (quando tecnicamente possível)."""
    with _lock:
        entries = load_history()
        entry = next((e for e in entries if e.id == entry_id), None)
    if entry is None:
        return False, "ação não encontrada no histórico"
    if not entry.undoable:
        return False, "esta ação não pode ser desfeita"
    if entry.undone:
        return False, "esta ação já foi desfeita"
    _ensure_handlers()
    handler = _undo_handlers.get(entry.undo_type)
    if handler is None:
        return False, f"sem manipulador para '{entry.undo_type}'"
    log.info(f"INÍCIO desfazer: {entry.description}")
    try:
        ok, msg = handler(entry.undo_data)
    except Exception as exc:  # nunca derruba a aplicação
        ok, msg = False, f"erro inesperado: {exc}"
    if ok:
        with _lock:
            entries = load_history()
            for e in entries:
                if e.id == entry_id:
                    e.undone = True
                    e.undone_at = datetime.now().isoformat(timespec="seconds")
            _save_history(entries)
        log.info(f"FIM desfazer OK: {entry.description} ({msg})")
    else:
        log.error(f"ERRO ao desfazer '{entry.description}': {msg}")
    return ok, msg


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

def export_registry_key(key: str, label: str) -> Optional[Path]:
    """Exporta uma chave do Registro (reg export) antes de alterá-la.

    Somente leitura do Registro; cria um .reg em backups/ que pode ser
    reimportado manualmente se necessário.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backups_dir() / f"reg_{label}_{stamp}.reg"
    result = run_command(["reg.exe", "export", key, str(target), "/y"], timeout=30)
    if result.ok and target.exists():
        log.info(f"Backup do Registro criado: {key} -> {target}")
        return target
    # Chave inexistente não é erro (nada a salvar).
    log.warning(f"Backup do Registro não criado para {key}: {result.summary()}")
    return None


def create_restore_point(description: str = "Windows Optimizer") -> tuple[bool, str]:
    """Cria um ponto de restauração do sistema (requer Administrador).

    O Windows limita a criação a um ponto a cada 24h por padrão; nesse caso
    o comando conclui sem criar um novo ponto. O programa NÃO altera essa
    política no Registro.
    """
    if not is_admin():
        return False, "requer Administrador"
    safe_desc = description.replace("'", "")[:60]
    before = _latest_restore_point_seq()
    result = run_powershell(
        f"Checkpoint-Computer -Description '{safe_desc}' -RestorePointType MODIFY_SETTINGS "
        "-ErrorAction Stop", timeout=300)
    if result.timed_out:
        log.error("ERRO: criação do ponto de restauração excedeu o tempo limite")
        return False, "tempo limite excedido"
    if not result.ok:
        text = (result.stderr or result.stdout).strip()
        if "desativad" in text.lower() or "disabled" in text.lower():
            msg = "Proteção do Sistema está desativada nesta unidade"
        else:
            msg = text.splitlines()[0] if text else result.summary()
        log.error(f"ERRO ao criar ponto de restauração: {msg}")
        return False, msg
    after = _latest_restore_point_seq()
    if after is not None and after == before:
        msg = ("o Windows não criou um novo ponto (limite de 1 ponto a cada 24h); "
               "o ponto mais recente continua válido")
        log.warning(msg)
        return True, msg
    log.info(f"Ponto de restauração criado: {safe_desc}")
    return True, "ponto de restauração criado"


def _latest_restore_point_seq() -> Optional[int]:
    result = run_powershell(
        "(Get-ComputerRestorePoint -ErrorAction SilentlyContinue | "
        "Sort-Object SequenceNumber | Select-Object -Last 1).SequenceNumber", timeout=60)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None

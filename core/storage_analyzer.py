"""Armazenamento: unidades, TRIM, otimização, pastas grandes, arquivos grandes
e duplicados.

Nada aqui apaga arquivos automaticamente. Arquivos escolhidos pelo usuário
são enviados para a LIXEIRA (recuperáveis), via core.security.
Otimização de unidade usa o defrag nativo com a operação correta:
  * SSD -> somente ReTrim (/L). Desfragmentação tradicional NUNCA é usada.
  * HDD -> otimização padrão (/O), que desfragmenta.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from core import restore_manager
from core.security import is_reparse_point, send_to_recycle_bin, system_protected_roots
from core.system_info import drive_media_types, list_drives
from utils.admin import is_admin
from utils.helpers import format_size, run_command, stream_command
from utils.logger import get_logger

log = get_logger("storage")

ProgressFn = Optional[Callable[[str], None]]


# ---------------------------------------------------------------------------
# Unidades e TRIM
# ---------------------------------------------------------------------------

@dataclass
class DriveInfo:
    letter: str
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float
    media: str
    model: str
    removable: bool


def drives_with_media() -> list[DriveInfo]:
    media = drive_media_types()
    result = []
    for d in list_drives():
        letter = d["letter"].upper()
        result.append(DriveInfo(letter, d["mountpoint"], d["fstype"], d["total"], d["used"],
                                d["free"], d["percent"], media.get(letter, "Desconhecido"),
                                media.get(letter + ":model", ""), d["removable"]))
    return result


def trim_status() -> dict[str, Optional[bool]]:
    """{'NTFS': True/False/None, 'ReFS': ...}. True = TRIM ativado."""
    result = run_command(["fsutil.exe", "behavior", "query", "DisableDeleteNotify"], timeout=15)
    status: dict[str, Optional[bool]] = {"NTFS": None, "ReFS": None}
    for line in result.stdout.splitlines():
        for fs in status:
            if line.strip().upper().startswith(fs.upper()) and "=" in line:
                value = line.split("=", 1)[1].strip()[:1]
                if value in ("0", "1"):
                    status[fs] = value == "0"
    return status


def enable_trim() -> tuple[bool, str]:
    """Ativa TRIM para NTFS (padrão do Windows). Requer Admin; reversível."""
    if not is_admin():
        return False, "requer Administrador"
    before = trim_status().get("NTFS")
    result = run_command(["fsutil.exe", "behavior", "set", "DisableDeleteNotify", "NTFS", "0"], timeout=15)
    if not result.ok:
        return False, result.summary()
    restore_manager.record_action(restore_manager.HistoryEntry(
        action_type="trim", description="TRIM ativado para NTFS (DisableDeleteNotify=0)",
        previous_state="ativado" if before else "desativado", new_state="ativado",
        undoable=before is False, undo_type="trim", undo_data={"disable": True},
    ))
    return True, "TRIM ativado"


def _undo_trim(data: dict) -> tuple[bool, str]:
    if not is_admin():
        return False, "requer Administrador"
    value = "1" if data.get("disable") else "0"
    result = run_command(["fsutil.exe", "behavior", "set", "DisableDeleteNotify", "NTFS", value], timeout=15)
    return result.ok, "configuração de TRIM restaurada" if result.ok else result.summary()


restore_manager.register_undo_handler("trim", _undo_trim)


def optimize_drive(drive: DriveInfo, on_line: Callable[[str], None],
                   analyze_only: bool = False) -> int:
    """Executa o otimizador nativo com a operação adequada ao tipo de mídia."""
    if not is_admin():
        on_line("Esta operação requer Administrador.")
        return -1
    letter = f"{drive.letter}:"
    if analyze_only:
        args = ["defrag.exe", letter, "/A", "/U", "/V"]
    elif drive.media == "SSD":
        args = ["defrag.exe", letter, "/L", "/U", "/V"]  # ReTrim apenas
    elif drive.media == "HDD":
        args = ["defrag.exe", letter, "/O", "/U", "/V"]
    else:
        on_line("Tipo de mídia desconhecido: por segurança, apenas a análise é executada.")
        args = ["defrag.exe", letter, "/A", "/U", "/V"]
    log.info(f"INÍCIO otimização de unidade: {' '.join(args)} (mídia {drive.media})")
    code = stream_command(args, on_line)
    log.info(f"FIM otimização de unidade {letter}: código {code}")
    return code


# ---------------------------------------------------------------------------
# Pastas que mais ocupam espaço
# ---------------------------------------------------------------------------

def _dir_size(path: Path, cancel: Optional[threading.Event]) -> tuple[int, int]:
    """(bytes, arquivos) de uma pasta, sem seguir links e ignorando erros."""
    total = count = 0
    stack = [path]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not is_reparse_point(Path(entry.path)):
                                stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def top_directories(root: Path, cancel: Optional[threading.Event] = None,
                    progress: ProgressFn = None) -> list[tuple[Path, int, int]]:
    """Tamanho de cada subpasta de primeiro nível de ``root`` (maior primeiro)."""
    results = []
    loose_bytes = loose_files = 0
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        log.warning(f"Sem acesso a {root}: {exc}")
        return []
    for entry in entries:
        if cancel is not None and cancel.is_set():
            break
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if progress:
                    progress(f"Analisando {entry.path}")
                size, files = _dir_size(Path(entry.path), cancel)
                results.append((Path(entry.path), size, files))
            else:
                loose_bytes += entry.stat(follow_symlinks=False).st_size
                loose_files += 1
        except OSError:
            continue
    if loose_files:
        results.append((root / "(arquivos soltos nesta pasta)", loose_bytes, loose_files))
    return sorted(results, key=lambda r: r[1], reverse=True)


# ---------------------------------------------------------------------------
# Arquivos grandes / antigos / tipos específicos
# ---------------------------------------------------------------------------

KIND_ISO = {".iso", ".img", ".vhd", ".vhdx"}
KIND_INSTALLER = {".exe", ".msi", ".msix", ".appx"}
KIND_ARCHIVE = {".zip", ".rar", ".7z", ".tar", ".gz"}
KIND_DUMP = {".dmp", ".hdmp", ".mdmp"}
KIND_TEMP = {".tmp", ".temp", ".bak", ".old", ".log", ".etl"}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in KIND_ISO:
        return "Imagem de disco (ISO/VHD)"
    if ext in KIND_INSTALLER and "download" in str(path.parent).lower():
        return "Instalador baixado"
    if ext in KIND_INSTALLER:
        return "Executável/instalador"
    if ext in KIND_DUMP:
        return "Dump de memória"
    if ext in KIND_TEMP:
        return "Temporário/log/backup"
    if ext in KIND_ARCHIVE:
        return "Arquivo compactado"
    return "Outro"


@dataclass
class FoundFile:
    path: Path
    size: int
    modified: float
    kind: str

    @property
    def age_days(self) -> int:
        return int((time.time() - self.modified) / 86400)


def _walk_files(root: Path, cancel: Optional[threading.Event], skip_system: bool = True) -> Iterable[os.DirEntry]:
    protected = [str(p).lower() for p in system_protected_roots()] if skip_system else []
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        current = stack.pop()
        lower = str(current).lower()
        if any(lower == p or lower.startswith(p + "\\") for p in protected):
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not is_reparse_point(Path(entry.path)):
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def find_large_files(root: Path, min_size_mb: int = 500, older_than_days: int = 0,
                     kinds: Optional[set[str]] = None, cancel: Optional[threading.Event] = None,
                     progress: ProgressFn = None, limit: int = 2000) -> list[FoundFile]:
    """Encontra arquivos grandes e/ou antigos. Diretórios de sistema são ignorados.

    ``kinds``: se informado, inclui também arquivos menores desses tipos
    (ex.: ISOs, instaladores, dumps) acima de 10 MB.
    """
    min_size = min_size_mb * 1024 ** 2
    now = time.time()
    found: list[FoundFile] = []
    scanned = 0
    for entry in _walk_files(root, cancel):
        scanned += 1
        if progress and scanned % 5000 == 0:
            progress(f"{scanned} arquivos verificados…")
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        path = Path(entry.path)
        kind = classify(path)
        big = st.st_size >= min_size
        typed = kinds is not None and kind in kinds and st.st_size >= 10 * 1024 ** 2
        if not (big or typed):
            continue
        if older_than_days and (now - st.st_mtime) < older_than_days * 86400:
            continue
        found.append(FoundFile(path, st.st_size, st.st_mtime, kind))
    found.sort(key=lambda f: f.size, reverse=True)
    return found[:limit]


# ---------------------------------------------------------------------------
# Duplicados (por hash)
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    digest: str
    size: int
    files: list[Path] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        return self.size * (len(self.files) - 1)


def _hash_file(path: Path, partial: bool, cancel: Optional[threading.Event]) -> Optional[str]:
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(path, "rb") as fh:
            if partial:
                h.update(fh.read(64 * 1024))
            else:
                while True:
                    if cancel is not None and cancel.is_set():
                        return None
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(root: Path, min_size_kb: int = 1024, cancel: Optional[threading.Event] = None,
                    progress: ProgressFn = None) -> list[DuplicateGroup]:
    """Encontra duplicados: agrupa por tamanho -> hash parcial -> hash completo."""
    min_size = min_size_kb * 1024
    by_size: dict[int, list[Path]] = {}
    count = 0
    for entry in _walk_files(root, cancel):
        count += 1
        if progress and count % 5000 == 0:
            progress(f"Listando arquivos: {count}")
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if size >= min_size:
            by_size.setdefault(size, []).append(Path(entry.path))

    candidates = {s: ps for s, ps in by_size.items() if len(ps) > 1}
    groups: list[DuplicateGroup] = []
    total = sum(len(v) for v in candidates.values())
    done = 0
    for size, paths in candidates.items():
        partial: dict[str, list[Path]] = {}
        for p in paths:
            if cancel is not None and cancel.is_set():
                return groups
            done += 1
            if progress and done % 200 == 0:
                progress(f"Comparando conteúdo: {done}/{total}")
            digest = _hash_file(p, partial=True, cancel=cancel)
            if digest:
                partial.setdefault(digest, []).append(p)
        for same in partial.values():
            if len(same) < 2:
                continue
            full: dict[str, list[Path]] = {}
            for p in same:
                digest = _hash_file(p, partial=False, cancel=cancel)
                if digest:
                    full.setdefault(digest, []).append(p)
            for digest, files in full.items():
                if len(files) > 1:
                    groups.append(DuplicateGroup(digest, size, sorted(files)))
    groups.sort(key=lambda g: g.wasted, reverse=True)
    return groups


def validate_duplicate_selection(groups: list[DuplicateGroup], selected: set[Path]) -> Optional[str]:
    """Impede que TODAS as cópias de um grupo sejam removidas."""
    for g in groups:
        if all(f in selected for f in g.files):
            return (f"Todas as cópias de '{g.files[0].name}' foram selecionadas. "
                    "Mantenha ao menos uma cópia de cada grupo.")
    return None


def recycle_files(paths: list[Path], progress: ProgressFn = None) -> tuple[int, int, list[str]]:
    """Envia arquivos escolhidos pelo usuário para a Lixeira (recuperáveis).

    Retorna (quantidade, bytes, erros).
    """
    ok_count = freed = 0
    errors: list[str] = []
    log.info(f"INÍCIO envio de {len(paths)} arquivo(s) para a Lixeira")
    for i, path in enumerate(paths, 1):
        if progress:
            progress(f"{i}/{len(paths)}: {path.name}")
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        ok, msg = send_to_recycle_bin(path)
        if ok:
            ok_count += 1
            freed += size
            log.info(f"REMOVIDO (Lixeira): {path} ({format_size(size)})")
        else:
            errors.append(f"{path}: {msg}")
            log.warning(f"IGNORADO: {path} ({msg})")
    if ok_count:
        restore_manager.record_action(restore_manager.HistoryEntry(
            action_type="recycle_files",
            description=f"{ok_count} arquivo(s) enviados para a Lixeira ({format_size(freed)})",
            previous_state=[str(p) for p in paths][:200], new_state="na Lixeira",
            undoable=False,
            result="Restaure pela Lixeira do Windows (clique direito > Restaurar).",
        ))
    log.info(f"FIM envio para Lixeira: {ok_count} ok, {len(errors)} erro(s)")
    return ok_count, freed, errors

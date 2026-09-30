"""Limpeza de arquivos temporários e caches, em duas fases.

Fase 1 (scan_*):  apenas lista o que PODE ser removido — nada é apagado.
Fase 2 (clean_*): chamada somente após confirmação do usuário; revalida a
                  segurança de cada arquivo no momento da exclusão.

Regras de segurança:
  * só atua dentro de pastas explicitamente reconhecidas como temporárias;
  * nunca segue symlinks/junctions;
  * arquivos em uso (bloqueados) são ignorados e registrados no log;
  * idade mínima por categoria (ex.: TEMP só arquivos com mais de 24h),
    para não atrapalhar instaladores/programas em execução;
  * extensões executáveis/sistema são protegidas nas áreas do Windows.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import stat
import string
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.security import (FILE_ATTRIBUTE_REPARSE_POINT, check_temp_root, dedupe_paths,
                           is_reparse_point, is_safe_temp_file)
from utils.admin import is_admin
from utils.helpers import IS_WINDOWS, env_path, format_size
from utils.logger import get_logger

log = get_logger("temp_cleaner")

ProgressFn = Optional[Callable[[str], None]]

SKIP_DIR_NAMES = frozenset({".git", ".hg", ".svn"})


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

@dataclass
class CleanLocation:
    """Uma pasta autorizada para limpeza e seus filtros."""
    path: Path
    extensions: Optional[frozenset[str]] = None      # None = qualquer extensão
    name_prefixes: Optional[tuple[str, ...]] = None  # ex.: ("thumbcache_",)
    min_age_hours: float = 0
    protect_executables: bool = True
    recursive: bool = True


@dataclass
class CleanCategory:
    key: str
    name: str
    description: str
    locations: list[CleanLocation]
    risk: str = "baixo"             # "baixo" | "médio"
    safe_default: bool = True       # incluída em "Limpar tudo que for seguro"
    requires_admin: bool = False
    note: str = ""


@dataclass
class ScanResult:
    category: CleanCategory
    files: list[tuple[Path, int, Path]] = field(default_factory=list)  # (arquivo, tamanho, raiz)
    dirs: list[tuple[Path, Path]] = field(default_factory=list)        # (pasta, raiz)
    total_bytes: int = 0
    inaccessible: int = 0
    skipped: int = 0
    unavailable_reason: str = ""

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass
class CleanResult:
    category_key: str
    category_name: str
    removed_files: int = 0
    removed_dirs: int = 0
    freed_bytes: int = 0
    in_use: int = 0
    denied: int = 0
    skipped: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Catálogo de categorias
# ---------------------------------------------------------------------------

def _existing(locations: list[CleanLocation]) -> list[CleanLocation]:
    unique = dedupe_paths([loc.path for loc in locations])
    keep = {str(p).lower() for p in unique}
    out, seen = [], set()
    for loc in locations:
        key = str(loc.path).lower()
        if key in keep and key not in seen:
            seen.add(key)
            try:
                if loc.path.is_dir():
                    out.append(loc)
            except OSError:
                continue
    return out


_SPECIAL_PROFILES = frozenset({"public", "default", "default user", "all users"})


def other_user_temp_dirs() -> list[Path]:
    """Pastas TEMP dos outros perfis em C:\\Users (exceto o usuário atual e perfis especiais).

    Aliases localizados ("Usuário Padrão", "Todos os Usuários") são junctions
    de volta para Default/All Users e são ignorados para não varrer o mesmo alvo duas vezes.
    """
    users_root = Path(os.environ.get("SystemDrive", "C:") + "\\Users")
    current = env_path("USERPROFILE")
    current_key = os.path.normcase(str(current)) if current else ""
    result: list[Path] = []
    try:
        entries = list(users_root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if entry.name.lower() in _SPECIAL_PROFILES or os.path.normcase(str(entry)) == current_key:
            continue
        try:
            if not entry.is_dir() or is_reparse_point(entry):
                continue
        except OSError:
            continue
        result.append(entry / "AppData" / "Local" / "Temp")
    return result


def build_categories() -> list[CleanCategory]:
    """Categorias de limpeza existentes neste computador."""
    windir = env_path("SystemRoot", r"C:\Windows")
    local = env_path("LOCALAPPDATA")
    roaming = env_path("APPDATA")
    progdata = env_path("ProgramData", r"C:\ProgramData")
    cats: list[CleanCategory] = []

    user_temp = [env_path("TEMP"), env_path("TMP"), local / "Temp" if local else None]
    cats.append(CleanCategory(
        key="user_temp", name="Temporários do usuário (%TEMP%)",
        description="Arquivos temporários criados por programas e instaladores. "
                    "Somente arquivos com mais de 24 horas são removidos.",
        locations=[CleanLocation(p, min_age_hours=24, protect_executables=False)
                   for p in user_temp if p],
    ))

    if windir:
        cats.append(CleanCategory(
            key="windows_temp", name="Temporários do Windows (C:\\Windows\\Temp)",
            description="Pasta temporária do sistema. Arquivos com mais de 24 horas; "
                        "executáveis e arquivos de sistema são preservados.",
            locations=[CleanLocation(windir / "Temp", min_age_hours=24)],
            requires_admin=True,
        ))

    cats.append(CleanCategory(
        key="other_users_temp", name="Temporários de outros usuários",
        description="Pastas %TEMP% dos outros perfis deste computador. Somente arquivos com mais de 24 horas; "
                    "executáveis e instaladores são preservados.",
        locations=[CleanLocation(p, min_age_hours=24) for p in other_user_temp_dirs()],
        safe_default=False, requires_admin=True,
        note="Só aparece se houver outros perfis de usuário no PC.",
    ))

    win_cache: list[CleanLocation] = []
    if local:
        win_cache.append(CleanLocation(local / "Microsoft" / "Windows" / "INetCache", min_age_hours=1))
        win_cache.append(CleanLocation(local / "Microsoft" / "Windows" / "WER", min_age_hours=24))
    if progdata:
        win_cache.append(CleanLocation(progdata / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                                       min_age_hours=24))
        win_cache.append(CleanLocation(progdata / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                                       min_age_hours=24))
    cats.append(CleanCategory(
        key="windows_cache", name="Cache do Windows e relatórios de erro",
        description="Cache de internet do Windows (INetCache) e relatórios de erro (WER) já processados.",
        locations=win_cache,
    ))

    dumps: list[CleanLocation] = []
    if local:
        dumps.append(CleanLocation(local / "CrashDumps", min_age_hours=24 * 7,
                                   extensions=frozenset({".dmp"})))
    if windir:
        dumps.append(CleanLocation(windir / "Minidump", min_age_hours=24 * 30,
                                   extensions=frozenset({".dmp"})))
    cats.append(CleanCategory(
        key="crash_dumps", name="Dumps de falhas antigos",
        description="Arquivos .dmp de falhas de aplicativos (mais de 7 dias) e minidumps "
                    "do sistema (mais de 30 dias). Úteis apenas para diagnóstico de travamentos.",
        locations=dumps, requires_admin=False,
        note="Minidumps do Windows exigem Administrador.",
    ))

    if local:
        cats.append(CleanCategory(
            key="thumbnails", name="Cache de miniaturas do Explorer",
            description="Miniaturas de imagens/vídeos (thumbcache_*.db). O Windows recria "
                        "automaticamente; pastas com muitas imagens ficam mais lentas na 1ª abertura.",
            locations=[CleanLocation(local / "Microsoft" / "Windows" / "Explorer",
                                     extensions=frozenset({".db"}), name_prefixes=("thumbcache_",),
                                     recursive=False)],
            safe_default=False,
            note="Arquivos em uso pelo Explorer são ignorados.",
        ))

    shader: list[CleanLocation] = []
    if local:
        for rel in ("D3DSCache", "NVIDIA/DXCache", "NVIDIA/GLCache", "AMD/DxCache",
                    "AMD/DxcCache", "AMD/GLCache", "AMD/VkCache", "Intel/ShaderCache"):
            shader.append(CleanLocation(local / rel))
        locallow = local.parent / "LocalLow"
        shader.append(CleanLocation(locallow / "NVIDIA" / "PerDriverVersion" / "DXCache"))
        shader.append(CleanLocation(locallow / "Intel" / "ShaderCache"))
    cats.append(CleanCategory(
        key="shaders", name="Cache de shaders (DirectX/GPU)",
        description="Shaders compilados pelo driver de vídeo. São recriados automaticamente; "
                    "jogos podem ter pequenas travadas na primeira execução após a limpeza.",
        locations=shader, safe_default=False,
    ))

    if windir:
        cats.append(CleanCategory(
            key="update_cache", name="Downloads antigos do Windows Update",
            description="Arquivos já baixados em SoftwareDistribution\\Download com mais de 10 dias. "
                        "Instaladores (.cab/.msu/.exe) são preservados. O Windows baixa novamente se precisar.",
            locations=[CleanLocation(windir / "SoftwareDistribution" / "Download", min_age_hours=24 * 10)],
            risk="médio", safe_default=False, requires_admin=True,
        ))

    if progdata:
        cats.append(CleanCategory(
            key="delivery_optimization", name="Cache de Otimização de Entrega",
            description="Pedaços de atualizações compartilhados entre PCs pelo Windows Update "
                        "(DeliveryOptimization\\Cache), com mais de 24 horas. O Windows baixa novamente se precisar.",
            locations=[CleanLocation(progdata / "Microsoft" / "Windows" / "DeliveryOptimization" / "Cache",
                                     min_age_hours=24)],
            requires_admin=True,
        ))

    if windir:
        cats.append(CleanCategory(
            key="windows_logs", name="Logs antigos do Windows",
            description="Arquivos .log/.old/.etl em C:\\Windows\\Logs com mais de 3 dias. "
                        "Servem apenas para diagnóstico de problemas passados.",
            locations=[CleanLocation(windir / "Logs", min_age_hours=24 * 3,
                                     extensions=frozenset({".log", ".old", ".etl"}))],
            safe_default=False, requires_admin=True,
            note="Logs em uso pelo Windows são ignorados.",
        ))
        cats.append(CleanCategory(
            key="prefetch", name="Prefetch do Windows",
            description="Arquivos .pf que o Windows usa para abrir programas mais rápido. São recriados "
                        "automaticamente, mas os programas podem abrir um pouco mais devagar na primeira vez. "
                        "Limpar raramente traz benefício; use só para liberar espaço.",
            locations=[CleanLocation(windir / "Prefetch", extensions=frozenset({".pf"}), recursive=False)],
            risk="médio", safe_default=False, requires_admin=True,
        ))

    apps: list[CleanLocation] = []
    if roaming:
        for app in ("Code", "discord", "Slack", "Microsoft/Teams"):
            for sub in ("Cache", "Code Cache", "GPUCache", "CachedData"):
                apps.append(CleanLocation(roaming / app / sub, min_age_hours=1))
    if local:
        apps.append(CleanLocation(local / "Steam" / "htmlcache", min_age_hours=1))
        apps.append(CleanLocation(local / "pip" / "cache", min_age_hours=24))
    cats.append(CleanCategory(
        key="app_caches", name="Cache de aplicativos",
        description="Caches de VS Code, Discord, Slack, Teams (clássico), Steam (navegador interno) "
                    "e pip. Feche os aplicativos antes para liberar mais espaço.",
        locations=apps, safe_default=False,
    ))

    for cat in cats:
        cat.locations = _existing(cat.locations)
    return [c for c in cats if c.locations]


# ---------------------------------------------------------------------------
# Motor de varredura / exclusão (reutilizado pelo browser_cleaner)
# ---------------------------------------------------------------------------

def _matches(loc: CleanLocation, name: str) -> bool:
    lname = name.lower()
    if loc.extensions is not None and os.path.splitext(lname)[1] not in loc.extensions:
        return False
    if loc.name_prefixes and not lname.startswith(loc.name_prefixes):
        return False
    return True


def scan_category(category: CleanCategory, cancel: Optional[threading.Event] = None) -> ScanResult:
    """Varre as pastas da categoria. NÃO apaga nada."""
    result = ScanResult(category=category)
    if category.requires_admin and not is_admin():
        # Varre o que for legível; a exclusão informará o que falhar.
        result.unavailable_reason = "requer Administrador para remover tudo"
    now = time.time()
    for loc in category.locations:
        ok, reason, real_root = check_temp_root(loc.path)
        if not ok:
            log.warning(f"Pasta ignorada na varredura: {loc.path} ({reason})")
            result.skipped += 1
            continue
        min_age = loc.min_age_hours * 3600
        # Varre a partir da raiz REAL (resolve nomes curtos 8.3 como PEDRO~1).
        stack = [real_root]
        while stack:
            if cancel is not None and cancel.is_set():
                return result
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                result.inaccessible += 1
                continue
            if current != real_root:
                result.dirs.append((Path(current), loc.path))
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    result.inaccessible += 1
                    continue
                attrs = getattr(st, "st_file_attributes", 0) or 0
                if entry.is_dir(follow_symlinks=False):
                    # Nunca desce por symlinks/junctions nem pastas reservadas.
                    if attrs & FILE_ATTRIBUTE_REPARSE_POINT or entry.name.lower() in SKIP_DIR_NAMES:
                        result.skipped += 1
                    elif loc.recursive:
                        stack.append(entry.path)
                    continue
                if not _matches(loc, entry.name):
                    continue
                safe, _reason = is_safe_temp_file(entry.path, attrs, real_root, loc.protect_executables)
                if not safe:
                    result.skipped += 1
                    continue
                if min_age and now - max(st.st_mtime, st.st_ctime) < min_age:
                    continue
                result.files.append((Path(entry.path), st.st_size, loc.path))
                result.total_bytes += st.st_size
    # Pastas mais profundas primeiro (remoção de baixo para cima, só se vazias).
    result.dirs.sort(key=lambda item: len(item[0].parts), reverse=True)
    return result


def _delete_file(path: Path, real_root: str, protect_exec: bool, checked_parents: set[str]) -> str:
    """Remove um arquivo revalidando a segurança no ato. Retorna o status."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "denied"
    parent = str(path.parent)
    if parent not in checked_parents:
        # Uma pasta pode ter sido trocada por uma junction após a varredura:
        # resolve o caminho REAL da pasta (uma vez por pasta) e confere a raiz.
        try:
            real_parent = os.path.normcase(str(path.parent.resolve(strict=True)))
        except (OSError, RuntimeError):
            return "missing"
        root_norm = os.path.normcase(real_root)
        if real_parent != root_norm and not real_parent.startswith(root_norm + "\\"):
            return "not_safe"
        checked_parents.add(parent)
    safe, _ = is_safe_temp_file(str(path), getattr(st, "st_file_attributes", 0) or 0, real_root, protect_exec)
    if not safe:
        return "not_safe"
    try:
        os.remove(path)
        return "removed"
    except FileNotFoundError:
        return "missing"
    except PermissionError as exc:
        if getattr(exc, "winerror", None) == 32:
            return "in_use"
        # Somente leitura: remove o atributo e tenta de novo.
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return "removed"
        except OSError as exc2:
            return "in_use" if getattr(exc2, "winerror", None) == 32 else "denied"
    except OSError:
        return "error"


def clean_scan(scan: ScanResult, cancel: Optional[threading.Event] = None,
               progress: ProgressFn = None) -> CleanResult:
    cat = scan.category
    res = CleanResult(cat.key, cat.name)
    roots: dict[str, tuple[str, bool]] = {}
    for loc in cat.locations:
        ok, _, real_root = check_temp_root(loc.path)
        if ok:
            roots[str(loc.path)] = (real_root, loc.protect_executables)
    checked_parents: set[str] = set()
    log.info(f"INÍCIO limpeza: {cat.name} ({scan.file_count} arquivo(s), {format_size(scan.total_bytes)})")
    for i, (fp, size, root) in enumerate(scan.files):
        if cancel is not None and cancel.is_set():
            log.warning(f"Limpeza de '{cat.name}' cancelada pelo usuário")
            break
        if progress and i % 200 == 0:
            progress(f"{cat.name}: {i}/{scan.file_count}")
        real_root, protect_exec = roots.get(str(root), ("", True))
        status = _delete_file(fp, real_root, protect_exec, checked_parents) if real_root else "not_safe"
        if status == "removed":
            res.removed_files += 1
            res.freed_bytes += size
            log.debug(f"REMOVIDO: {fp} ({format_size(size)})")
        elif status == "in_use":
            res.in_use += 1
            log.debug(f"IGNORADO (em uso): {fp}")
        elif status == "denied":
            res.denied += 1
            log.debug(f"IGNORADO (acesso negado): {fp}")
        elif status in ("missing", "not_safe"):
            res.skipped += 1
            log.debug(f"IGNORADO ({status}): {fp}")
        else:
            res.errors += 1
            log.warning(f"ERRO ao remover: {fp}")
    for d, root in scan.dirs:
        try:
            if d.resolve() != root.resolve() and not is_reparse_point(d):
                os.rmdir(d)  # só remove se estiver vazia
                res.removed_dirs += 1
        except OSError:
            continue
    log.info(f"FIM limpeza: {cat.name}: {res.removed_files} removido(s), "
             f"{format_size(res.freed_bytes)} liberados, {res.in_use} em uso, "
             f"{res.denied} sem permissão, {res.errors} erro(s)")
    return res


# ---------------------------------------------------------------------------
# Lixeira (API oficial do Shell)
# ---------------------------------------------------------------------------

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def _drive_roots() -> list[str]:
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def query_recycle_bin() -> tuple[int, int]:
    """(itens, bytes) somados de todas as unidades."""
    if not IS_WINDOWS:
        return 0, 0
    shell32 = ctypes.windll.shell32
    shell32.SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    shell32.SHQueryRecycleBinW.restype = ctypes.c_long
    items = size = 0
    for drive in _drive_roots():
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        if shell32.SHQueryRecycleBinW(drive, ctypes.byref(info)) == 0:
            items += info.i64NumItems
            size += info.i64Size
    return items, size


def empty_recycle_bin() -> tuple[bool, str]:
    """Esvazia a Lixeira (chamar somente após confirmação explícita)."""
    if not IS_WINDOWS:
        return False, "não suportado"
    items, size = query_recycle_bin()
    shell32 = ctypes.windll.shell32
    shell32.SHEmptyRecycleBinW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
    shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
    flags = 0x1 | 0x2 | 0x4  # sem confirmação do shell (já confirmado na GUI), sem UI, sem som
    hr = shell32.SHEmptyRecycleBinW(None, None, flags)
    if hr in (0, 1):
        log.info(f"Lixeira esvaziada: {items} item(ns), {format_size(size)}")
        return True, f"{items} item(ns), {format_size(size)} liberados"
    msg = f"SHEmptyRecycleBinW retornou 0x{hr & 0xFFFFFFFF:08X}"
    log.error(f"ERRO ao esvaziar a Lixeira: {msg}")
    return False, msg

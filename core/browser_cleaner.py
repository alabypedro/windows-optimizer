"""Limpeza seletiva de CACHE de navegadores.

Apenas pastas de cache são tocadas. Nunca são apagados: senhas (Login Data),
favoritos (Bookmarks/places.sqlite), histórico (History), cookies (Cookies),
extensões, sessões ou preferências. Se o navegador estiver aberto, os
arquivos em uso são simplesmente ignorados (recomenda-se fechá-lo antes).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from core.temp_cleaner import CleanCategory, CleanLocation, ScanResult, scan_category
from utils.helpers import env_path

# Subpastas de cache de navegadores Chromium (por perfil).
CHROMIUM_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
                       "DawnWebGPUCache")
# Pastas de cache no nível "User Data" (compartilhadas entre perfis).
CHROMIUM_SHARED_CACHE = ("ShaderCache", "GrShaderCache", "GraphiteDawnCache")
FIREFOX_CACHE_DIRS = ("cache2", "startupCache", "thumbnails", "jumpListCache")

# Arquivos/pastas que NUNCA são tocados (usado nos testes como garantia).
NEVER_TOUCH = ("Login Data", "Bookmarks", "History", "Cookies", "Web Data", "Preferences",
               "places.sqlite", "logins.json", "key4.db", "cookies.sqlite", "Extensions")


@dataclass
class Browser:
    key: str
    name: str
    process_names: tuple[str, ...]
    profiles: list[Path] = field(default_factory=list)
    cache_locations: list[CleanLocation] = field(default_factory=list)

    @property
    def running(self) -> bool:
        names = {n.lower() for n in self.process_names}
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() in names:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def as_category(self) -> CleanCategory:
        return CleanCategory(
            key=f"browser_{self.key}", name=f"Cache do {self.name}",
            description="Somente cache (imagens, scripts e shaders). Senhas, favoritos, "
                        "histórico e cookies são preservados.",
            locations=self.cache_locations, safe_default=False,
        )


def _chromium(key: str, name: str, user_data: Path, procs: tuple[str, ...]) -> Browser | None:
    if not user_data.is_dir():
        return None
    browser = Browser(key, name, procs)
    try:
        entries = list(user_data.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile ")
                               or entry.name == "Guest Profile"):
            browser.profiles.append(entry)
            for sub in CHROMIUM_CACHE_DIRS:
                browser.cache_locations.append(CleanLocation(entry / sub))
    for sub in CHROMIUM_SHARED_CACHE:
        browser.cache_locations.append(CleanLocation(user_data / sub))
    # Opera guarda o perfil direto na pasta (sem "Default").
    if not browser.profiles and (user_data / "Preferences").exists():
        browser.profiles.append(user_data)
        for sub in CHROMIUM_CACHE_DIRS:
            browser.cache_locations.append(CleanLocation(user_data / sub))
    browser.cache_locations = [c for c in browser.cache_locations if c.path.is_dir()]
    return browser


def detect_browsers() -> list[Browser]:
    local = env_path("LOCALAPPDATA")
    roaming = env_path("APPDATA")
    found: list[Browser] = []
    if local:
        specs = [
            ("chrome", "Google Chrome", local / "Google/Chrome/User Data", ("chrome.exe",)),
            ("edge", "Microsoft Edge", local / "Microsoft/Edge/User Data", ("msedge.exe",)),
            ("brave", "Brave", local / "BraveSoftware/Brave-Browser/User Data", ("brave.exe",)),
            ("vivaldi", "Vivaldi", local / "Vivaldi/User Data", ("vivaldi.exe",)),
            ("chromium", "Chromium", local / "Chromium/User Data", ("chrome.exe",)),
        ]
        for key, name, path, procs in specs:
            b = _chromium(key, name, path, procs)
            if b:
                found.append(b)
    if roaming and local:
        opera_specs = [
            ("opera", "Opera", roaming / "Opera Software/Opera Stable", local / "Opera Software/Opera Stable"),
            ("opera_gx", "Opera GX", roaming / "Opera Software/Opera GX Stable",
             local / "Opera Software/Opera GX Stable"),
        ]
        for key, name, profile, cache_root in opera_specs:
            if profile.is_dir():
                b = Browser(key, name, ("opera.exe",), [profile])
                for sub in CHROMIUM_CACHE_DIRS:
                    for base in (profile, cache_root):
                        if (base / sub).is_dir():
                            b.cache_locations.append(CleanLocation(base / sub))
                found.append(b)
    if roaming and local:
        ff_profiles = roaming / "Mozilla/Firefox/Profiles"
        ff_local = local / "Mozilla/Firefox/Profiles"
        if ff_profiles.is_dir():
            b = Browser("firefox", "Mozilla Firefox", ("firefox.exe",))
            try:
                profiles = [p for p in ff_profiles.iterdir() if p.is_dir()]
            except OSError:
                profiles = []
            for prof in profiles:
                b.profiles.append(prof)
                for sub in FIREFOX_CACHE_DIRS:
                    for base in (ff_local / prof.name, prof):
                        if (base / sub).is_dir():
                            b.cache_locations.append(CleanLocation(base / sub))
            found.append(b)
    # Remove localizações duplicadas.
    for b in found:
        seen, unique = set(), []
        for loc in b.cache_locations:
            k = os.path.normcase(str(loc.path))
            if k not in seen:
                seen.add(k)
                unique.append(loc)
        b.cache_locations = unique
    return found


def scan_browser(browser: Browser) -> ScanResult:
    return scan_category(browser.as_category())

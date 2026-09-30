"""Publica uma nova versão do Windows Optimizer no GitHub Releases.

Depois disso, todo programa instalado encontra a versão nova pelo botão
"Verificar atualizações" (ou pelo aviso ao abrir).

Passo a passo para publicar:
  1. aumente APP_VERSION em utils/helpers.py (ex.: 1.0.0 -> 1.1.0);
  2. rode build_exe.bat e build_installer.bat;
  3. rode:  python publish_release.py "O que mudou nesta versão"

Requer o GitHub CLI autenticado (winget install GitHub.cli; gh auth login) e
UPDATE_REPO configurado em utils/helpers.py (ou a variável WO_UPDATE_REPO).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from utils.helpers import APP_VERSION, UPDATE_REPO  # noqa: E402


def main(argv: list[str]) -> int:
    notes = argv[0] if argv else f"Windows Optimizer {APP_VERSION}"
    if not UPDATE_REPO:
        print("[ERRO] Configure UPDATE_REPO em utils/helpers.py (ex.: \"seu-usuario/windows-optimizer\").")
        return 1
    gh = shutil.which("gh")
    if not gh:
        print("[ERRO] GitHub CLI nao encontrado. Instale com: winget install GitHub.cli  (depois: gh auth login)")
        return 1
    installer = ROOT / "installer_output" / f"WindowsOptimizer-Setup-{APP_VERSION}.exe"
    if not installer.exists():
        print(f"[ERRO] {installer.name} nao encontrado. Rode build_exe.bat e build_installer.bat antes.")
        return 1

    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    checksum = installer.with_name(installer.name + ".sha256")
    checksum.write_text(f"{digest}  {installer.name}\n", encoding="ascii")
    tag = f"v{APP_VERSION}"
    print(f"Publicando {tag} em {UPDATE_REPO}")
    print(f"  {installer.name}  sha256={digest}")
    result = subprocess.run([gh, "release", "create", tag, str(installer), str(checksum),
                             "--repo", UPDATE_REPO, "--title", f"Windows Optimizer {APP_VERSION}",
                             "--notes", notes, "--latest"])
    if result.returncode != 0:
        print("[ERRO] A publicacao falhou (veja a mensagem do gh acima). "
              "Se a versao ja existe, aumente APP_VERSION em utils/helpers.py.")
        return 1
    print(f"[OK] Versao {APP_VERSION} publicada: https://github.com/{UPDATE_REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

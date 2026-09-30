"""Testes do atualizador (sem internet: as respostas do GitHub são simuladas)."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402

INSTALLER = b"MZ" + b"x" * 5000
SHA = hashlib.sha256(INSTALLER).hexdigest()
REPO = "fulano/windows-optimizer"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self._url = url

    def geturl(self) -> str:
        return self._url


def release(version="1.2.0", digest=True, extra_assets=()):
    asset = {"name": f"WindowsOptimizer-Setup-{version}.exe", "size": len(INSTALLER),
             "browser_download_url": f"https://github.com/{REPO}/releases/download/v{version}/"
                                     f"WindowsOptimizer-Setup-{version}.exe"}
    if digest:
        asset["digest"] = f"sha256:{SHA}"
    return {"tag_name": f"v{version}", "body": "- melhoria A\n- correção B",
            "html_url": f"https://github.com/{REPO}/releases/tag/v{version}", "assets": [asset, *extra_assets]}


def fake_urlopen(routes: dict):
    def opener(request, timeout=None):
        url = request.full_url
        data, final = routes[url] if isinstance(routes[url], tuple) else (routes[url], url)
        return FakeResponse(data, final)
    return opener


class TestVersions(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(updater.is_newer("2", "1.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0", "1.0.0"))
        self.assertFalse(updater.is_newer("0.9.9", "1.0.0"))


class TestCheck(unittest.TestCase):
    api = f"https://api.github.com/repos/{REPO}/releases/latest"

    def check(self, payload, extra_routes=None):
        routes = {self.api: json.dumps(payload).encode(), **(extra_routes or {})}
        with mock.patch.object(updater.urllib.request, "urlopen", fake_urlopen(routes)):
            return updater.check_for_update(REPO)

    def test_reads_release_with_github_digest(self):
        info = self.check(release("1.2.0"))
        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.sha256, SHA)
        self.assertTrue(info.available)
        self.assertIn("melhoria A", info.notes)

    def test_same_version_is_not_available(self):
        with mock.patch.object(updater, "APP_VERSION", "1.2.0"):
            self.assertFalse(self.check(release("1.2.0")).available)

    def test_falls_back_to_sha256_file(self):
        url = f"https://github.com/{REPO}/releases/download/v1.2.0/WindowsOptimizer-Setup-1.2.0.exe.sha256"
        hash_asset = {"name": "WindowsOptimizer-Setup-1.2.0.exe.sha256", "browser_download_url": url}
        info = self.check(release(digest=False, extra_assets=[hash_asset]),
                          {url: f"{SHA}  WindowsOptimizer-Setup-1.2.0.exe\n".encode()})
        self.assertEqual(info.sha256, SHA)

    def test_refuses_release_without_hash(self):
        with self.assertRaisesRegex(updater.UpdateError, "SHA-256"):
            self.check(release(digest=False))

    def test_refuses_release_without_installer(self):
        payload = release()
        payload["assets"][0]["name"] = "outra-coisa.zip"
        with self.assertRaisesRegex(updater.UpdateError, "instalador"):
            self.check(payload)

    def test_unconfigured_repo(self):
        with mock.patch.object(updater, "UPDATE_REPO", ""), \
                self.assertRaisesRegex(updater.UpdateError, "configurada"):
            updater.check_for_update("")

    def test_offline_gives_friendly_message(self):
        def offline(request, timeout=None):
            raise updater.urllib.error.URLError("sem rede")
        with mock.patch.object(updater.urllib.request, "urlopen", offline):
            with self.assertRaisesRegex(updater.UpdateError, "internet"):
                updater.check_for_update(REPO)

    def test_redirect_outside_github_is_refused(self):
        routes = {self.api: (json.dumps(release()).encode(), "https://evil.example.com/x")}
        with mock.patch.object(updater.urllib.request, "urlopen", fake_urlopen(routes)):
            with self.assertRaisesRegex(updater.UpdateError, "não permitido"):
                updater.check_for_update(REPO)


class TestDownload(unittest.TestCase):
    def info(self, sha=SHA):
        return updater.UpdateInfo("1.2.0", "", "https://github.com/x", "WindowsOptimizer-Setup-1.2.0.exe",
                                  f"https://github.com/{REPO}/releases/download/v1.2.0/WindowsOptimizer-Setup-1.2.0.exe",
                                  len(INSTALLER), sha)

    def download(self, info, data=INSTALLER, final_url=None):
        routes = {info.installer_url: (data, final_url or
                                       "https://objects.githubusercontent.com/github-production-release-asset/1")}
        with mock.patch.object(updater.urllib.request, "urlopen", fake_urlopen(routes)):
            return updater.download_installer(info)

    def test_download_verifies_hash(self):
        path = self.download(self.info())
        try:
            self.assertEqual(path.read_bytes(), INSTALLER)
            self.assertEqual(path.name, "WindowsOptimizer-Setup-1.2.0.exe")
        finally:
            path.unlink()
            path.parent.rmdir()

    def test_tampered_download_is_discarded(self):
        seen = []
        real_mkdtemp = updater.tempfile.mkdtemp
        with mock.patch.object(updater.tempfile, "mkdtemp", lambda **kw: seen.append(real_mkdtemp(**kw)) or seen[-1]):
            with self.assertRaisesRegex(updater.UpdateError, "não confere"):
                self.download(self.info(), data=INSTALLER + b"virus")
        self.assertFalse(Path(seen[0]).exists(), "arquivo adulterado não pode ficar no disco")

    def test_non_https_url_is_refused(self):
        info = self.info()
        info.installer_url = "http://github.com/x.exe"
        with self.assertRaisesRegex(updater.UpdateError, "não permitido"):
            updater.download_installer(info)


class TestInstallGuard(unittest.TestCase):
    def test_source_run_cannot_self_update(self):
        self.assertIsNone(updater.installed_app_dir())  # rodando pelo Python, não pelo instalador
        with self.assertRaisesRegex(updater.UpdateError, "instalada"):
            updater.launch_installer(Path("x.exe"))


if __name__ == "__main__":
    unittest.main()

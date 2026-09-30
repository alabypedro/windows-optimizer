"""Testes de segurança e robustez (não alteram o sistema real).

Executar:  python -m unittest discover -s tests -v

Tudo acontece em pastas temporárias próprias (sandbox em %TEMP%).
Nenhum arquivo pessoal, serviço, item de inicialização ou configuração real é tocado.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import browser_cleaner, restore_manager, security, startup_manager, storage_analyzer, temp_cleaner  # noqa: E402
from core.temp_cleaner import CleanCategory, CleanLocation  # noqa: E402
from utils import helpers  # noqa: E402
from utils.admin import is_admin  # noqa: E402

OLD = time.time() - 10 * 86400


def _touch(path: Path, data: bytes = b"x" * 100, old: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if old:
        os.utime(path, (OLD, OLD))
    return path


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.sandbox = Path(tempfile.mkdtemp(prefix="wo_test_"))

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def category(self, *locations: CleanLocation) -> CleanCategory:
        return CleanCategory("test", "Teste", "", list(locations))


class TestPathGuards(SandboxTest):
    def test_rejects_critical_and_personal_roots(self):
        home = Path(os.environ["USERPROFILE"])
        windir = Path(os.environ["SystemRoot"])
        for bad in (Path("C:\\"), windir, windir / "System32", home / "Documents", home / "Desktop", home):
            ok, _, _ = security.check_temp_root(bad)
            self.assertFalse(ok, f"deveria recusar {bad}")

    def test_accepts_sandbox_root(self):
        ok, reason, _ = security.check_temp_root(self.sandbox)
        self.assertTrue(ok, reason)

    def test_file_checks(self):
        root = str(self.sandbox)
        self.assertTrue(security.is_safe_temp_file(os.path.join(root, "a.tmp"), 0, root)[0])
        self.assertFalse(security.is_safe_temp_file(r"C:\Windows\a.tmp", 0, root)[0])
        self.assertFalse(security.is_safe_temp_file(os.path.join(root, "..", "x.tmp"), 0, root)[0])
        self.assertFalse(security.is_safe_temp_file(os.path.join(root, "setup.exe"), 0, root, True)[0])
        self.assertTrue(security.is_safe_temp_file(os.path.join(root, "setup.exe"), 0, root, False)[0])
        self.assertFalse(security.is_safe_temp_file(os.path.join(root, "l.tmp"),
                                                    security.FILE_ATTRIBUTE_REPARSE_POINT, root)[0])
        # Prefixo de nome parecido não conta como "dentro" (C:\x\temp2 vs C:\x\temp)
        self.assertFalse(security.is_safe_temp_file(root + "2\\a.tmp", 0, root)[0])

    def test_user_file_delete_guard(self):
        self.assertFalse(security.is_safe_user_file_delete(Path(os.environ["SystemRoot"]) / "notepad.exe")[0])
        self.assertFalse(security.is_safe_user_file_delete(Path(os.environ["ProgramFiles"]) / "x.txt")[0])
        self.assertFalse(security.is_safe_user_file_delete(self.sandbox / "nao_existe.txt")[0])
        self.assertTrue(security.is_safe_user_file_delete(_touch(self.sandbox / "f.txt"))[0])
        ok, _ = security.send_to_recycle_bin(Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe")
        self.assertFalse(ok)

    def test_all_builtin_categories_are_safe_roots(self):
        personal = security.personal_roots()
        for cat in temp_cleaner.build_categories():
            for loc in cat.locations:
                ok, reason, real = security.check_temp_root(loc.path)
                self.assertTrue(ok, f"{loc.path}: {reason}")
                for p in personal:
                    self.assertFalse(Path(real).is_relative_to(p), f"{loc.path} dentro de {p}")


class TestProtectedLists(unittest.TestCase):
    def test_processes(self):
        for name in ("lsass.exe", "csrss.exe", "svchost.exe", "winlogon.exe", "MsMpEng.exe", "dwm.exe"):
            self.assertTrue(security.is_process_protected(name, 1234)[0], name)
        self.assertTrue(security.is_process_protected("System", 4)[0])
        self.assertTrue(security.is_process_protected("python.exe", os.getpid())[0])
        self.assertFalse(security.is_process_protected("notepad.exe", 1234)[0])

    def test_services(self):
        for name in ("WinDefend", "mpssvc", "wuauserv", "WaaSMedicSvc", "BFE", "RpcSs", "CDPUserSvc_1a2b3c"):
            self.assertTrue(security.is_service_protected(name)[0], name)
        self.assertFalse(security.is_service_protected("Fax", "Fax")[0])
        self.assertFalse(security.is_service_protected("XblGameSave", "Xbox Live Game Save")[0])

    def test_startup(self):
        self.assertTrue(security.is_startup_item_protected("SecurityHealth", r"%windir%\system32\SecurityHealthSystray.exe")[0])
        self.assertTrue(security.is_startup_item_protected("RtkAudUService", r"C:\x\RtkAudUService64.exe")[0])
        self.assertFalse(security.is_startup_item_protected("Discord", r"C:\Users\u\Discord\Update.exe")[0])
        self.assertFalse(security.is_startup_item_protected("StreamDeck", r"C:\Elgato\StreamDeck.exe")[0])

    def test_extract_exe_path(self):
        f = startup_manager.extract_exe_path
        self.assertEqual(f('"C:\\A B\\app.exe" --min'), "C:\\A B\\app.exe")
        self.assertEqual(f("C:\\A B\\app.exe -silent"), "C:\\A B\\app.exe")


class TestTempCleaner(SandboxTest):
    def test_age_filter_and_cleanup(self):
        # A idade usa max(modificação, criação): arquivos recém-criados nunca são removidos,
        # mesmo que tragam data de modificação antiga. Para simular, "avançamos o relógio".
        future = time.time() + 10 * 86400
        old = _touch(self.sandbox / "sub" / "old.tmp")
        new = _touch(self.sandbox / "new.tmp", old=False)
        os.utime(new, (future, future))
        cat = self.category(CleanLocation(self.sandbox, min_age_hours=24))
        with mock.patch.object(temp_cleaner.time, "time", return_value=future):
            scan = temp_cleaner.scan_category(cat)
        recent = temp_cleaner.scan_category(cat)  # relógio real: tudo foi criado agora
        self.assertEqual(recent.file_count, 0, "arquivo criado agora não pode ser candidato")
        self.assertEqual({p for p, _, _ in scan.files}, {old})
        res = temp_cleaner.clean_scan(scan)
        self.assertEqual(res.removed_files, 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists(), "arquivo recente não pode ser removido")
        self.assertTrue(self.sandbox.exists(), "a raiz nunca é removida")

    def test_locked_file_is_skipped(self):
        locked = _touch(self.sandbox / "locked.tmp")
        other = _touch(self.sandbox / "other.tmp")
        scan = temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox)))
        with open(locked, "rb"):
            res = temp_cleaner.clean_scan(scan)
        self.assertEqual(res.in_use, 1)
        self.assertEqual(res.removed_files, 1)
        self.assertTrue(locked.exists())
        self.assertFalse(other.exists())

    def test_readonly_file_is_removed(self):
        ro = _touch(self.sandbox / "ro.tmp")
        os.chmod(ro, 0o444)
        res = temp_cleaner.clean_scan(temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox))))
        self.assertEqual(res.removed_files, 1)

    def test_missing_directory(self):
        cats = temp_cleaner._existing([CleanLocation(self.sandbox / "nao_existe")])
        self.assertEqual(cats, [])
        scan = temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox / "nao_existe")))
        self.assertEqual(scan.file_count, 0)

    def test_file_deleted_between_scan_and_clean(self):
        f = _touch(self.sandbox / "gone.tmp")
        scan = temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox)))
        f.unlink()
        res = temp_cleaner.clean_scan(scan)
        self.assertEqual(res.errors, 0)
        self.assertEqual(res.removed_files, 0)

    def test_executables_protected(self):
        exe = _touch(self.sandbox / "tool.exe")
        dll = _touch(self.sandbox / "lib.dll")
        tmp = _touch(self.sandbox / "x.tmp")
        scan = temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox, protect_executables=True)))
        self.assertEqual({p for p, _, _ in scan.files}, {tmp})
        temp_cleaner.clean_scan(scan)
        self.assertTrue(exe.exists() and dll.exists())

    def test_junction_is_not_followed(self):
        outside = Path(tempfile.mkdtemp(prefix="wo_outside_"))
        try:
            precious = _touch(outside / "precious.txt")
            root = self.sandbox / "temp"
            root.mkdir()
            link = root / "link"
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if r.returncode != 0:
                self.skipTest("não foi possível criar junction")
            _touch(root / "a.tmp")
            scan = temp_cleaner.scan_category(self.category(CleanLocation(root)))
            self.assertTrue(all("precious" not in str(p) for p, _, _ in scan.files))
            temp_cleaner.clean_scan(scan)
            self.assertTrue(precious.exists(), "arquivo fora da raiz via junction foi apagado!")
        finally:
            for p in (self.sandbox / "temp" / "link",):
                if p.exists():
                    os.rmdir(p)  # remove a junction, não o alvo
            shutil.rmtree(outside, ignore_errors=True)

    def test_junction_swapped_after_scan(self):
        """Pasta trocada por junction entre a varredura e a exclusão não é seguida."""
        outside = Path(tempfile.mkdtemp(prefix="wo_outside_"))
        try:
            root = self.sandbox / "temp"
            _touch(root / "d" / "same.tmp")
            victim = _touch(outside / "same.tmp")
            scan = temp_cleaner.scan_category(self.category(CleanLocation(root)))
            shutil.rmtree(root / "d")
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "d"), str(outside)], capture_output=True)
            if r.returncode != 0:
                self.skipTest("não foi possível criar junction")
            res = temp_cleaner.clean_scan(scan)
            self.assertTrue(victim.exists(), "exclusão seguiu uma junction criada após a varredura!")
            self.assertEqual(res.removed_files, 0)
        finally:
            j = self.sandbox / "temp" / "d"
            if j.exists():
                os.rmdir(j)
            shutil.rmtree(outside, ignore_errors=True)

    def test_access_denied_directory(self):
        denied = self.sandbox / "denied"
        _touch(denied / "a.tmp")
        _touch(self.sandbox / "ok.tmp")
        user = os.environ.get("USERNAME", "")
        r = subprocess.run(["icacls", str(denied), "/deny", f"{user}:(RD)"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("icacls indisponível")
        try:
            scan = temp_cleaner.scan_category(self.category(CleanLocation(self.sandbox)))
            res = temp_cleaner.clean_scan(scan)  # não pode lançar exceção
            self.assertGreaterEqual(scan.inaccessible, 1)
            self.assertEqual(res.removed_files, 1)
        finally:
            subprocess.run(["icacls", str(denied), "/remove:d", user], capture_output=True)

    def test_other_user_temp_dirs(self):
        """Só perfis reais de outros usuários: nunca o atual, perfis especiais ou junctions."""
        users = self.sandbox / "Users"
        for name in ("atual", "Public", "Default", "outro"):
            (users / name / "AppData" / "Local" / "Temp").mkdir(parents=True)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(users / "Usuário Padrão"), str(users / "Default")],
                           capture_output=True)
        env = {"SystemDrive": str(self.sandbox), "USERPROFILE": str(users / "atual")}
        with mock.patch.dict(os.environ, env):
            found = temp_cleaner.other_user_temp_dirs()
        self.assertEqual(found, [users / "outro" / "AppData" / "Local" / "Temp"])
        if r.returncode == 0:
            os.rmdir(users / "Usuário Padrão")

    def test_system_categories_need_admin_and_are_opt_in(self):
        cats = {c.key: c for c in temp_cleaner.build_categories()}
        for key in ("prefetch", "windows_logs", "other_users_temp"):
            if key in cats:
                self.assertTrue(cats[key].requires_admin, key)
                self.assertFalse(cats[key].safe_default, f"{key} não pode entrar na limpeza segura")
        if "prefetch" in cats:
            loc = cats["prefetch"].locations[0]
            self.assertEqual(loc.extensions, frozenset({".pf"}))
            self.assertFalse(loc.recursive)


class TestCliCleanup(SandboxTest):
    def test_dry_run_never_deletes(self):
        from core import cli_cleanup
        f = _touch(self.sandbox / "old.tmp")
        cat = self.category(CleanLocation(self.sandbox))
        with mock.patch.object(temp_cleaner, "build_categories", return_value=[cat]), \
                mock.patch.object(temp_cleaner, "empty_recycle_bin") as empty:
            self.assertEqual(cli_cleanup.run_clean(dry_run=True, yes=True, quiet=True), 0)
        self.assertTrue(f.exists())
        empty.assert_not_called()

    def test_without_confirmation_nothing_is_deleted(self):
        from core import cli_cleanup
        f = _touch(self.sandbox / "old.tmp")
        cat = self.category(CleanLocation(self.sandbox))
        with mock.patch.object(temp_cleaner, "build_categories", return_value=[cat]), \
                mock.patch.object(cli_cleanup, "_confirm", return_value=False), \
                mock.patch.object(temp_cleaner, "empty_recycle_bin") as empty:
            cli_cleanup.run_clean(quiet=True)
        self.assertTrue(f.exists())
        empty.assert_not_called()

    def test_yes_cleans_without_touching_recycle_bin_when_disabled(self):
        from core import cli_cleanup
        f = _touch(self.sandbox / "old.tmp")
        cat = self.category(CleanLocation(self.sandbox))
        with mock.patch.object(temp_cleaner, "build_categories", return_value=[cat]), \
                mock.patch.object(temp_cleaner, "empty_recycle_bin") as empty, \
                mock.patch.object(cli_cleanup.restore_manager, "record_action"):
            cli_cleanup.run_clean(yes=True, recycle_bin=False, quiet=True)
        self.assertFalse(f.exists())
        empty.assert_not_called()


class TestBrowsers(SandboxTest):
    def test_no_browsers(self):
        env = {"LOCALAPPDATA": str(self.sandbox / "L"), "APPDATA": str(self.sandbox / "R")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(browser_cleaner.detect_browsers(), [])

    def test_only_cache_is_cleaned(self):
        local = self.sandbox / "L"
        profile = local / "Google" / "Chrome" / "User Data" / "Default"
        cache = _touch(profile / "Cache" / "Cache_Data" / "f_0001")
        keep = [_touch(profile / n) for n in ("Cookies", "Login Data", "Bookmarks", "History", "Preferences")]
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(self.sandbox / "R")}):
            browsers = browser_cleaner.detect_browsers()
        self.assertEqual([b.key for b in browsers], ["chrome"])
        scan = browser_cleaner.scan_browser(browsers[0])
        self.assertEqual([p for p, _, _ in scan.files], [cache])
        temp_cleaner.clean_scan(scan)
        self.assertFalse(cache.exists())
        for k in keep:
            self.assertTrue(k.exists(), f"{k.name} não pode ser apagado")


class TestStorage(SandboxTest):
    def test_duplicates(self):
        data = os.urandom(5000)
        a = _touch(self.sandbox / "a.bin", data)
        b = _touch(self.sandbox / "sub" / "b.bin", data)
        _touch(self.sandbox / "c.bin", data[:-1] + b"\0")  # mesmo tamanho, conteúdo diferente
        groups = storage_analyzer.find_duplicates(self.sandbox, min_size_kb=1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0].files), {a, b})
        self.assertIsNotNone(storage_analyzer.validate_duplicate_selection(groups, {a, b}))
        self.assertIsNone(storage_analyzer.validate_duplicate_selection(groups, {b}))
        self.assertTrue(a.exists() and b.exists(), "busca de duplicados nunca apaga")

    def test_large_files(self):
        _touch(self.sandbox / "small.txt", b"1")
        big = _touch(self.sandbox / "big.iso", b"0" * (11 * 1024 * 1024))
        found = storage_analyzer.find_large_files(self.sandbox, min_size_mb=100,
                                                  kinds={"Imagem de disco (ISO/VHD)"})
        self.assertEqual([f.path for f in found], [big])
        self.assertTrue(big.exists())

    def test_drives(self):
        drives = storage_analyzer.drives_with_media()
        self.assertTrue(drives)
        for d in drives:
            self.assertGreaterEqual(d.total, d.free)
            self.assertIn(d.media, ("SSD", "HDD", "Desconhecido"))


class TestHistory(SandboxTest):
    def test_history_and_undo(self):
        with mock.patch.object(helpers, "_data_dir_cache", self.sandbox):
            calls = []
            restore_manager.register_undo_handler("test_undo", lambda d: (calls.append(d) or True, "ok"))
            e = restore_manager.record_action(restore_manager.HistoryEntry(
                action_type="t", description="teste", previous_state="a", new_state="b",
                undoable=True, undo_type="test_undo", undo_data={"x": 1}))
            self.assertEqual(restore_manager.load_history()[-1].id, e.id)
            self.assertTrue(restore_manager.undo_action(e.id)[0])
            self.assertEqual(calls, [{"x": 1}])
            self.assertFalse(restore_manager.undo_action(e.id)[0], "não pode desfazer duas vezes")
            ne = restore_manager.record_action(restore_manager.HistoryEntry(action_type="t", description="x"))
            self.assertFalse(restore_manager.undo_action(ne.id)[0])

    def test_corrupt_history(self):
        with mock.patch.object(helpers, "_data_dir_cache", self.sandbox):
            (self.sandbox / "backups").mkdir(exist_ok=True)
            restore_manager.history_file().write_text("{corrompido", encoding="utf-8")
            self.assertEqual(restore_manager.load_history(), [])


@unittest.skipIf(is_admin(), "teste do modo sem Administrador")
class TestWithoutAdmin(unittest.TestCase):
    def test_admin_operations_refused(self):
        from core import service_manager
        svc = service_manager.ServiceInfo("Fax", "Fax", "stopped", "manual", "", "", False, False, "", "", "", None)
        ok, msg = service_manager.change_start_type(svc, "disabled")
        self.assertFalse(ok)
        self.assertIn("Administrador", msg)
        item = startup_manager.StartupItem("X", "x.exe", "x.exe", "", "run", "HKLM", "Run", True)
        self.assertFalse(startup_manager.set_enabled(item, False)[0])
        self.assertFalse(storage_analyzer.enable_trim()[0])
        self.assertFalse(restore_manager.create_restore_point()[0])


class TestHelpers(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(helpers.decode_output("Verificação".encode("utf-16-le")), "Verificação")
        self.assertEqual(helpers.decode_output("ação".encode("utf-8")), "ação")
        self.assertEqual(helpers.decode_output("ação".encode("cp850")), "ação")

    def test_commands_never_raise(self):
        self.assertFalse(helpers.run_command(["comando_que_nao_existe_xyz"]).ok)
        r = helpers.run_command(["ping", "-n", "5", "127.0.0.1"], timeout=1)
        self.assertTrue(r.timed_out)

    def test_format(self):
        self.assertEqual(helpers.format_size(1536), "1,50 KB")
        self.assertEqual(helpers.format_int(1234567), "1.234.567")


if __name__ == "__main__":
    unittest.main(verbosity=2)

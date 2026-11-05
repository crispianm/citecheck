import hashlib
import io
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

from citecheck import config, setup_wizard, ui  # noqa: E402


def _env(**kw):
    e = dict(os.environ)
    e.update(kw)
    return e


class ConfigTest(unittest.TestCase):
    def test_round_trip_and_paths(self):
        with tempfile.TemporaryDirectory() as t:
            env = {"XDG_CONFIG_HOME": t + "/c", "XDG_DATA_HOME": t + "/d"}
            env.pop("CITECHECK_DB", None)
            with mock.patch.dict(os.environ, env):
                os.environ.pop("CITECHECK_DB", None)
                self.assertEqual(config.load(), {})
                d = {"data_dir": t + "/dd", "mode": "lite", "s2_api_key": "abc=def"}
                config.save(d)
                self.assertEqual(config.load(), d)
                self.assertEqual(config.db_path(d), Path(t + "/dd/dblp.sqlite"))
                self.assertEqual(config.db_path({}), Path(t + "/d/citecheck/dblp.sqlite"))
                self.assertTrue(config.cache_dir(d).is_dir())
                with mock.patch.dict(os.environ, {"CITECHECK_DB": "/x/y.sqlite"}):
                    self.assertEqual(config.db_path(d), Path("/x/y.sqlite"))
                self.assertNotEqual(config.load(), {"mode": "full"})  # control


class Md5Test(unittest.TestCase):
    def test_verify(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello citecheck")
        try:
            good = hashlib.md5(b"hello citecheck").hexdigest()
            self.assertTrue(setup_wizard.verify_md5(f.name, good.upper()))
            # forced-fail control: a wrong hash must be rejected
            self.assertFalse(setup_wizard.verify_md5(f.name, "0" * 32))
        finally:
            os.unlink(f.name)


class UiTest(unittest.TestCase):
    def test_bar_plain(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            b = ui.ProgressBar(100, "bytes", "dl", stream=io.StringIO())
            line = b.render(50, width=100)
            self.assertNotIn("\033", line)
            self.assertIn(" 50%", line)
            self.assertNotIn(" 99%", line)  # control
            self.assertNotEqual(ui.bar_str(0.5, 10), ui.bar_str(1.0, 10))

    def test_bar_unknown_total(self):
        b = ui.ProgressBar(0, "count", stream=io.StringIO())
        self.assertIn("1,234", b.render(1234, width=80))
        b.update(5)
        b.close()  # must not raise or divide by zero
        self.assertNotIn("%", b.render(5, width=80))

    def test_bar_non_tty_prints_deciles(self):
        s = io.StringIO()
        b = ui.ProgressBar(100, "count", stream=s)
        for i in range(0, 101, 5):
            b.update(i)
        self.assertEqual(len(s.getvalue().splitlines()), 11)

    def test_spinner_plain(self):
        s = io.StringIO()
        with ui.Spinner("working", stream=s):
            pass
        self.assertIn("working", s.getvalue())


class DownloadTest(unittest.TestCase):
    def _serve(self, size=50 * 1024 * 1024):
        d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, d, True)
        blob = (b"0123456789abcdef" * 4096) * (size // 65536)
        (Path(d) / "dummy.gz").write_bytes(blob)
        sock = __import__("socket").socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        p = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                              "-d", d], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)
        url = "http://127.0.0.1:%d/dummy.gz" % port
        for _ in range(50):
            if setup_wizard.remote_size(url):
                break
            time.sleep(0.1)
        return url, len(blob), hashlib.md5(blob).hexdigest()

    def _run(self, no_aria):
        url, n, md5 = self._serve()
        self.assertEqual(setup_wizard.remote_size(url), n)
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, out, True)
        dest = Path(out) / "x" / "dummy.gz"
        real = shutil.which
        which = (lambda c: None if c == "aria2c" else real(c)) if no_aria else real
        buf = io.StringIO()
        with mock.patch.object(setup_wizard.shutil, "which", which), \
                mock.patch.object(ui.sys, "stdout", buf):
            ok = setup_wizard.download(url, dest)
        self.assertTrue(ok)
        self.assertTrue(setup_wizard.verify_md5(dest, md5))
        self.assertIn("100%", buf.getvalue())
        # forced-fail control: a bad URL returns False
        with mock.patch.object(setup_wizard.shutil, "which", which), mock.patch.object(ui.sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertFalse(setup_wizard.download(url.replace("dummy", "nope"), Path(out) / "bad.gz"))

    def test_curl(self):
        if not shutil.which("curl"):
            self.skipTest("no curl")
        self._run(True)

    def test_aria2(self):
        if not shutil.which("aria2c"):
            self.skipTest("no aria2c")
        self._run(False)


class WizardTest(unittest.TestCase):
    def test_yes_none(self):
        with tempfile.TemporaryDirectory() as t:
            env = _env(HOME=t, XDG_CONFIG_HOME="", XDG_DATA_HOME="", PYTHONPATH=str(ROOT))
            env.pop("CITECHECK_DB", None)
            r = subprocess.run([sys.executable, "-m", "citecheck.setup_wizard", "--yes", "--index", "none"],
                               env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            cfg = (Path(t) / ".config/citecheck/config").read_text()
            self.assertIn("mode = none", cfg)
            self.assertIn("All set", r.stdout)

    def test_bad_flag_fails(self):  # control
        r = subprocess.run([sys.executable, "-m", "citecheck.setup_wizard", "--index", "bogus"],
                           env=_env(PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_default_mode(self):
        self.assertEqual(setup_wizard.default_mode(100), "full")
        self.assertEqual(setup_wizard.default_mode(6.5), "lite")
        self.assertEqual(setup_wizard.default_mode(1), "none")


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class InstallShTest(unittest.TestCase):
    def test_install_twice_and_uninstall(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as t:
            src, home = Path(t) / "src", Path(t) / "home"
            home.mkdir()
            shutil.copytree(str(ROOT / "citecheck"), str(src / "citecheck"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(str(ROOT / "install.sh"), str(src / "install.sh"))
            g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.check_call(["git", "init", "-q", "-b", "main", str(src)])
            subprocess.check_call(["git", "-C", str(src), "add", "-A"])
            subprocess.check_call(g + ["-C", str(src), "commit", "-qm", "t"])
            env = _env(HOME=str(home), SHELL="/bin/bash", NO_COLOR="1", CITECHECK_REPO="file://" + str(src),
                       XDG_CONFIG_HOME="", XDG_DATA_HOME="")
            env.pop("CITECHECK_HOME", None)
            env.pop("CITECHECK_DB", None)
            cmd = ["bash", str(src / "install.sh"), "--yes", "--index", "none", "--no-modify-path"]
            for _ in range(2):  # idempotent
                r = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            shim = home / ".local/bin/citecheck"
            self.assertTrue(shim.exists())
            self.assertTrue((home / ".citecheck/citecheck/__main__.py").exists() or
                            (home / ".citecheck/citecheck/setup_wizard.py").exists())
            self.assertIn("mode = none", (home / ".config/citecheck/config").read_text())
            self.assertFalse((home / ".bashrc").exists())  # --no-modify-path
            r = subprocess.run(["bash", str(src / "install.sh"), "--uninstall", "--yes"], env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(shim.exists())
            self.assertFalse((home / ".citecheck").exists())

    def test_syntax(self):
        self.assertEqual(subprocess.call(["bash", "-n", str(ROOT / "install.sh")]), 0)


if __name__ == "__main__":
    unittest.main()

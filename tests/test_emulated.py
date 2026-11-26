"""Testa a CLI de ponta a ponta contra o emulador (tests/fake_pedal.py)."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from zoomfx import cli
from zoomfx import patch as P
from zoomfx.catalog import Catalog

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


@unittest.skipUnless(sys.platform.startswith("linux"), "emulador usa pty do Linux")
class TestEmulated(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_pedal.py")],
                                     stdout=subprocess.PIPE, text=True)
        self.dev = self.proc.stdout.readline().strip()
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.proc.kill()
        self.proc.wait()
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--device", self.dev] + list(args))
        return code, out.getvalue()

    def test_push_store_pull(self):
        src = os.path.join(ROOT, "patches", "thrash.json")
        code, out = self.run_cli("push", src, "--slot", "C4", "--store")
        self.assertEqual(code, 0, out)
        self.assertIn("Gravado em C4", out)
        self.assertIn("write_patch", out)
        self.assertEqual(len(os.listdir("backups")), 1)
        code, out = self.run_cli("pull", "--slot", "C4", "-o", "c4.json")
        self.assertEqual(code, 0, out)
        cat = Catalog()
        original = P.encode(P.load(src), cat, level_fallback=70)
        self.assertEqual(P.encode(P.load("c4.json"), cat), original)


if __name__ == "__main__":
    unittest.main()

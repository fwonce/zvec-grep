"""Portable tests for timeout capture ordering; no GPU or debugger required."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("diagnostics", Path(__file__).with_name("diagnose-windows-cuda.py"))
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticTests(unittest.TestCase):
    def test_timeout_inspects_live_child_before_termination(self):
        observed = []
        def inspect(process, *args):
            self.assertIsNone(process.poll())
            observed.append(process)
        with tempfile.TemporaryDirectory() as directory, patch.object(diagnostics, "inspect_hang", inspect), patch.object(diagnostics, "capture", return_value=0):
            root = Path(directory)
            with self.assertRaises(subprocess.TimeoutExpired):
                diagnostics.execute(Path(sys.executable), root, root, "index",
                                    ["-c", "import time; time.sleep(10)"], dict(os.environ),
                                    concurrency=1, timeout=0.05, cdb=Path("unused"))
            self.assertEqual(len(observed), 1)
            self.assertIsNotNone(observed[0].poll())
            self.assertTrue((root / "index.stderr").is_file())

    def test_success_does_not_attach_debugger(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(diagnostics, "inspect_hang") as inspect:
            root = Path(directory)
            out, _ = diagnostics.execute(Path(sys.executable), root, root, "query",
                                         ["-c", "print('fixture')"], dict(os.environ),
                                         concurrency=2, timeout=5, cdb=Path("unused"))
            self.assertIn("fixture", out)
            inspect.assert_not_called()

    def test_concurrency_argument_is_applied_to_the_actual_command(self):
        for concurrency in (1, 2):
            with self.subTest(concurrency=concurrency), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                out, _ = diagnostics.execute(Path(sys.executable), root, root, "index",
                                             ["-c", "import sys; print(sys.argv[1:])", "--embedding-concurrency", "2"],
                                             dict(os.environ), concurrency=concurrency, timeout=5, cdb=Path("unused"))
                self.assertIn(f"'--embedding-concurrency', '{concurrency}'", out)

    def test_same_cuda12_path_for_both_onnx_cases(self):
        path = os.pathsep.join(("/cuda12/bin", "/cuda13/bin", "/other"))
        self.assertEqual(diagnostics.case_path(path, "onnx", "/cuda13"), os.pathsep.join(("/cuda12/bin", "/other")))
        self.assertEqual(diagnostics.case_path(path, "gguf", "/cuda13"), path)


if __name__ == "__main__":
    unittest.main()

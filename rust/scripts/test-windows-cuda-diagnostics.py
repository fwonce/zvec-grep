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
    def test_existing_case_log_directory_can_run_the_complete_cli_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            (logs / "runtime-path.txt").write_text("runtime")
            selected = ('llama.cpp CUDA backend library selected cuda_version="13.1"\n'
                        'llama.cpp GPU embedding backend selected backend=CUDA\n'
                        'offloaded 25/25 layers to GPU\nIndex complete')
            calls = [("", selected), ("Workspace index is ready local/embeddinggemma-300m", ""),
                     ("gpu-fixture.md cuda_cli_e2e_marker", selected)]
            with patch.object(diagnostics.cli, "run_zg", side_effect=calls) as run:
                evidence = diagnostics.cli.run_case(root / "zg.exe", root / "models", root / "work",
                                                    logs, "gguf", "local/embeddinggemma-300m", "13.1")
                self.assertEqual(run.call_count, 3)
                self.assertEqual(evidence["gguf_cuda"], "13.1")
                self.assertEqual((logs / "runtime-path.txt").read_text(), "runtime")

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

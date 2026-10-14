#!/usr/bin/env python3
"""Check fallback-only CMake configuration without a CUDA compiler."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("cuda12", Path(__file__).with_name("build-ggml-cuda12.py"))
cuda12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cuda12)


class BuildConfigurationTests(unittest.TestCase):
    def test_disables_unshipped_app_and_non_library_targets(self):
        args = cuda12.configure(Path("source"), Path("build"), Path("cuda-12.4"), "75")
        for target in ("APP", "COMMON", "TESTS", "EXAMPLES", "TOOLS", "SERVER"):
            self.assertIn(f"-DLLAMA_BUILD_{target}=OFF", args)
        self.assertIn("-DGGML_CUDA=ON", args)
        self.assertIn("-DGGML_BACKEND_DL=ON", args)
        self.assertIn("-DGGML_CPU=OFF", args)
        self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=75", args)

    def test_rejects_wrong_toolkit_before_any_build_or_package_changes(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            cuda12.subprocess, "check_output", return_value="Cuda compilation tools, release 13.1, V13.1.1"
        ) as command:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "CUDA 12.4"):
                cuda12.build_plugin(root / "bundle", root / "cuda", root / "target")
            self.assertEqual(command.call_count, 1)
            self.assertFalse((root / "bundle").exists())
            self.assertFalse((root / "target").exists())


if __name__ == "__main__":
    unittest.main()

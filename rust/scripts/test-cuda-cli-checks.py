#!/usr/bin/env python3
"""CPU-only regression tests for the GPU evidence assertions."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("cuda_cli", Path(__file__).with_name("test-cuda-cli.py"))
cuda_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cuda_cli)


class CudaEvidenceTests(unittest.TestCase):
    def test_requested_gguf_plugin_must_really_be_selected(self):
        selected = "llama.cpp GPU embedding backend selected backend=CUDA\n"
        for version in ("12.4", "13.1"):
            message = selected + f'llama.cpp CUDA backend library selected cuda_version="{version}"'
            cuda_cli.cuda_evidence(message, "gguf", version)
            with self.assertRaises(AssertionError):
                cuda_cli.cuda_evidence(selected, "gguf", version)
            with self.assertRaises(AssertionError):
                cuda_cli.cuda_evidence(message, "gguf", "13.1" if version == "12.4" else "12.4")

    def test_gguf_requires_cuda_not_just_any_gpu(self):
        message = "llama.cpp GPU embedding backend selected backend=CUDA gpu=Tesla T4"
        self.assertEqual(cuda_cli.cuda_evidence(message, "gguf"), [message])
        with self.assertRaises(AssertionError):
            cuda_cli.cuda_evidence(message.replace("CUDA", "Vulkan"), "gguf")

    def test_onnx_requires_nonzero_cuda_node_placement(self):
        selected = "Transformers ONNX cuda embedding execution provider selected\n"
        nodes = "Node(s) placed on [CUDAExecutionProvider]. Number of nodes: 123"
        self.assertEqual(cuda_cli.cuda_evidence(selected + nodes, "onnx"), [nodes])
        for invalid in (selected, selected + nodes.replace("123", "0"),
                        selected + nodes.replace("CUDAExecutionProvider", "CPUExecutionProvider"), nodes):
            with self.subTest(invalid=invalid), self.assertRaises(AssertionError):
                cuda_cli.cuda_evidence(invalid, "onnx")

    def test_fallback_after_selection_is_rejected(self):
        for backend, selected in (
            ("gguf", "llama.cpp GPU embedding backend selected backend=CUDA"),
            ("onnx", "Transformers ONNX cuda embedding execution provider selected\n"
             "All nodes placed on [CUDAExecutionProvider]. Number of nodes: 12"),
        ):
            with self.subTest(backend=backend), self.assertRaises(AssertionError):
                cuda_cli.cuda_evidence(selected + "\nInference failed, falling back to CPU", backend)


if __name__ == "__main__":
    unittest.main()

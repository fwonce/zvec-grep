#!/usr/bin/env python3
"""Verify CUDA runtime packaging without network access or GPU hardware."""

import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("prepare-onnxruntime-cuda.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def archive(self, root, platform, omit=None, duplicate=False):
        names = [*runtime.required_files(platform), "LICENSE", "ThirdPartyNotices.txt", "ignored.lib"]
        entries = [(f"release/lib/{name}", name.encode()) for name in names if name != omit]
        if duplicate:
            entries.append((f"another/{names[0]}", b"duplicate"))
        path = root / ("runtime.zip" if platform == "win32" else "runtime.tgz")
        if platform == "win32":
            with zipfile.ZipFile(path, "w") as package:
                for name, data in entries:
                    package.writestr(name, data)
        else:
            with tarfile.open(path, "w:gz") as package:
                for name, data in entries:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    package.addfile(info, io.BytesIO(data))
                info = tarfile.TarInfo("release/lib/libonnxruntime.so")
                info.type = tarfile.SYMTYPE
                info.linkname = f"libonnxruntime.so.{runtime.VERSION}"
                package.addfile(info)
        return path

    def test_both_platforms_include_only_canonical_runtime_and_notices(self):
        for platform in runtime.DISTRIBUTIONS:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.archive(root, platform)
                destination = root / "bin"
                runtime.extract_runtime(archive, destination, platform, runtime.digest(archive))
                files = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
                self.assertEqual(files, {*runtime.required_files(platform).values(),
                                        "licenses/onnxruntime-LICENSE", "licenses/onnxruntime-ThirdPartyNotices.txt"})
                self.assertFalse(any(p.is_symlink() for p in destination.rglob("*")))

    def test_rejects_hash_mismatch_missing_and_duplicate_before_publishing(self):
        for platform in runtime.DISTRIBUTIONS:
            for failure in ("hash", "missing", "duplicate"):
                with self.subTest(platform=platform, failure=failure), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    archive = self.archive(root, platform, omit="LICENSE" if failure == "missing" else None,
                                           duplicate=failure == "duplicate")
                    destination = root / "bin"
                    checksum = "bad" if failure == "hash" else runtime.digest(archive)
                    with self.assertRaises(RuntimeError):
                        runtime.extract_runtime(archive, destination, platform, checksum)
                    self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()

"""Prove native stack collection works before spending time on GPU setup."""
import os
from pathlib import Path
import re
import subprocess
import sys

logs = Path("gpu-logs/diagnostic-preflight")
logs.mkdir(parents=True, exist_ok=True)
with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"]) as child:
    try:
        with (logs / "cdb-smoke.txt").open("w", encoding="utf-8") as output:
            result = subprocess.run([os.environ["ZG_CDB"], "-pv", "-p", str(child.pid),
                                     "-y", "", "-c", "~* kb; lm; q"], stdout=output,
                                    stderr=subprocess.STDOUT, timeout=60, check=False)
        text = (logs / "cdb-smoke.txt").read_text(errors="replace")
        assert result.returncode == 0 and re.search(r"Child-SP|RetAddr|Call Site", text), text[-6000:]
        print("CDB captured live native thread stacks successfully")
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)

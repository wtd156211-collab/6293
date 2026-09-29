#!/usr/bin/env python3
"""Acceptance check: run fill.py on every sample and byte-compare with
samples/expected (README section 5, items 1-2). Exit 0 when all match."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    failures = 0
    for spec in sorted((ROOT / "samples" / "polygons").glob("*.json")):
        name = spec.stem
        expected_path = ROOT / "samples" / "expected" / f"{name}.txt"
        expected = expected_path.read_bytes()
        proc = subprocess.run(
            [sys.executable, "fill.py", str(spec)],
            capture_output=True, cwd=ROOT,
        )
        if proc.returncode != 0:
            print(f"FAIL {name}: exit code {proc.returncode}: "
                  f"{proc.stderr.decode('utf-8', 'replace').strip()}")
            failures += 1
            continue
        if proc.stdout != expected:
            print(f"FAIL {name}: stdout differs from {expected_path.name}")
            failures += 1
            continue
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp_path = tmp.name
        proc = subprocess.run(
            [sys.executable, "fill.py", str(spec), "--out", tmp_path],
            capture_output=True, cwd=ROOT,
        )
        out_bytes = Path(tmp_path).read_bytes() if proc.returncode == 0 else b""
        Path(tmp_path).unlink(missing_ok=True)
        if proc.returncode != 0 or out_bytes != expected:
            print(f"FAIL {name}: --out file differs from stdout/expected")
            failures += 1
            continue
        print(f"ok   {name}")
    if failures:
        print(f"{failures} sample(s) failed")
        return 1
    print("all samples match")
    return 0


if __name__ == "__main__":
    sys.exit(main())

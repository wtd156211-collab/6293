#!/usr/bin/env python3
"""验收第 5 节第 1、2 项：逐样例运行 fill.py 并与 samples/expected 逐字节比对。

用法：python3 check_samples.py
退出码 0 表示全部一致；任一不符退出码 1。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    failures = 0
    for json_path in sorted((ROOT / "samples" / "polygons").glob("*.json")):
        name = json_path.stem
        expected = (ROOT / "samples" / "expected" / (name + ".txt")).read_bytes()

        proc = subprocess.run([sys.executable, str(ROOT / "fill.py"),
                               str(json_path)], capture_output=True, check=False)
        ok = proc.returncode == 0 and proc.stdout == expected

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            out_path = Path(fh.name)
        try:
            proc2 = subprocess.run(
                [sys.executable, str(ROOT / "fill.py"), str(json_path),
                 "--out", str(out_path)], capture_output=True, check=False)
            ok_out = proc2.returncode == 0 and out_path.read_bytes() == expected
        finally:
            out_path.unlink(missing_ok=True)

        status = "OK" if (ok and ok_out) else "FAIL"
        if not (ok and ok_out):
            failures += 1
        print("%-24s stdout:%s --out:%s" % (name, "OK" if ok else "FAIL",
                                            "OK" if ok_out else "FAIL"), status)
    print("all samples match" if failures == 0 else "%d sample(s) mismatch" % failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

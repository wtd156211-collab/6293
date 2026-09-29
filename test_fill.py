#!/usr/bin/env python3
"""fill.py 的 unittest 测试：样例逐字节、CLI 行为、规则口径、确定性。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fill  # noqa: E402

POLY_DIR = ROOT / "samples" / "polygons"
EXPECTED_DIR = ROOT / "samples" / "expected"
SAMPLE_NAMES = sorted(p.stem for p in POLY_DIR.glob("*.json"))


def run_cli(*args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / "fill.py"), *args],
        capture_output=True, text=True, env=env, check=False)


def write_tmp(spec):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    return path


class SampleTests(unittest.TestCase):
    """验收第 1、2 项：每个样例退出码 0，输出与 expected 逐字节一致。"""

    def test_samples_byte_identical(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                proc = run_cli(str(POLY_DIR / (name + ".json")))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                expected = (EXPECTED_DIR / (name + ".txt")).read_bytes()
                self.assertEqual(proc.stdout.encode("utf-8"), expected)

    def test_out_flag_byte_identical(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
                    out_path = fh.name
                try:
                    proc = run_cli(str(POLY_DIR / (name + ".json")),
                                   "--out", out_path)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    expected = (EXPECTED_DIR / (name + ".txt")).read_bytes()
                    self.assertEqual(Path(out_path).read_bytes(), expected)
                finally:
                    os.unlink(out_path)


class ErrorTests(unittest.TestCase):
    """参数、JSON、坐标、规则、画布不合法时：stderr 一行 error:，退出码 2。"""

    def assert_error(self, *args):
        proc = run_cli(*args)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1)
        return proc

    def test_no_args(self):
        self.assert_error()

    def test_too_many_args(self):
        self.assert_error("a.json", "b.json")

    def test_missing_file(self):
        self.assert_error("/nonexistent/contours.json")

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            self.assert_error(path)
        finally:
            os.unlink(path)

    def test_bad_specs(self):
        base = {"canvas": [8, 8], "fill_rule": "even-odd",
                "contours": [[[0, 0], [4, 0], [4, 4]]]}
        cases = [
            {"canvas": [0, 8], "fill_rule": "even-odd", "contours": base["contours"]},
            {"canvas": [8, 4097], "fill_rule": "even-odd", "contours": base["contours"]},
            {"canvas": [8.0, 8], "fill_rule": "even-odd", "contours": base["contours"]},
            {"canvas": [8], "fill_rule": "even-odd", "contours": base["contours"]},
            {"canvas": [8, 8], "fill_rule": "winding", "contours": base["contours"]},
            {"canvas": [8, 8], "fill_rule": "even-odd", "contours": []},
            {"canvas": [8, 8], "fill_rule": "even-odd",
             "contours": [[[0, 0], [4, 0]]]},
            {"canvas": [8, 8], "fill_rule": "even-odd",
             "contours": [[[0, 0], [1.5, 2], [3, 0]]]},
            {"canvas": [8, 8], "fill_rule": "even-odd",
             "contours": [[[0, 0], [True, 2], [3, 0]]]},
            {"canvas": [8, 8], "fill_rule": "even-odd",
             "contours": [[[0, 0], [1, 2, 3], [3, 0]]]},
        ]
        for spec in cases:
            with self.subTest(spec=spec):
                path = write_tmp(spec)
                try:
                    self.assert_error(path)
                finally:
                    os.unlink(path)

    def test_error_writes_no_output_file(self):
        path = write_tmp({"canvas": [8, 8], "fill_rule": "bad",
                          "contours": [[[0, 0], [1, 2], [3, 0]]]})
        out = path + ".out"
        try:
            proc = run_cli(path, "--out", out)
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(os.path.exists(out))
        finally:
            os.unlink(path)


class DeterminismTests(unittest.TestCase):
    """同一输入连跑、不同 PYTHONHASHSEED，输出逐字节一致。"""

    def test_hashseed_invariance(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                outputs = set()
                for seed in ("0", "1", "2"):
                    proc = run_cli(str(POLY_DIR / (name + ".json")),
                                   env_extra={"PYTHONHASHSEED": seed})
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    outputs.add(proc.stdout)
                self.assertEqual(len(outputs), 1)


class EquivalenceTests(unittest.TestCase):
    """验收第 4 项：even-odd 结果不随起点、顺序、走向变化。"""

    @staticmethod
    def rotated(points, k):
        return points[k:] + points[:k]

    def variants(self, contours):
        yield contours
        yield [self.rotated(c, 1) for c in contours]
        yield [self.rotated(c, 2) for c in contours]
        yield [list(reversed(c)) for c in contours]
        yield list(reversed(contours))

    def test_even_odd_invariant(self):
        for name in ("01-concave", "05-scanline-vertices", "06-horizontal-edges"):
            spec = json.loads((POLY_DIR / (name + ".json")).read_text("utf-8"))
            self.assertEqual(spec["fill_rule"], "even-odd")
            reference = None
            for variant in self.variants(spec["contours"]):
                with self.subTest(sample=name, variant=id(variant)):
                    path = write_tmp({**spec, "contours": variant})
                    try:
                        proc = run_cli(path)
                    finally:
                        os.unlink(path)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    if reference is None:
                        reference = proc.stdout
                    else:
                        self.assertEqual(proc.stdout, reference)

    def test_nonzero_reverse_inner_digs_hole(self):
        outer = [[0, 0], [8, 0], [8, 8], [0, 8]]
        inner = [[2, 2], [6, 2], [6, 6], [2, 6]]
        results = {}
        for tag, contours in (("same", [outer, inner]),
                              ("reversed", [outer, list(reversed(inner))])):
            path = write_tmp({"canvas": [10, 10], "fill_rule": "nonzero",
                              "contours": contours})
            try:
                proc = run_cli(path)
            finally:
                os.unlink(path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            results[tag] = proc.stdout
        self.assertIn("filled 64", results["same"])
        self.assertIn("filled 48", results["reversed"])


class RuleTests(unittest.TestCase):
    """第 2 节口径的单元级核对：顶点压线、水平边、边界像素、裁剪。"""

    @staticmethod
    def spans(width, height, rule, contours):
        return fill.fill_rows(width, height, rule, contours)

    def test_boundary_pixels_left_closed_right_open(self):
        # 正方形 [2,6) x [2,6)：像素 2..5 填，6 不填
        rows = self.spans(10, 10, "even-odd",
                          [[(2, 2), (6, 2), (6, 6), (2, 6)]])
        self.assertEqual(rows, [(j, [(2, 5)]) for j in range(2, 6)])

    def test_vertex_on_scanline_extreme(self):
        # 朝下的尖锋顶点 (2,2)：不多填也不少填行
        rows = self.spans(6, 6, "even-odd",
                          [[(0, 0), (4, 0), (2, 2)]])
        self.assertEqual(rows, [(0, [(0, 3)]), (1, [(1, 2)])])

    def test_vertex_on_scanline_saddle(self):
        # 顶点 (2,1) 是两条边的公共端点且非极值：只过一次，行不间断
        rows = self.spans(6, 6, "even-odd",
                          [[(0, 0), (4, 0), (2, 1), (4, 2), (0, 2)]])
        self.assertEqual([y for y, _ in rows], [0, 1])
        # j=0 由下行边 (4,0)->(2,1) 给出 x=4；j=1 由 (2,1)->(4,2) 给出 x=2
        self.assertEqual(rows, [(0, [(0, 3)]), (1, [(0, 1)])])

    def test_horizontal_edge_one_pixel_bar(self):
        # 一像素高横条：水平边不产生交点，只填 y=2 一行
        rows = self.spans(30, 5, "even-odd",
                          [[(3, 2), (24, 2), (24, 3), (3, 3)]])
        self.assertEqual(rows, [(2, [(3, 23)])])

    def test_degenerate_and_collinear_points(self):
        # 重复点、共线点不改变结果
        plain = self.spans(10, 10, "even-odd",
                           [[(2, 2), (6, 2), (6, 6), (2, 6)]])
        messy = self.spans(10, 10, "even-odd",
                           [[(2, 2), (4, 2), (6, 2), (6, 2), (6, 6), (2, 6)]])
        self.assertEqual(plain, messy)

    def test_clipping_partial_and_full(self):
        # 部分越界：裁到画布内
        rows = self.spans(4, 4, "even-odd",
                          [[(-2, -2), (3, -2), (3, 3), (-2, 3)]])
        self.assertEqual(rows, [(j, [(0, 2)]) for j in range(3)])
        # 完全在外：没有填充行
        rows = self.spans(4, 4, "even-odd",
                          [[(10, 10), (12, 10), (12, 12), (10, 12)]])
        self.assertEqual(rows, [])

    def test_even_odd_pairs_coincident_intersections(self):
        # 两个矩形共边：共边处交点重合，even-odd 下抵消成一条整段
        rows = self.spans(20, 6, "even-odd",
                          [[(3, 1), (11, 1), (11, 5), (3, 5)],
                           [(11, 1), (19, 1), (19, 5), (11, 5)]])
        self.assertEqual(rows, [(j, [(3, 18)]) for j in range(1, 5)])

    def test_nonzero_weights_merge_at_same_x(self):
        # 同向嵌套：内层权重叠加仍填；星形中心 even-odd 空、nonzero 填
        spec = json.loads((POLY_DIR / "03-pentagram-evenodd.json")
                          .read_text("utf-8"))
        contours = [[tuple(p) for p in c] for c in spec["contours"]]
        w, h = spec["canvas"]
        eo = self.spans(w, h, "even-odd", contours)
        nz = self.spans(w, h, "nonzero", contours)
        count = lambda rows: sum(b - a + 1 for _, spans in rows
                                 for a, b in spans)
        self.assertGreater(count(nz), count(eo))


class HashTests(unittest.TestCase):
    """hash 行是对所有 row 行（含 LF）的 FNV-1a 64。"""

    def test_hash_recomputable(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                text = (EXPECTED_DIR / (name + ".txt")).read_text("utf-8")
                lines = text.splitlines()
                row_lines = [l for l in lines if l.startswith("row ")]
                hash_line = [l for l in lines if l.startswith("hash ")][0]
                expect = "hash 0x%016x" % fill.fnv1a_rows(row_lines)
                self.assertEqual(hash_line, expect)

    def test_filled_matches_span_sum(self):
        for name in SAMPLE_NAMES:
            with self.subTest(sample=name):
                text = (EXPECTED_DIR / (name + ".txt")).read_text("utf-8")
                lines = text.splitlines()
                filled = int([l for l in lines
                              if l.startswith("filled ")][0].split()[1])
                total = 0
                for line in lines:
                    if line.startswith("row "):
                        for tok in line.split()[2:]:
                            a, b = tok.split("-")
                            total += int(b) - int(a) + 1
                self.assertEqual(filled, total)


if __name__ == "__main__":
    unittest.main()

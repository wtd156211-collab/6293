#!/usr/bin/env python3
"""unittest suite for the scanline fill engine (fill.py) and viewer build."""

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fill  # noqa: E402
import build_viewer  # noqa: E402

POLYGONS_DIR = ROOT / "samples" / "polygons"
EXPECTED_DIR = ROOT / "samples" / "expected"


def run_cli(*args, env_extra=None, cwd=ROOT):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "fill.py", *args],
        capture_output=True, cwd=cwd, env=env,
    )


def reference_pixels(width, height, rule, contours):
    """Independent per-pixel reference: README 2.4 equivalence.

    Pixel i on row j is filled iff the number of intersections with
    x <= i is odd (even-odd) / the summed weights are nonzero (nonzero).
    All comparisons use exact integer cross-multiplication.
    """
    edges = fill.build_edges(contours)
    pixels = set()
    for j in range(height):
        events = []
        for x1, y1, x2, y2, w in edges:
            if min(y1, y2) <= j < max(y1, y2):
                den = y2 - y1
                num = x1 * den + (x2 - x1) * (j - y1)
                if den < 0:
                    num, den = -num, -den
                events.append((num, den, w))
        for i in range(width):
            count = 0
            winding = 0
            for num, den, w in events:
                if num <= i * den:  # x <= i, exact
                    count += 1
                    winding += w
            inside = (count % 2 == 1) if rule == "even-odd" else (winding != 0)
            if inside:
                pixels.add((i, j))
    return pixels


def result_pixels(result):
    return {(i, y) for y, segments in result.rows for a, b in segments for i in range(a, b + 1)}


class SampleTests(unittest.TestCase):
    """Every shipped sample must match its expected file byte for byte."""

    def test_samples_match_expected(self):
        for spec in sorted(POLYGONS_DIR.glob("*.json")):
            with self.subTest(sample=spec.stem):
                width, height, rule, contours = fill.load_spec(str(spec))
                result = fill.fill_contours(width, height, rule, contours)
                expected = (EXPECTED_DIR / f"{spec.stem}.txt").read_text(encoding="utf-8")
                self.assertEqual(result.render(), expected)

    def test_cli_stdout_and_out_file(self):
        spec = POLYGONS_DIR / "05-scanline-vertices.json"
        expected = (EXPECTED_DIR / "05-scanline-vertices.txt").read_bytes()
        proc = run_cli(str(spec))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, expected)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            proc = run_cli(str(spec), "--out", tmp_path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"")
            self.assertEqual(Path(tmp_path).read_bytes(), expected)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_deterministic_across_hash_seeds(self):
        spec = str(POLYGONS_DIR / "03-pentagram-evenodd.json")
        outputs = [
            run_cli(spec, env_extra={"PYTHONHASHSEED": seed}).stdout
            for seed in ("0", "1", "2")
        ]
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])


class CliErrorTests(unittest.TestCase):
    def assert_cli_error(self, *args):
        proc = run_cli(*args)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.decode("utf-8", "replace").startswith("error:"))
        self.assertEqual(proc.stdout, b"")

    def test_bad_argument_counts(self):
        self.assert_cli_error()
        self.assert_cli_error("a.json", "b.json")
        self.assert_cli_error("a.json", "--out")

    def test_missing_file(self):
        self.assert_cli_error("no-such-file.json")

    def test_invalid_documents(self):
        bad_docs = [
            "not json",
            "[1, 2]",
            {"canvas": [0, 4], "fill_rule": "even-odd", "contours": [[[0, 0], [1, 0], [1, 1]]]},
            {"canvas": [4, 4097], "fill_rule": "even-odd", "contours": [[[0, 0], [1, 0], [1, 1]]]},
            {"canvas": [4], "fill_rule": "even-odd", "contours": [[[0, 0], [1, 0], [1, 1]]]},
            {"canvas": [4, 4], "fill_rule": "winding", "contours": [[[0, 0], [1, 0], [1, 1]]]},
            {"canvas": [4, 4], "fill_rule": "even-odd", "contours": []},
            {"canvas": [4, 4], "fill_rule": "even-odd", "contours": [[[0, 0], [1, 1]]]},
            {"canvas": [4, 4], "fill_rule": "even-odd", "contours": [[[0, 0], [1.5, 0], [1, 1]]]},
            {"canvas": [4, 4], "fill_rule": "even-odd", "contours": [[[0, 0], [True, 0], [1, 1]]]},
            {"canvas": [4, 4], "fill_rule": "even-odd", "contours": [[[0, 0], [1], [1, 1]]]},
        ]
        for doc in bad_docs:
            with self.subTest(doc=str(doc)[:60]):
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".json", delete=False, encoding="utf-8") as tmp:
                    if isinstance(doc, str):
                        tmp.write(doc)
                    else:
                        json.dump(doc, tmp)
                    tmp_path = tmp.name
                try:
                    self.assert_cli_error(tmp_path)
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

    def test_no_output_file_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "out.txt"
            proc = run_cli("no-such-file.json", "--out", str(out_path))
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(out_path.exists())


class GeometryTests(unittest.TestCase):
    def fill(self, width, height, rule, contours):
        return fill.fill_contours(width, height, rule, contours)

    def test_vertex_on_scanline_diamond(self):
        # Extremum vertices at j=0 and j=4 produce empty rows; the row
        # through the left/right vertices is a single segment.
        result = self.fill(8, 8, "even-odd", [[(0, 2), (2, 0), (4, 2), (2, 4)]])
        self.assertEqual(result.rows, [(1, [(1, 2)]), (2, [(0, 3)]), (3, [(1, 2)])])

    def test_vertex_on_scanline_triangle(self):
        result = self.fill(10, 10, "even-odd", [[(2, 2), (6, 2), (4, 6)]])
        self.assertEqual(result.rows, [
            (2, [(2, 5)]), (3, [(3, 5)]), (4, [(3, 4)]), (5, [(4, 4)]),
        ])

    def test_horizontal_and_degenerate_edges_ignored(self):
        contour = [(1, 1), (5, 1), (5, 1), (9, 1), (9, 4), (1, 4)]
        result = self.fill(12, 6, "even-odd", [contour])
        self.assertEqual(result.rows, [(1, [(1, 8)]), (2, [(1, 8)]), (3, [(1, 8)])])

    def test_adjacent_segments_merge(self):
        # Two rectangles sharing the edge x=8 must fuse into one segment.
        result = self.fill(20, 8, "even-odd", [
            [(2, 2), (8, 2), (8, 6), (2, 6)],
            [(8, 2), (14, 2), (14, 6), (8, 6)],
        ])
        self.assertEqual(result.rows, [(j, [(2, 13)]) for j in range(2, 6)])

    def test_clipping(self):
        # Fully outside: nothing. Partially outside: clipped to canvas.
        result = self.fill(4, 4, "even-odd", [[(-10, -10), (-5, -10), (-5, -5)]])
        self.assertEqual(result.rows, [])
        self.assertEqual(result.filled, 0)
        result = self.fill(4, 4, "even-odd", [[(-2, -2), (3, -2), (3, 3), (-2, 3)]])
        self.assertEqual(result.rows, [(0, [(0, 2)]), (1, [(0, 2)]), (2, [(0, 2)])])

    def test_exact_rational_boundaries(self):
        # The second intersection sits at x = 100 + 1e-16, which float64
        # rounds to exactly 100.0; exact arithmetic must keep pixel 100 empty.
        big = 10 ** 16
        contours = [
            [(100, -1), (100, 1), (200, 1), (200, -1)],
            [(0, -1), (100 * big + 1, big - 1), (0, big - 1)],
        ]
        result = self.fill(210, 1, "even-odd", contours)
        self.assertEqual(result.rows, [(0, [(0, 99), (101, 199)])])

    def test_huge_coordinates_clip_cleanly(self):
        contours = [[(0, 0), (2 ** 60, 1), (0, 2)]]
        result = self.fill(4, 4, "even-odd", contours)
        self.assertEqual(result.rows, [(1, [(0, 3)])])

    def test_even_odd_invariant_to_contour_transforms(self):
        contours = [
            [(2, 2), (14, 2), (14, 6), (7, 6), (7, 13), (2, 13)],
            [(4, 4), (10, 4), (10, 10), (4, 10)],
        ]
        base = self.fill(20, 16, "even-odd", contours)
        rotated = [points[2:] + points[:2] for points in contours[::-1]]
        reversed_contours = [list(reversed(points)) for points in contours]
        for variant in (rotated, reversed_contours):
            self.assertEqual(self.fill(20, 16, "even-odd", variant).rows, base.rows)

    def test_nonzero_direction_matters(self):
        outer = [(0, 0), (12, 0), (12, 12), (0, 12)]
        inner = [(3, 3), (9, 3), (9, 9), (3, 9)]
        same = self.fill(12, 12, "nonzero", [outer, inner])
        self.assertEqual(same.filled, 144)
        holed = self.fill(12, 12, "nonzero", [outer, list(reversed(inner))])
        self.assertEqual(holed.filled, 108)
        self.assertEqual(holed.rows[3], (3, [(0, 2), (9, 11)]))
        # Reversing a single contour negates all weights; c != 0 is unchanged.
        solo = self.fill(12, 12, "nonzero", [outer])
        solo_rev = self.fill(12, 12, "nonzero", [list(reversed(outer))])
        self.assertEqual(solo.rows, solo_rev.rows)


class DifferentialTests(unittest.TestCase):
    """Random small inputs checked against the per-pixel reference."""

    def random_case(self, rng):
        width = rng.randint(1, 12)
        height = rng.randint(1, 12)
        contours = []
        for _ in range(rng.randint(1, 3)):
            count = rng.randint(3, 8)
            contours.append([
                (rng.randint(-3, width + 3), rng.randint(-3, height + 3))
                for _ in range(count)
            ])
        return width, height, contours

    def test_against_per_pixel_reference(self):
        rng = random.Random(20260929)
        for case in range(300):
            width, height, contours = self.random_case(rng)
            for rule in ("even-odd", "nonzero"):
                with self.subTest(case=case, rule=rule):
                    result = fill.fill_contours(width, height, rule, contours)
                    self.assertEqual(
                        result_pixels(result),
                        reference_pixels(width, height, rule, contours),
                    )

    def test_even_odd_invariance_randomized(self):
        rng = random.Random(77003)
        for case in range(100):
            width, height, contours = self.random_case(rng)
            base = fill.fill_contours(width, height, "even-odd", contours)
            shuffled = contours[:]
            rng.shuffle(shuffled)
            rotated = []
            for points in shuffled:
                k = rng.randrange(len(points))
                rotated.append(points[k:] + points[:k])
            variants = [
                shuffled,
                [list(reversed(points)) for points in shuffled],
                rotated,
            ]
            for variant in variants:
                with self.subTest(case=case):
                    self.assertEqual(
                        fill.fill_contours(width, height, "even-odd", variant).rows,
                        base.rows,
                    )


class OutputFormatTests(unittest.TestCase):
    def test_render_layout_and_hash(self):
        result = fill.fill_contours(6, 6, "even-odd", [[(1, 1), (5, 1), (5, 5), (1, 5)]])
        text = result.render()
        lines = text.split("\n")
        self.assertEqual(lines[0], "format 1")
        self.assertEqual(lines[1], "canvas 6 6")
        self.assertEqual(lines[2], "rule even-odd")
        self.assertEqual(lines[3], "filled 16")
        self.assertEqual(lines[4:8], [f"row {j} 1-4" for j in range(1, 5)])
        self.assertRegex(lines[8], r"^hash 0x[0-9a-f]{16}$")
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_fnv_of_empty_rows_is_offset_basis(self):
        result = fill.FillResult(4, 4, "even-odd", [])
        self.assertEqual(fill.fnv1a_64(result.row_lines()), "0xcbf29ce484222325")
        self.assertIn("hash 0xcbf29ce484222325", result.render())


class ViewerTests(unittest.TestCase):
    def test_payload_matches_engine(self):
        samples = build_viewer.collect_samples()
        self.assertEqual(len(samples), len(list(POLYGONS_DIR.glob("*.json"))))
        for sample in samples:
            with self.subTest(sample=sample["name"]):
                total = sum(b - a + 1 for _, segs in sample["rows"] for a, b in segs)
                self.assertEqual(total, sample["filled"])
                self.assertGreaterEqual(sample["elapsedMs"], 0)

    def test_html_is_self_contained(self):
        html = build_viewer.build_html(build_viewer.collect_samples())
        self.assertIn("data-pixel", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("src=", html)
        self.assertIn("?sample=", html)


@unittest.skipUnless(os.environ.get("FILL_RUN_PERF"), "perf test: set FILL_RUN_PERF=1")
class PerfTests(unittest.TestCase):
    def test_budget_input(self):
        # 4096 x 4096 canvas, 20000 edges, average span ~13 rows (README 5).
        rng = random.Random(4096)
        points = []
        x, y = 2048, 2048
        for _ in range(20000):
            x = rng.randrange(0, 4096)
            y += rng.randint(-20, 20)
            points.append((x, y))
        import time
        started = time.perf_counter()
        result = fill.fill_contours(4096, 4096, "even-odd", [points])
        elapsed = time.perf_counter() - started
        print(f"\nperf: 20000 edges on 4096x4096 in {elapsed:.2f}s, "
              f"filled={result.filled}")
        self.assertLess(elapsed, 10.0)
        again = fill.fill_contours(4096, 4096, "even-odd", [points])
        self.assertEqual(result.rows, again.rows)


if __name__ == "__main__":
    unittest.main()

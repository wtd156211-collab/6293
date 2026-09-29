#!/usr/bin/env python3
"""Scanline polygon fill engine.

Implements the spec in README.md: half-open scanline intersection
("lower closed, upper open"), exact rational intersections, even-odd and
nonzero fill rules, left-closed/right-open pixel coverage, canvas clipping
at output time only.

Usable both as a library (``fill_contours``) and as a CLI:

    python3 fill.py <contours.json> [--out <file>]
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

FORMAT_VERSION = 1
RULES = ("even-odd", "nonzero")
MIN_CANVAS = 1
MAX_CANVAS = 4096

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
FNV_MASK = 0xFFFFFFFFFFFFFFFF


class InputError(ValueError):
    """Raised when the input document violates the spec."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_spec(doc):
    """Validate a decoded JSON document.

    Returns ``(width, height, rule, contours)`` where contours is a list of
    lists of ``(x, y)`` integer tuples.
    """
    if not isinstance(doc, dict):
        raise InputError("top level must be a JSON object")
    canvas = doc.get("canvas")
    if not isinstance(canvas, list) or len(canvas) != 2 or not all(_is_int(v) for v in canvas):
        raise InputError("canvas must be two integers [W, H]")
    width, height = canvas
    if not (MIN_CANVAS <= width <= MAX_CANVAS and MIN_CANVAS <= height <= MAX_CANVAS):
        raise InputError(f"canvas dimensions must be within {MIN_CANVAS}..{MAX_CANVAS}")
    rule = doc.get("fill_rule")
    if rule not in RULES:
        raise InputError('fill_rule must be "even-odd" or "nonzero"')
    raw_contours = doc.get("contours")
    if not isinstance(raw_contours, list) or not raw_contours:
        raise InputError("contours must be a non-empty list of contours")
    contours = []
    for index, raw in enumerate(raw_contours):
        if not isinstance(raw, list) or len(raw) < 3:
            raise InputError(f"contour {index} must have at least 3 points")
        points = []
        for point in raw:
            if not isinstance(point, list) or len(point) != 2 or not all(_is_int(v) for v in point):
                raise InputError(f"contour {index} has a malformed or non-integer point")
            points.append((point[0], point[1]))
        contours.append(points)
    return width, height, rule, contours


def load_spec(path):
    """Read and validate a contour JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {path}: {exc}") from exc
    return parse_spec(doc)


def build_edges(contours):
    """Flatten contours into a directed edge list ``(x1, y1, x2, y2, w)``.

    Horizontal and degenerate edges are dropped: they never intersect a
    scanline. ``w`` is +1 for downward edges, -1 for upward edges.
    """
    edges = []
    for points in contours:
        count = len(points)
        for k in range(count):
            x1, y1 = points[k]
            x2, y2 = points[(k + 1) % count]
            if y1 == y2:
                continue
            edges.append((x1, y1, x2, y2, 1 if y2 > y1 else -1))
    return edges


def _ceil_div(num, den):
    """ceil(num / den) for a positive ``den``, exact integer arithmetic."""
    return -(-num // den)


def _fix_inversions(events):
    """Repair exact-order inversions left behind by the float-keyed sort.

    The float key is only a hint; any pair it misordered must be adjacent
    (a misordered pair implies the two floats are nearly equal), so a local
    bubble pass with exact cross-multiplication restores the true order.
    """
    index = 1
    while index < len(events):
        num, den, _ = events[index]
        prev_num, prev_den, _ = events[index - 1]
        if num * prev_den < prev_num * den:
            events[index - 1], events[index] = events[index], events[index - 1]
            if index > 1:
                index -= 1
                continue
        index += 1


def _row_segments(events, rule, width):
    """Turn one scanline's intersections into clipped pixel segments.

    ``events`` is a list of ``(num, den, w)`` with ``den > 0``; the
    intersection x is the rational ``num / den``. Returns a list of
    inclusive ``(a, b)`` pixel segments in ascending, merged order.
    """
    events.sort(key=lambda event: event[0] / event[1])
    _fix_inversions(events)
    even_odd = rule == "even-odd"
    intervals = []
    start_num = start_den = None
    crossings = 0
    winding = 0
    index = 0
    total = len(events)
    while index < total:
        num, den, _ = events[index]
        end = index + 1
        while end < total and events[end][0] * den == num * events[end][1]:
            end += 1
        # Coverage interval that ends exactly at this x (zero-length
        # intervals from extremum vertices are dropped).
        inside = (crossings & 1) == 1 if even_odd else winding != 0
        if inside and num * start_den != start_num * den:
            intervals.append((start_num, start_den, num, den))
        for k in range(index, end):
            winding += events[k][2]
        crossings += end - index
        if (crossings & 1) == 1 if even_odd else winding != 0:
            start_num, start_den = num, den
        else:
            start_num = None
        index = end
    segments = []
    for a_num, a_den, b_num, b_den in intervals:
        lo = _ceil_div(a_num, a_den)
        hi = _ceil_div(b_num, b_den) - 1
        if lo < 0:
            lo = 0
        if hi >= width:
            hi = width - 1
        if lo > hi:
            continue
        if segments and lo <= segments[-1][1] + 1:
            if hi > segments[-1][1]:
                segments[-1][1] = hi
        else:
            segments.append([lo, hi])
    return [(lo, hi) for lo, hi in segments]


@dataclass
class FillResult:
    """Filled pixels as per-row inclusive segments."""

    width: int
    height: int
    rule: str
    rows: list  # list of (y, [(a, b), ...]), y ascending, segments ascending

    @property
    def filled(self):
        return sum(b - a + 1 for _, segments in self.rows for a, b in segments)

    def row_lines(self):
        lines = []
        for y, segments in self.rows:
            parts = " ".join(f"{a}-{b}" for a, b in segments)
            lines.append(f"row {y} {parts}")
        return lines

    def render(self):
        """Canonical result text (README section 4.2), LF-terminated."""
        lines = [
            f"format {FORMAT_VERSION}",
            f"canvas {self.width} {self.height}",
            f"rule {self.rule}",
            f"filled {self.filled}",
        ]
        row_lines = self.row_lines()
        lines.extend(row_lines)
        lines.append(f"hash {fnv1a_64(row_lines)}")
        return "\n".join(lines) + "\n"


def fnv1a_64(lines):
    """FNV-1a 64 over the given row lines, each fed with a trailing LF."""
    digest = FNV_OFFSET
    for line in lines:
        for byte in (line + "\n").encode("utf-8"):
            digest ^= byte
            digest = (digest * FNV_PRIME) & FNV_MASK
    return f"0x{digest:016x}"


def fill_contours(width, height, rule, contours):
    """Fill ``contours`` on a ``width`` x ``height`` canvas under ``rule``.

    Scanline sweep with a bucket-activated edge table: each edge is
    activated at the first scanline it crosses and evicted after the last,
    so per-row work is proportional to the number of active edges.
    """
    buckets = [[] for _ in range(height)]
    head = []
    for edge in build_edges(contours):
        _, y1, _, y2, _ = edge
        low = y1 if y1 < y2 else y2
        if low < 0:
            head.append(edge)
        elif low < height:
            buckets[low].append(edge)
    active = []
    rows = []
    for j in range(height):
        if head:
            active.extend(head)
            head = []
        if buckets[j]:
            active.extend(buckets[j])
        if not active:
            continue
        events = []
        kept = []
        for x1, y1, x2, y2, w in active:
            if j < (y2 if y2 > y1 else y1):
                kept.append((x1, y1, x2, y2, w))
                den = y2 - y1
                num = x1 * den + (x2 - x1) * (j - y1)
                if den < 0:
                    num, den = -num, -den
                events.append((num, den, w))
        active = kept
        if events:
            segments = _row_segments(events, rule, width)
            if segments:
                rows.append((j, segments))
    return FillResult(width, height, rule, rows)


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out_path = None
    if "--out" in args:
        pos = args.index("--out")
        if pos + 1 >= len(args):
            return _fail("usage: python3 fill.py <contours.json> [--out <file>]")
        out_path = args[pos + 1]
        del args[pos:pos + 2]
    if len(args) != 1:
        return _fail("usage: python3 fill.py <contours.json> [--out <file>]")
    try:
        width, height, rule, contours = load_spec(args[0])
    except InputError as exc:
        return _fail(str(exc))
    result = fill_contours(width, height, rule, contours)
    text = result.render()
    if out_path is not None:
        try:
            with open(out_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except OSError as exc:
            return _fail(f"cannot write {out_path}: {exc.strerror or exc}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

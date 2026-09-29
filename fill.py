#!/usr/bin/env python3
"""扫描线多边形填充：读轮廓 JSON，按 even-odd / nonzero 规则输出每行像素段。

口径（与 README 第 2 节一致）：
- 扫描线 y = j 穿过第 j 行像素中心，相交条件「下闭上开」min(y1,y2) <= j < max(y1,y2)；
- 水平边与退化边不产生交点；交点用有理数（分子/分母）表示，比较一律整数交叉相乘；
- even-odd 两两配对，nonzero 同一 x 先合并权重再结算；
- 覆盖区间左闭右开，像素段 [ceil(a), ceil(b)-1]，再裁到 [0, W-1]，相接段合并。
"""

import json
import sys
from functools import cmp_to_key

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
FNV_MASK = 0xFFFFFFFFFFFFFFFF

RULE_EVEN_ODD = "even-odd"
RULE_NONZERO = "nonzero"
MAX_CANVAS = 4096


class InputError(Exception):
    """输入文件不合法。"""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def load_spec(path):
    """读取并校验轮廓 JSON，返回 (width, height, rule, contours)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise InputError("cannot read %s: %s" % (path, exc.strerror or exc))
    except ValueError as exc:
        raise InputError("invalid JSON in %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise InputError("top-level JSON value must be an object")

    canvas = data.get("canvas")
    if not (isinstance(canvas, list) and len(canvas) == 2
            and _is_int(canvas[0]) and _is_int(canvas[1])):
        raise InputError("canvas must be a pair of integers [W, H]")
    width, height = canvas
    if not (1 <= width <= MAX_CANVAS and 1 <= height <= MAX_CANVAS):
        raise InputError("canvas dimensions must be within 1..%d" % MAX_CANVAS)

    rule = data.get("fill_rule")
    if rule not in (RULE_EVEN_ODD, RULE_NONZERO):
        raise InputError("unknown fill_rule: %r" % (rule,))

    raw_contours = data.get("contours")
    if not (isinstance(raw_contours, list) and raw_contours):
        raise InputError("contours must be a non-empty list")
    contours = []
    for ci, raw in enumerate(raw_contours):
        if not (isinstance(raw, list) and len(raw) >= 3):
            raise InputError("contour %d must have at least 3 points" % ci)
        points = []
        for pi, point in enumerate(raw):
            if not (isinstance(point, list) and len(point) == 2
                    and _is_int(point[0]) and _is_int(point[1])):
                raise InputError(
                    "contour %d point %d must be a pair of integers" % (ci, pi))
            points.append((point[0], point[1]))
        contours.append(points)
    return width, height, rule, contours


def _build_edges(contours):
    """轮廓 -> 边表。每条边 (ymin, ymax, num0, den, step, weight)。

    扫描线 y = j 上的交点 x = num(j) / den，其中 num(j) = num0 + step*(j - ymin)，
    den > 0；weight 为 nonzero 权重（向下 +1，向上 -1）。水平边、退化边不产生交点。
    """
    edges = []
    for points in contours:
        count = len(points)
        for k in range(count):
            x1, y1 = points[k]
            x2, y2 = points[(k + 1) % count]
            if y1 == y2:
                continue
            dx = x2 - x1
            dy = y2 - y1
            if dy > 0:
                edges.append((y1, y2, x1 * dy, dy, dx, 1))
            else:
                edges.append((y2, y1, -x2 * dy, -dy, -dx, -1))
    return edges


def _ceil_div(num, den):
    """ceil(num / den)，den > 0，整数运算。"""
    return -((-num) // den)


def _cmp_edges(a, b):
    """按 x = num/den 升序比较两条活性边，整数交叉相乘，不用浮点。"""
    lhs = a[0] * b[1]
    rhs = b[0] * a[1]
    if lhs < rhs:
        return -1
    if lhs > rhs:
        return 1
    return 0


def _sweep_even_odd(active, spans):
    """交点按 x 升序两两配对，[x0, x1)、[x2, x3)… 为覆盖区间。"""
    last = len(active) - 1
    for k in range(0, last, 2):
        lo = _ceil_div(active[k][0], active[k][1])
        hi = _ceil_div(active[k + 1][0], active[k + 1][1]) - 1
        if lo <= hi:
            spans.append((lo, hi))


def _sweep_nonzero(active, spans):
    """同一 x 先合并权重，累计权重 c != 0 的 x 段为覆盖区间。"""
    c = 0
    seg_num = seg_den = 0
    idx = 0
    count = len(active)
    while idx < count:
        num = active[idx][0]
        den = active[idx][1]
        group = 0
        while idx < count and active[idx][0] * den == num * active[idx][1]:
            group += active[idx][3]
            idx += 1
        if c != 0:
            lo = _ceil_div(seg_num, seg_den)
            hi = _ceil_div(num, den) - 1
            if lo <= hi:
                spans.append((lo, hi))
        c += group
        if c != 0:
            seg_num, seg_den = num, den


def _clip_merge(spans, width):
    """像素段裁到 [0, width-1]，首尾相接的段合并。spans 已按 a 升序。"""
    out = []
    hi_max = width - 1
    for lo, hi in spans:
        if lo < 0:
            lo = 0
        if hi > hi_max:
            hi = hi_max
        if lo > hi:
            continue
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1][1] = hi
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def fill_rows(width, height, rule, contours):
    """扫描线主循环，返回 [(y, [(a, b), ...]), ...]，段为闭区间、按行/列升序。"""
    buckets = [[] for _ in range(height)]
    for ymin, ymax, num0, den, step, weight in _build_edges(contours):
        if ymax <= 0 or ymin >= height:
            continue
        buckets[max(ymin, 0)].append((ymin, ymax, num0, den, step, weight))

    nonzero = rule == RULE_NONZERO
    rows = []
    active = []
    for j in range(height):
        for ymin, ymax, num0, den, step, weight in buckets[j]:
            active.append([num0 + step * (j - ymin), den, step, weight, ymax])
        if not active:
            continue
        active.sort(key=cmp_to_key(_cmp_edges))
        spans = []
        if nonzero:
            _sweep_nonzero(active, spans)
        else:
            _sweep_even_odd(active, spans)
        clipped = _clip_merge(spans, width) if spans else []
        if clipped:
            rows.append((j, clipped))
        nxt = []
        for edge in active:
            if edge[4] > j + 1:
                edge[0] += edge[2]
                nxt.append(edge)
        active = nxt
    return rows


def fnv1a_rows(row_lines):
    """FNV-1a 64，按顺序吃下所有 row 行（含行尾 LF、UTF-8）。"""
    h = FNV_OFFSET
    for line in row_lines:
        for byte in (line + "\n").encode("utf-8"):
            h = ((h ^ byte) * FNV_PRIME) & FNV_MASK
    return h


def render(width, height, rule, rows):
    """组装 4.2 节规范输出文本（LF 行尾，末行有换行）。"""
    row_lines = []
    filled = 0
    for y, spans in rows:
        parts = ["row %d" % y]
        for a, b in spans:
            parts.append("%d-%d" % (a, b))
            filled += b - a + 1
        row_lines.append(" ".join(parts))
    lines = [
        "format 1",
        "canvas %d %d" % (width, height),
        "rule %s" % rule,
        "filled %d" % filled,
    ]
    lines.extend(row_lines)
    lines.append("hash 0x%016x" % fnv1a_rows(row_lines))
    return "\n".join(lines) + "\n"


def main(argv):
    args = argv[1:]
    out_path = None
    if len(args) == 1:
        src = args[0]
    elif len(args) == 3 and args[1] == "--out":
        src, out_path = args[0], args[2]
    else:
        sys.stderr.write("error: usage: fill.py <contours.json> [--out <file>]\n")
        return 2
    try:
        width, height, rule, contours = load_spec(src)
    except InputError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    text = render(width, height, rule, fill_rows(width, height, rule, contours))
    if out_path is None:
        sys.stdout.write(text)
    else:
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

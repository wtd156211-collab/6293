#!/usr/bin/env python3
"""生成自包含页面 viewer.html。

对 samples/polygons/*.json 逐个运行 fill.py（计时，冷启动计入），把轮廓与
4.2 节规范输出（行段、filled、hash、耗时）内联进页面；页面只画结果，不另算规则。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLY_DIR = ROOT / "samples" / "polygons"


def run_engine(json_path):
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "fill.py"), str(json_path)],
        capture_output=True, text=True, check=False)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if proc.returncode != 0:
        raise SystemExit("fill.py failed on %s: %s" % (json_path, proc.stderr))
    return proc.stdout, elapsed_ms


def parse_output(text):
    info = {"rows": []}
    for line in text.splitlines():
        parts = line.split()
        if parts[0] == "canvas":
            info["canvas"] = [int(parts[1]), int(parts[2])]
        elif parts[0] == "rule":
            info["rule"] = parts[1]
        elif parts[0] == "filled":
            info["filled"] = int(parts[1])
        elif parts[0] == "hash":
            info["hash"] = parts[1]
        elif parts[0] == "row":
            spans = []
            for tok in parts[2:]:
                a, b = tok.split("-")
                spans.append([int(a), int(b)])
            info["rows"].append([int(parts[1]), spans])
    return info


def collect_samples():
    samples = {}
    for json_path in sorted(POLY_DIR.glob("*.json")):
        spec = json.loads(json_path.read_text(encoding="utf-8"))
        text, elapsed_ms = run_engine(json_path)
        info = parse_output(text)
        info["contours"] = spec["contours"]
        info["elapsed_ms"] = round(elapsed_ms, 1)
        samples[json_path.stem] = info
    return samples


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>扫描线填充核对视图</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 16px; color: #222; }
  h1 { font-size: 18px; margin: 0 0 8px; }
  .bar { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  .stats { font-size: 14px; line-height: 1.7; }
  .stats b { font-variant-numeric: tabular-nums; }
  .ok { color: #0a7d2c; } .bad { color: #c00; }
  svg { border: 1px solid #ccc; background: #fff; image-rendering: pixelated; }
  .legend { font-size: 12px; color: #666; margin-top: 6px; }
  .swatch { display: inline-block; width: 12px; height: 12px; vertical-align: -2px; margin: 0 4px 0 10px; }
</style>
</head>
<body>
<h1>扫描线填充核对视图</h1>
<div class="bar">
  <label>样例 <select id="picker"></select></label>
  <span class="stats" id="stats"></span>
</div>
<svg id="stage" xmlns="http://www.w3.org/2000/svg"></svg>
<div class="legend">
  <span class="swatch" style="background:#9ec5fe"></span>填充像素（引擎结果）
  <span class="swatch" style="border:2px solid #d63384"></span>轮廓（画布内实线）
  <span class="swatch" style="border:2px dashed #d63384"></span>轮廓被裁掉的部分（虚线）
</div>
<script>
"use strict";
const SAMPLES = __DATA__;
const CELL = 28;          // 1 个数学单位对应的屏幕像素
const MARGIN = 1;         // 视图外留白（单位：数学坐标）

function clipEdgeToCanvas(x1, y1, x2, y2, w, h) {
  // Cohen–Sutherland，裁到 [-0.5, w-0.5] x [-0.5, h-0.5]
  const xmin = -0.5, xmax = w - 0.5, ymin = -0.5, ymax = h - 0.5;
  const L = 1, R = 2, B = 4, T = 8;
  const code = (x, y) =>
    (x < xmin ? L : 0) | (x > xmax ? R : 0) | (y < ymin ? T : 0) | (y > ymax ? B : 0);
  let c1 = code(x1, y1), c2 = code(x2, y2);
  for (;;) {
    if (!(c1 | c2)) return [x1, y1, x2, y2];
    if (c1 & c2) return null;
    const c = c1 || c2;
    let x, y;
    if (c & T) { x = x1 + (x2 - x1) * (ymin - y1) / (y2 - y1); y = ymin; }
    else if (c & B) { x = x1 + (x2 - x1) * (ymax - y1) / (y2 - y1); y = ymax; }
    else if (c & R) { y = y1 + (y2 - y1) * (xmax - x1) / (x2 - x1); x = xmax; }
    else { y = y1 + (y2 - y1) * (xmin - x1) / (x2 - x1); x = xmin; }
    if (c === c1) { x1 = x; y1 = y; c1 = code(x1, y1); }
    else { x2 = x; y2 = y; c2 = code(x2, y2); }
  }
}

function el(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
}

function render(name) {
  const s = SAMPLES[name];
  const [W, H] = s.canvas;
  const svg = document.getElementById("stage");
  svg.textContent = "";

  // 视图范围：画布与全部轮廓的并集，外扩 MARGIN
  let vx0 = -0.5, vy0 = -0.5, vx1 = W - 0.5, vy1 = H - 0.5;
  for (const contour of s.contours)
    for (const [x, y] of contour) {
      vx0 = Math.min(vx0, x); vy0 = Math.min(vy0, y);
      vx1 = Math.max(vx1, x); vy1 = Math.max(vy1, y);
    }
  vx0 -= MARGIN; vy0 -= MARGIN; vx1 += MARGIN; vy1 += MARGIN;
  svg.setAttribute("viewBox", [vx0, vy0, vx1 - vx0, vy1 - vy0].join(" "));
  svg.setAttribute("width", Math.round((vx1 - vx0) * CELL));
  svg.setAttribute("height", Math.round((vy1 - vy0) * CELL));

  // 1. 填充像素（只画引擎结果）
  const fillLayer = el("g", { fill: "#9ec5fe" });
  let rendered = 0;
  for (const [y, spans] of s.rows)
    for (const [a, b] of spans)
      for (let i = a; i <= b; i++) {
        const r = el("rect", {
          x: i - 0.5, y: y - 0.5, width: 1, height: 1,
          "data-pixel": i + "," + y,
        });
        fillLayer.appendChild(r);
        rendered++;
      }
  svg.appendChild(fillLayer);

  // 2. 画布边界
  svg.appendChild(el("rect", {
    x: -0.5, y: -0.5, width: W, height: H,
    fill: "none", stroke: "#333", "stroke-width": 0.06,
  }));

  // 3. 轮廓：整体 <polygon> 虚线（被裁掉的部分即画布外的虚线段），
  //    画布内部分再用实线 <path> 压一遍
  const dashedLayer = el("g", {
    fill: "none", stroke: "#d63384", "stroke-width": 0.08,
    "stroke-dasharray": "0.3 0.18", opacity: 0.9,
  });
  const solidSegs = [];
  for (const contour of s.contours) {
    dashedLayer.appendChild(el("polygon", {
      points: contour.map(p => p.join(",")).join(" "),
    }));
    for (let k = 0; k < contour.length; k++) {
      const [x1, y1] = contour[k];
      const [x2, y2] = contour[(k + 1) % contour.length];
      const seg = clipEdgeToCanvas(x1, y1, x2, y2, W, H);
      if (seg) solidSegs.push(seg);
    }
  }
  svg.appendChild(dashedLayer);
  const solid = el("path", {
    d: solidSegs.map(([x1, y1, x2, y2]) => `M${x1} ${y1}L${x2} ${y2}`).join(""),
    fill: "none", stroke: "#d63384", "stroke-width": 0.08,
  });
  svg.appendChild(solid);

  // 4. 数字面板：像素数与耗时均来自引擎输出
  const match = rendered === s.filled;
  document.getElementById("stats").innerHTML =
    `画布 <b>${W}×${H}</b> · 规则 <b>${s.rule}</b> · ` +
    `填充像素 <b>${s.filled}</b>（引擎）/ <b class="${match ? "ok" : "bad"}">${rendered}</b>（页面） · ` +
    `耗时 <b>${s.elapsed_ms} ms</b>（引擎实测） · hash <b>${s.hash}</b>`;
}

const picker = document.getElementById("picker");
for (const name of Object.keys(SAMPLES)) {
  const opt = document.createElement("option");
  opt.value = opt.textContent = name;
  picker.appendChild(opt);
}
const requested = new URLSearchParams(location.search).get("sample");
const initial = SAMPLES[requested] ? requested : "01-concave";
picker.value = initial;
picker.addEventListener("change", () => {
  const q = new URLSearchParams(location.search);
  q.set("sample", picker.value);
  location.search = q.toString();
});
render(initial);
</script>
</body>
</html>
"""


def main():
    samples = collect_samples()
    data = json.dumps(samples, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.replace("__DATA__", data)
    out = ROOT / "viewer.html"
    out.write_text(html, encoding="utf-8")
    print("wrote %s (%d samples, %d bytes)" % (out, len(samples), len(html)))


if __name__ == "__main__":
    main()

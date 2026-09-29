#!/usr/bin/env python3
"""Generate the self-contained viewer.html.

Runs the fill engine (fill.py) on every sample in samples/polygons and
inlines the canonical results (rows, filled count, measured engine time)
into the page. The page itself never recomputes geometry: it only renders
what the engine produced (README section 4.3).

    python3 build_viewer.py            # writes viewer.html
"""

import json
import statistics
import time
from pathlib import Path

import fill

ROOT = Path(__file__).resolve().parent
POLYGONS_DIR = ROOT / "samples" / "polygons"
OUT_PATH = ROOT / "viewer.html"
TIMING_RUNS = 5
DEFAULT_SAMPLE = "01-concave"


def collect_samples():
    samples = []
    for path in sorted(POLYGONS_DIR.glob("*.json")):
        width, height, rule, contours = fill.load_spec(str(path))
        timings = []
        result = None
        for _ in range(TIMING_RUNS):
            started = time.perf_counter()
            result = fill.fill_contours(width, height, rule, contours)
            timings.append(time.perf_counter() - started)
        samples.append({
            "name": path.stem,
            "canvas": [width, height],
            "rule": rule,
            "contours": [[[x, y] for x, y in contour] for contour in contours],
            "rows": [[y, [[a, b] for a, b in segments]] for y, segments in result.rows],
            "filled": result.filled,
            "elapsedMs": round(statistics.median(timings) * 1000, 3),
        })
    return samples


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>扫描线填充查看器</title>
<style>
  :root { color-scheme: light; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; color: #222; }
  header { padding: 10px 16px; border-bottom: 1px solid #ddd; }
  h1 { font-size: 16px; margin: 0 0 8px; }
  nav a { margin-right: 8px; font-size: 12px; color: #2f6fdb; text-decoration: none; }
  nav a.current { font-weight: 700; color: #111; border-bottom: 2px solid #2f6fdb; }
  main { display: flex; gap: 20px; padding: 16px; align-items: flex-start; }
  svg { background: #fff; border: 1px solid #eee; flex: none; }
  .bg { fill: #ffffff; }
  .px { fill: #2f6fdb; }
  .px:hover { fill: #e8871a; }
  .outside { fill: none; stroke: #b5b5b5; stroke-width: 0.14; stroke-dasharray: 0.7 0.45; }
  .clipped { fill: none; stroke: #d2342c; stroke-width: 0.18; }
  .canvas-border { fill: none; stroke: #333; stroke-width: 0.12; }
  aside { font-size: 13px; min-width: 240px; }
  table { border-collapse: collapse; }
  td { padding: 2px 10px 2px 0; vertical-align: top; }
  td:first-child { color: #666; white-space: nowrap; }
  .legend { margin-top: 12px; }
  .legend div { margin: 2px 0; }
  .swatch { display: inline-block; width: 22px; height: 0; border-top: 2px solid; vertical-align: middle; margin-right: 6px; }
  #hover { color: #666; margin-top: 8px; min-height: 1.2em; }
  .bad { color: #d2342c; font-weight: 700; }
</style>
</head>
<body>
<header>
  <h1>扫描线填充查看器</h1>
  <nav id="sample-nav"></nav>
</header>
<main>
  <svg id="stage" xmlns="http://www.w3.org/2000/svg"></svg>
  <aside>
    <table id="stats"></table>
    <div class="legend">
      <div><span class="swatch" style="border-color:#2f6fdb"></span>填充像素（引擎结果）</div>
      <div><span class="swatch" style="border-color:#d2342c"></span>轮廓（画布内部分）</div>
      <div><span class="swatch" style="border-color:#b5b5b5;border-top-style:dashed"></span>被裁掉的部分（画布外，虚线）</div>
      <div><span class="swatch" style="border-color:#333"></span>画布边界</div>
    </div>
    <div id="hover"></div>
  </aside>
</main>
<script>
"use strict";
/* 数据由 build_viewer.py 运行 fill.py 引擎生成并内联；页面只画结果，不另算规则。 */
const SAMPLES = __SAMPLES_JSON__;
const DEFAULT_SAMPLE = __DEFAULT_SAMPLE__;
const TIMING_RUNS = __TIMING_RUNS__;

function pickSample() {
  const name = new URLSearchParams(location.search).get("sample") || DEFAULT_SAMPLE;
  return SAMPLES.find((s) => s.name === name) || null;
}

function buildNav(current) {
  const nav = document.getElementById("sample-nav");
  for (const s of SAMPLES) {
    const a = document.createElement("a");
    a.href = "?sample=" + encodeURIComponent(s.name);
    a.textContent = s.name;
    if (current && s.name === current.name) a.classList.add("current");
    nav.appendChild(a);
  }
}

function statRows(sample, pixelCount) {
  const rows = [
    ["样例", sample.name],
    ["画布", sample.canvas[0] + " × " + sample.canvas[1]],
    ["填充规则", sample.rule],
    ["填充像素数", String(sample.filled)],
    ["引擎耗时", sample.elapsedMs.toFixed(3) + " ms（fill.py 单次填充，" + TIMING_RUNS + " 次取中位）"],
  ];
  const ok = pixelCount === sample.filled;
  rows.push(["自检", ok
    ? "data-pixel 数 " + pixelCount + " = filled ✓"
    : "data-pixel 数 " + pixelCount + " ≠ filled " + sample.filled + " ✗"]);
  return { rows, ok };
}

function render(sample) {
  const stage = document.getElementById("stage");
  const stats = document.getElementById("stats");
  if (!sample) {
    stage.replaceChildren();
    stats.innerHTML = "";
    document.getElementById("hover").textContent = "未知样例，请从上方导航选择。";
    return;
  }
  const W = sample.canvas[0], H = sample.canvas[1];
  let minX = 0, minY = 0, maxX = W, maxY = H;
  for (const contour of sample.contours) {
    for (const p of contour) {
      if (p[0] < minX) minX = p[0];
      if (p[1] < minY) minY = p[1];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] > maxY) maxY = p[1];
    }
  }
  const pad = 2;
  const vbX = minX - pad, vbY = minY - pad;
  const vbW = maxX - minX + 2 * pad, vbH = maxY - minY + 2 * pad;
  const unit = Math.max(4, Math.min(28, 960 / vbW, 640 / vbH));
  stage.setAttribute("viewBox", vbX + " " + vbY + " " + vbW + " " + vbH);
  stage.setAttribute("width", Math.round(vbW * unit));
  stage.setAttribute("height", Math.round(vbH * unit));

  const parts = [];
  parts.push('<rect class="bg" x="' + vbX + '" y="' + vbY + '" width="' + vbW + '" height="' + vbH + '"/>');
  const pointStrings = sample.contours.map((c) => c.map((p) => p[0] + "," + p[1]).join(" "));
  for (const points of pointStrings) {
    parts.push('<polygon class="outside" points="' + points + '"/>');
  }
  let pixelCount = 0;
  for (const row of sample.rows) {
    const y = row[0];
    for (const seg of row[1]) {
      for (let i = seg[0]; i <= seg[1]; i++) {
        parts.push('<rect class="px" data-pixel="' + i + "," + y + '" x="' + (i - 0.5) +
                   '" y="' + (y - 0.5) + '" width="1" height="1"/>');
        pixelCount++;
      }
    }
  }
  parts.push('<defs><clipPath id="canvas-clip"><rect x="-0.5" y="-0.5" width="' + W + '" height="' + H + '"/></clipPath></defs>');
  parts.push('<g clip-path="url(#canvas-clip)">');
  for (const points of pointStrings) {
    parts.push('<polygon class="clipped" points="' + points + '"/>');
  }
  parts.push("</g>");
  parts.push('<rect class="canvas-border" x="-0.5" y="-0.5" width="' + W + '" height="' + H + '"/>');
  stage.innerHTML = parts.join("");

  const { rows, ok } = statRows(sample, pixelCount);
  stats.innerHTML = rows.map(([k, v], i) =>
    '<tr><td>' + k + '</td><td' + (i === rows.length - 1 && !ok ? ' class="bad"' : "") +
    ">" + v + "</td></tr>").join("");
}

const sample = pickSample();
buildNav(sample);
render(sample);

const hover = document.getElementById("hover");
document.getElementById("stage").addEventListener("mouseover", (event) => {
  const tag = event.target.getAttribute("data-pixel");
  hover.textContent = tag ? "像素 (" + tag.replace(",", ", ") + ")" : "";
});
</script>
</body>
</html>
"""


def build_html(samples):
    payload = json.dumps(samples, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    html = TEMPLATE.replace("__SAMPLES_JSON__", payload)
    html = html.replace("__DEFAULT_SAMPLE__", json.dumps(DEFAULT_SAMPLE))
    html = html.replace("__TIMING_RUNS__", str(TIMING_RUNS))
    return html


def main():
    samples = collect_samples()
    OUT_PATH.write_text(build_html(samples), encoding="utf-8")
    print(f"wrote {OUT_PATH.name}: {len(samples)} samples")


if __name__ == "__main__":
    main()

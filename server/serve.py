"""Accept temperature and soil-moisture posts from the ESP32 and graph the log."""

import json
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8080
LOG_PATH = Path(__file__).with_name("readings.jsonl")

latest = None

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vermiculture sensors</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: Georgia, serif;
      margin: 2rem auto 3rem;
      max-width: 58rem;
      padding: 0 1.25rem;
      color: #1c1915;
      background: #fbf8f4;
    }
    h1 { font-weight: normal; font-size: 1.6rem; margin-bottom: 0.4rem; }
    h2 { font-weight: normal; font-size: 1.15rem; margin: 0 0 0.7rem; }
    h3 { font-weight: normal; font-size: 1.05rem; margin: 1.1rem 0 0.45rem; }
    table { border-collapse: collapse; width: 100%; background: #fff; }
    th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #ddd6cc; }
    code { font-family: Consolas, monospace; }
    .wait { color: #6b6258; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem 1.5rem;
      align-items: center;
      justify-content: space-between;
      margin: 1rem 0 1.25rem;
    }
    .ranges { display: flex; flex-wrap: wrap; gap: 0.35rem; }
    button { font: inherit; color: #1c1915; }
    button:focus-visible { outline: 2px solid #1c1915; outline-offset: 2px; }
    .ranges button {
      background: transparent;
      border: 1px solid #ddd6cc;
      padding: 0.28rem 0.7rem;
      cursor: pointer;
    }
    .ranges button:hover { border-color: #1c1915; }
    .ranges button[aria-pressed="true"] {
      background: #1c1915;
      border-color: #1c1915;
      color: #fbf8f4;
    }
    section { margin-top: 1.5rem; }
    .plot {
      position: relative;
      height: 320px;
      background: #fff;
      border: 1px solid #e4dcd2;
    }
    .plot svg { width: 100%; height: 320px; display: block; cursor: crosshair; }
    .plot text { font-family: Georgia, serif; font-size: 12px; fill: #6b6258; }
    .plot .wait { margin: 0; padding: 8.5rem 1rem 0; text-align: center; }
    .legend { display: flex; flex-wrap: wrap; gap: 0.45rem 1rem; margin-top: 0.6rem; }
    .legend button {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: transparent;
      border: 0;
      padding: 0;
      cursor: pointer;
    }
    .legend button[aria-pressed="false"] { opacity: 0.38; }
    .chip { width: 0.8rem; height: 0.8rem; display: inline-block; }
    .tip {
      position: absolute;
      z-index: 2;
      pointer-events: none;
      background: #1c1915;
      color: #f6f1ea;
      padding: 0.45rem 0.6rem;
      font-family: Consolas, monospace;
      font-size: 0.78rem;
      line-height: 1.45;
    }
    .tip .when { color: #d9cfc3; }
    .tip .row { display: flex; justify-content: space-between; gap: 0.9rem; }
  </style>
</head>
<body>
  <h1>Vermiculture sensors</h1>
  <p id="status" class="wait">Loading readings…</p>
  <div class="toolbar">
    <div class="ranges" role="group" aria-label="Time range">
      <button type="button" data-range="1h" aria-pressed="false">1 hour</button>
      <button type="button" data-range="6h" aria-pressed="false">6 hours</button>
      <button type="button" data-range="24h" aria-pressed="true">24 hours</button>
      <button type="button" data-range="all" aria-pressed="false">All</button>
    </div>
    <div class="ranges" role="group" aria-label="Temperature unit">
      <button type="button" data-unit="C" aria-pressed="true">°C</button>
      <button type="button" data-unit="F" aria-pressed="false">°F</button>
    </div>
  </div>

  <section>
    <h2>Temperature</h2>
    <div class="plot" id="temp-plot"></div>
    <div class="legend" id="temp-legend"></div>
  </section>

  <section id="moisture-panel" hidden>
    <h2>Soil moisture</h2>
    <div class="plot" id="moisture-plot"></div>
    <div class="legend" id="moisture-legend"></div>
  </section>

  <section>
    <h2>Latest reading</h2>
    <div id="latest"></div>
  </section>
  <noscript><p class="wait">This page draws the graph in the browser. Enable JavaScript, then reload.</p></noscript>
  <script>
    const COLORS = ["#9a4e32", "#2f6d4f", "#2d5f86", "#b0792b", "#6d4b8a"];
    const RANGE_MS = {"1h": 60 * 60 * 1000, "6h": 6 * 60 * 60 * 1000, "24h": 24 * 60 * 60 * 1000};
    const GAP_MS = 3 * 60 * 1000;

    let readings = [];
    let rangeKey = "24h";
    let tempUnit = "C";
    const hiddenTemp = new Set();
    const hiddenMoisture = new Set();

    function svgEl(name) {
      return document.createElementNS("http://www.w3.org/2000/svg", name);
    }

    function el(tag, text) {
      const node = document.createElement(tag);
      node.textContent = text;
      return node;
    }

    function note(text) {
      return el("p", text);
    }

    function finite(value) {
      if (typeof value !== "number" && typeof value !== "string") return null;
      if (typeof value === "string" && value.trim() === "") return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function formatStamp(ms) {
      return new Date(ms).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit"
      });
    }

    function formatAxisTime(ms, span) {
      const when = new Date(ms);
      const time = when.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
      if (span <= 18 * 60 * 60 * 1000) return time;
      const day = when.toLocaleDateString([], {month: "short", day: "numeric"});
      return day + " " + time;
    }

    function formatBoth(celsius) {
      const c = finite(celsius);
      if (c == null) return "no reading";
      const f = c * 9 / 5 + 32;
      return c.toFixed(2) + " °C (" + f.toFixed(2) + " °F)";
    }

    function tempNumber(celsius) {
      return tempUnit === "F" ? celsius * 9 / 5 + 32 : celsius;
    }

    function formatTempNumber(celsius) {
      const unit = tempUnit === "F" ? "°F" : "°C";
      return tempNumber(celsius).toFixed(2) + " " + unit;
    }

    function formatPercent(point) {
      const value = point.v;
      const shown = Number.isInteger(value) ? String(value) : value.toFixed(1);
      const raw = finite(point.raw);
      return raw == null ? shown + "%" : shown + "% · raw " + raw;
    }

    function collectSeries(rows, listName, identify, valueOf) {
      const map = new Map();
      rows.forEach(row => {
        const t = Date.parse(row.received_at);
        if (!Number.isFinite(t) || !Array.isArray(row[listName])) return;
        row[listName].forEach(item => {
          const v = valueOf(item);
          if (v == null) return;
          const id = identify(item);
          let series = map.get(id.key);
          if (!series) {
            series = {key: id.key, label: id.label, points: []};
            map.set(id.key, series);
          }
          series.points.push({t: t, v: v, raw: item.raw});
        });
      });
      const series = [...map.values()];
      series.forEach(item => item.points.sort((a, b) => a.t - b.t));
      series.sort((a, b) => a.label.localeCompare(b.label));
      return series;
    }

    function tempIdentity(sensor) {
      const address = sensor.address ? String(sensor.address) : "";
      const tail = address.length > 4 ? address.slice(-4) : address;
      return {
        key: address ? "addr:" + address : "index:" + sensor.index,
        label: tail ? "Probe …" + tail : "Probe " + sensor.index
      };
    }

    function moistureIdentity(sensor) {
      const pin = sensor.pin;
      return {
        key: pin != null ? "pin:" + pin : "index:" + sensor.index,
        label: pin != null ? "GPIO " + pin : "Input " + sensor.index
      };
    }

    function tempSeries(rows) {
      return collectSeries(rows, "sensors", tempIdentity, sensor => finite(sensor.celsius));
    }

    function moistureSeries(rows) {
      return collectSeries(rows, "moisture", moistureIdentity, sensor => finite(sensor.percent));
    }

    function inRange(rows) {
      const span = RANGE_MS[rangeKey];
      if (span == null) return rows;
      const cutoff = Date.now() - span;
      return rows.filter(row => {
        const t = Date.parse(row.received_at);
        return Number.isFinite(t) && t >= cutoff;
      });
    }

    function colorOf(keys) {
      const map = new Map();
      [...keys].sort().forEach((key, index) => map.set(key, COLORS[index % COLORS.length]));
      return key => map.get(key) || COLORS[0];
    }

    function segments(points) {
      const runs = [];
      let run = [];
      points.forEach(point => {
        const prev = run[run.length - 1];
        if (prev && point.t - prev.t > GAP_MS) {
          runs.push(run);
          run = [];
        }
        run.push(point);
      });
      if (run.length) runs.push(run);
      return runs;
    }

    function thinPoints(points, maxPoints) {
      if (points.length <= maxPoints) return points.slice();
      const out = [];
      const step = points.length / maxPoints;
      for (let i = 0; i < maxPoints; i++) {
        const start = Math.floor(i * step);
        const end = Math.min(points.length, Math.max(start + 1, Math.floor((i + 1) * step)));
        let lo = points[start];
        let hi = points[start];
        for (let j = start + 1; j < end; j++) {
          if (points[j].v < lo.v) lo = points[j];
          if (points[j].v > hi.v) hi = points[j];
        }
        if (lo.t <= hi.t) {
          out.push(lo);
          if (hi !== lo) out.push(hi);
        } else {
          out.push(hi);
          out.push(lo);
        }
      }
      return out;
    }

    function extent(numbers) {
      let min = Infinity;
      let max = -Infinity;
      numbers.forEach(number => {
        if (number < min) min = number;
        if (number > max) max = number;
      });
      return [min, max];
    }

    function niceTicks(min, max) {
      const span = max - min;
      if (!(span > 0)) return [min];
      const rough = span / 4;
      const mag = 10 ** Math.floor(Math.log10(rough));
      const residual = rough / mag;
      const nice = residual >= 7.5 ? 10 : residual >= 3.5 ? 5 : residual >= 1.5 ? 2 : 1;
      const step = nice * mag;
      const start = Math.ceil(min / step - 1e-9) * step;
      const ticks = [];
      for (let i = 0; i < 6; i++) {
        const value = Number((start + i * step).toFixed(6));
        if (value <= min + step * 0.15) continue;
        if (value >= max - step * 0.15) break;
        ticks.push(value);
      }
      if (!ticks.length) ticks.push(Number(((min + max) / 2).toFixed(6)));
      return ticks;
    }

    function drawChart(plot, legend, series, paint, opts) {
      plot.replaceChildren();
      legend.replaceChildren();
      const present = series.filter(item => item.points.length);
      if (!present.length) {
        const empty = note(opts.empty);
        empty.className = "wait";
        plot.append(empty);
        return;
      }

      present.forEach(item => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("aria-pressed", opts.hidden.has(item.key) ? "false" : "true");
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.style.background = paint(item.key);
        const last = item.points[item.points.length - 1];
        button.append(chip, document.createTextNode(item.label + " · " + opts.format(last)));
        button.addEventListener("click", () => {
          if (opts.hidden.has(item.key)) opts.hidden.delete(item.key);
          else opts.hidden.add(item.key);
          renderCharts();
        });
        legend.append(button);
      });

      const visible = present.filter(item => !opts.hidden.has(item.key));
      if (!visible.length) {
        const hiddenNote = note("Select a series in the legend to show it.");
        hiddenNote.className = "wait";
        plot.append(hiddenNote);
        return;
      }

      const width = plot.clientWidth;
      const height = 320;
      if (width < 80) return;

      const displayed = [];
      visible.forEach(item => item.points.forEach(point => displayed.push(opts.value(point))));
      let [min, max] = extent(displayed);
      if (opts.domain) {
        min = opts.domain[0];
        max = opts.domain[1];
      } else if (!(max > min)) {
        min -= opts.minPad;
        max += opts.minPad;
      } else {
        const pad = Math.max((max - min) * 0.12, opts.minPad);
        min -= pad;
        max += pad;
      }
      if (!(max > min)) {
        min -= 1;
        max += 1;
      }

      const span = max - min;
      const places = opts.axisPlaces != null ? opts.axisPlaces : span >= 20 ? 0 : span >= 2 ? 1 : 2;
      const yText = value => value.toFixed(places);
      const margin = {top: 16, right: 16, bottom: 36, left: Math.max(52, 18 + yText(max).length * 8)};
      const innerW = Math.max(10, width - margin.left - margin.right);
      const innerH = height - margin.top - margin.bottom;
      const times = [];
      visible.forEach(item => item.points.forEach(point => times.push(point.t)));
      const [tMin, tMax] = extent(times);
      const xOf = t => tMax === tMin ? margin.left + innerW / 2 : margin.left + (t - tMin) / (tMax - tMin) * innerW;
      const yOf = value => margin.top + (max - value) / (max - min) * innerH;

      const svg = svgEl("svg");
      svg.setAttribute("viewBox", "0 0 " + width + " " + height);
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", opts.aria);

      const defs = svgEl("defs");
      const clip = svgEl("clipPath");
      clip.setAttribute("id", opts.clipId);
      const clipRect = svgEl("rect");
      clipRect.setAttribute("x", margin.left);
      clipRect.setAttribute("y", margin.top);
      clipRect.setAttribute("width", innerW);
      clipRect.setAttribute("height", innerH);
      clip.append(clipRect);
      defs.append(clip);
      svg.append(defs);

      niceTicks(min, max).forEach(tick => {
        const y = yOf(tick);
        const grid = svgEl("line");
        grid.setAttribute("x1", margin.left);
        grid.setAttribute("x2", margin.left + innerW);
        grid.setAttribute("y1", y);
        grid.setAttribute("y2", y);
        grid.setAttribute("stroke", "#e6dfd6");
        svg.append(grid);
        const label = svgEl("text");
        label.setAttribute("x", margin.left - 8);
        label.setAttribute("y", y + 4);
        label.setAttribute("text-anchor", "end");
        label.textContent = yText(tick);
        svg.append(label);
      });

      const timeSpan = tMax - tMin;
      const longSpan = timeSpan > 18 * 60 * 60 * 1000;
      const tickCount = Math.max(2, Math.min(longSpan ? 4 : 6, Math.floor(innerW / (longSpan ? 150 : 72))));
      for (let i = 0; i < tickCount; i++) {
        const t = tMax === tMin ? tMin : tMin + i * timeSpan / (tickCount - 1);
        const label = svgEl("text");
        label.setAttribute("x", xOf(t));
        label.setAttribute("y", height - 12);
        label.setAttribute("text-anchor", i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle");
        label.textContent = formatAxisTime(t, timeSpan);
        svg.append(label);
      }

      const layer = svgEl("g");
      layer.setAttribute("clip-path", "url(#" + opts.clipId + ")");
      const plotted = [];
      visible.forEach(item => {
        const color = paint(item.key);
        segments(item.points).forEach(run => {
          const allowance = Math.max(2, Math.round(1200 * run.length / item.points.length));
          const drawn = thinPoints(run, allowance);
          if (drawn.length === 1) {
            const point = drawn[0];
            const cx = xOf(point.t);
            const cy = yOf(opts.value(point));
            const dot = svgEl("circle");
            dot.setAttribute("cx", cx);
            dot.setAttribute("cy", cy);
            dot.setAttribute("r", 3);
            dot.setAttribute("fill", color);
            layer.append(dot);
            plotted.push({key: item.key, label: item.label, color: color, point: point, x: cx, y: cy});
            return;
          }
          const commands = [];
          drawn.forEach((point, index) => {
            const x = xOf(point.t);
            const y = yOf(opts.value(point));
            plotted.push({key: item.key, label: item.label, color: color, point: point, x: x, y: y});
            commands.push((index === 0 ? "M" : "L") + x.toFixed(2) + " " + y.toFixed(2));
          });
          const path = svgEl("path");
          path.setAttribute("d", commands.join(" "));
          path.setAttribute("fill", "none");
          path.setAttribute("stroke", color);
          path.setAttribute("stroke-width", "1.75");
          path.setAttribute("stroke-linejoin", "round");
          path.setAttribute("stroke-linecap", "round");
          layer.append(path);
        });
      });
      svg.append(layer);

      const rule = svgEl("line");
      rule.setAttribute("y1", margin.top);
      rule.setAttribute("y2", margin.top + innerH);
      rule.setAttribute("stroke", "#1c1915");
      rule.setAttribute("stroke-width", "1");
      rule.setAttribute("visibility", "hidden");
      svg.append(rule);
      const markerLayer = svgEl("g");
      svg.append(markerLayer);

      const tip = document.createElement("div");
      tip.className = "tip";
      tip.hidden = true;
      plot.append(svg, tip);

      function hideTip() {
        rule.setAttribute("visibility", "hidden");
        markerLayer.replaceChildren();
        tip.hidden = true;
      }

      svg.addEventListener("pointermove", pointerEvent => {
        const bounds = svg.getBoundingClientRect();
        if (!bounds.width || !plotted.length) return;
        const x = (pointerEvent.clientX - bounds.left) * (width / bounds.width);
        let nearest = plotted[0];
        let nearestDist = Math.abs(nearest.x - x);
        plotted.forEach(item => {
          const dist = Math.abs(item.x - x);
          if (dist < nearestDist) {
            nearest = item;
            nearestDist = dist;
          }
        });
        const byKey = new Map();
        plotted.forEach(item => {
          const distT = Math.abs(item.point.t - nearest.point.t);
          const prev = byKey.get(item.key);
          if (!prev || distT < prev.distT) byKey.set(item.key, {distT: distT, item: item});
        });
        const hits = [...byKey.values()].filter(entry => entry.distT <= 20000).map(entry => entry.item);
        rule.setAttribute("visibility", "visible");
        rule.setAttribute("x1", nearest.x);
        rule.setAttribute("x2", nearest.x);
        markerLayer.replaceChildren();
        hits.forEach(item => {
          const dot = svgEl("circle");
          dot.setAttribute("cx", item.x);
          dot.setAttribute("cy", item.y);
          dot.setAttribute("r", "4");
          dot.setAttribute("fill", item.color);
          dot.setAttribute("stroke", "#fff");
          dot.setAttribute("stroke-width", "1.5");
          markerLayer.append(dot);
        });
        tip.replaceChildren();
        const when = document.createElement("div");
        when.className = "when";
        when.textContent = formatStamp(nearest.point.t);
        tip.append(when);
        hits.forEach(item => {
          const row = document.createElement("div");
          row.className = "row";
          row.append(el("span", item.label), el("span", opts.format(item.point)));
          tip.append(row);
        });
        tip.hidden = false;
        const host = plot.getBoundingClientRect();
        let left = pointerEvent.clientX - host.left + 14;
        let top = pointerEvent.clientY - host.top + 14;
        if (left + tip.offsetWidth > plot.clientWidth - 8) left = pointerEvent.clientX - host.left - tip.offsetWidth - 14;
        if (top + tip.offsetHeight > plot.clientHeight - 8) top = pointerEvent.clientY - host.top - tip.offsetHeight - 14;
        tip.style.left = Math.max(8, left) + "px";
        tip.style.top = Math.max(8, top) + "px";
      });
      svg.addEventListener("pointerleave", hideTip);
    }

    function renderLatest(reading) {
      const root = document.getElementById("latest");
      root.replaceChildren();
      if (!reading) {
        const waiting = note("Waiting for the ESP32. It posts about every 15 seconds.");
        waiting.className = "wait";
        root.append(waiting);
        return;
      }

      root.append(el("h3", "Temperature"));
      const sensors = Array.isArray(reading.sensors) ? reading.sensors : [];
      if (!sensors.length) {
        const missing = note("No temperature probes in this post.");
        missing.className = "wait";
        root.append(missing);
      } else {
        const table = document.createElement("table");
        const head = document.createElement("tr");
        ["#", "Probe", "Temperature"].forEach(text => head.append(el("th", text)));
        const thead = document.createElement("thead");
        thead.append(head);
        const tbody = document.createElement("tbody");
        sensors.forEach(sensor => {
          const row = document.createElement("tr");
          row.append(el("td", sensor.index == null ? "" : String(sensor.index)));
          const address = document.createElement("td");
          const code = document.createElement("code");
          code.textContent = sensor.address || "";
          address.append(code);
          row.append(address, el("td", formatBoth(sensor.celsius)));
          tbody.append(row);
        });
        table.append(thead, tbody);
        root.append(table);
      }

      root.append(el("h3", "Soil moisture"));
      const moisture = Array.isArray(reading.moisture) ? reading.moisture : [];
      if (!moisture.length) {
        const missing = note("No soil-moisture reading in this post.");
        missing.className = "wait";
        root.append(missing);
        return;
      }
      const table = document.createElement("table");
      const head = document.createElement("tr");
      ["#", "Input", "Moisture", "Raw"].forEach(text => head.append(el("th", text)));
      const thead = document.createElement("thead");
      thead.append(head);
      const tbody = document.createElement("tbody");
      moisture.forEach(sensor => {
        const percent = finite(sensor.percent);
        const raw = finite(sensor.raw);
        const row = document.createElement("tr");
        row.append(
          el("td", sensor.index == null ? "" : String(sensor.index)),
          el("td", "GPIO " + (sensor.pin == null ? "" : sensor.pin)),
          el("td", percent == null ? "no reading" : percent + "%"),
          el("td", raw == null ? "no reading" : String(raw))
        );
        tbody.append(row);
      });
      table.append(thead, tbody);
      root.append(table);
      const help = note("Percent uses the dry and wet raw limits in the sketch. Raw is the unscaled ADC value.");
      help.className = "wait";
      root.append(help);
    }

    function renderStatus() {
      const status = document.getElementById("status");
      if (!readings.length) {
        status.className = "wait";
        status.textContent = "No readings stored yet.";
        return;
      }
      const last = readings[readings.length - 1];
      const when = Date.parse(last.received_at);
      status.className = "";
      status.textContent = "Last post " + (Number.isFinite(when) ? formatStamp(when) : "at an unknown time")
        + " · " + readings.length + " readings stored";
    }

    function renderCharts() {
      const shown = inRange(readings);
      const temperatures = tempSeries(shown);
      const paintTemp = colorOf(tempSeries(readings).map(item => item.key));
      drawChart(document.getElementById("temp-plot"), document.getElementById("temp-legend"), temperatures, paintTemp, {
        empty: "No temperature readings in this range.",
        aria: "Temperature over time",
        clipId: "clip-temp",
        hidden: hiddenTemp,
        minPad: tempUnit === "F" ? 1 : 0.5,
        value: point => tempNumber(point.v),
        format: point => formatTempNumber(point.v)
      });

      const moistureAll = moistureSeries(readings);
      const panel = document.getElementById("moisture-panel");
      panel.hidden = !moistureAll.some(item => item.points.length);
      if (!panel.hidden) {
        drawChart(document.getElementById("moisture-plot"), document.getElementById("moisture-legend"), moistureSeries(shown), colorOf(moistureAll.map(item => item.key)), {
          empty: "No soil-moisture readings in this range.",
          aria: "Soil moisture over time",
          clipId: "clip-moisture",
          hidden: hiddenMoisture,
          domain: [0, 100],
          axisPlaces: 0,
          minPad: 1,
          value: point => point.v,
          format: formatPercent
        });
      }
    }

    function render() {
      renderStatus();
      renderLatest(readings.length ? readings[readings.length - 1] : null);
      renderCharts();
    }

    async function refresh() {
      try {
        const response = await fetch("/history", {cache: "no-store"});
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error("bad payload");
        readings = data;
        render();
      } catch (err) {
        if (!readings.length) {
          const status = document.getElementById("status");
          status.className = "wait";
          status.textContent = "Could not load readings.";
        }
      }
    }

    document.querySelectorAll("[data-range]").forEach(button => {
      button.addEventListener("click", () => {
        rangeKey = button.dataset.range;
        document.querySelectorAll("[data-range]").forEach(other => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        renderCharts();
      });
    });

    document.querySelectorAll("[data-unit]").forEach(button => {
      button.addEventListener("click", () => {
        tempUnit = button.dataset.unit;
        document.querySelectorAll("[data-unit]").forEach(other => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        renderCharts();
      });
    });

    window.addEventListener("resize", renderCharts);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refresh();
    });
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""


def lan_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def read_log() -> list:
    if not LOG_PATH.exists():
        return []
    rows = []
    with LOG_PATH.open(encoding="utf-8") as log:
        for line in log:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/readings":
            payload = json.dumps(latest).encode() if latest is not None else b"null"
            self._send(200, "application/json; charset=utf-8", payload)
            return
        if path == "/history":
            payload = json.dumps(read_log(), separators=(",", ":")).encode()
            self._send(200, "application/json; charset=utf-8", payload)
            return
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/readings":
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            reading = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, "text/plain; charset=utf-8", b"expected JSON")
            return
        if not isinstance(reading, dict):
            self._send(400, "text/plain; charset=utf-8", b"expected a JSON object")
            return

        reading["received_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        global latest
        latest = reading
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(json.dumps(reading) + "\n")
        print(json.dumps(reading), flush=True)
        self._send(204, "text/plain; charset=utf-8", b"")

    def _send(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
    global latest
    rows = read_log()
    latest = rows[-1] if rows else None
    ip = lan_ip()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"On this PC:  http://localhost:{PORT}")
    print(f"ESP32 posts: http://{ip}:{PORT}/readings")
    print("Leave this window open.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

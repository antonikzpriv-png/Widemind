import type { PublicWebsiteData } from "../contracts/index.ts";
import { displayUsd, escape } from "./format.ts";

const kinds = {
  thinking: "Thinking",
  research: "Research",
  communication: "Communication",
  delivery: "Delivery",
  handoff: "Business handoff",
  task: "Task",
};
export function thinkingNetwork(data: PublicWebsiteData) {
  const graph = data.agentGraph;
  const costs = (graph?.nodes ?? []).map((node, index) => {
    const label =
      node.cost.micros === null
        ? node.cost.status === "pending"
          ? "Cost pending"
          : "Cost unavailable"
        : `${displayUsd(node.cost.micros)}${node.cost.status === "settled" ? "" : ` · ${node.cost.status}`}`;
    return `<li><span>${index + 1}. ${kinds[node.kind]} · ${escape(node.state)}</span><span>${escape(label)}</span></li>`;
  });
  return `<section class="thinking-stage" aria-labelledby="thinking-title" data-thinking-network>
    <div class="thinking-heading"><p class="network-kicker">WIDEMIND / THE LIVING EXPERIMENT</p><h1 id="thinking-title">A mind in motion.</h1><p>Every action leaves a connection.<br>Every connection has a cost.</p></div>
    <div class="network-state"><span class="network-indicator"></span><span id="network-status" role="status">${graph?.state ? escape(graph.state) : "Awaiting a run"}</span><span id="network-mode">${graph?.profile === "demo" ? "DEMO · SYNTHETIC ACTIVITY" : data.financial?.mode === "sandbox" ? "SANDBOX · TEST FUNDS" : "RECORDED AGENT ACTIVITY"}</span></div>
    <div class="network-viewport"><svg id="thinking-svg" viewBox="0 0 1000 760" role="group" aria-labelledby="network-svg-title network-svg-description"><title id="network-svg-title">WideMind activity constellation</title><desc id="network-svg-description">Glowing white dots form a circular network. Each new recorded action adds a dot and a connection labeled with its cost. The activity list below provides a text alternative.</desc><g id="network-ambient" aria-hidden="true"></g><g id="network-edges" aria-hidden="true"></g><g id="network-dots"></g><g id="network-core" aria-hidden="true"><circle class="core-halo" cx="500" cy="380" r="28"/><circle class="core-dot" cx="500" cy="380" r="4"/></g></svg></div>
    <p id="network-empty" class="network-empty"${graph?.nodes.length ? " hidden" : ""}>The first action starts the constellation.<span>Waiting for recorded work.</span></p>
    <div class="network-bottom"><div class="network-count"><span id="network-count">${graph?.totalActions ?? 0}</span><small>RECORDED ACTIONS</small></div><p id="network-detail" aria-live="polite">${graph?.nodes.length ? "Select a dot to inspect its cost and status." : "No activity or spend is assumed."}</p><div class="network-actions"><button id="network-motion" class="secondary" type="button" aria-pressed="false" hidden>Pause motion</button><a href="#headquarters-details" class="network-explore">Explore headquarters <span aria-hidden="true">↓</span></a></div></div>
    <div class="network-caption"><span id="network-sync">${graph ? "Recorded state" : "Activity unavailable"}</span><span>PUBLIC ACTIVITY · PRIVATE THOUGHTS STAY PRIVATE</span></div>
    <noscript><p class="network-noscript">Enable JavaScript for the live constellation. Recorded activity and costs remain available below.</p></noscript>
  </section><details class="network-ledger"><summary>Connections & costs <span id="network-window">${graph ? `Latest ${graph.nodes.length} of ${graph.totalActions}` : "No recorded actions"}</span></summary><p>Exact USD costs from runtime receipts. Pending is not zero; provisional usage may change. Business handoffs report orchestration cost only. Opening a page never starts paid work.</p><ol id="network-cost-list">${costs.join("") || "<li>No recorded actions yet.</li>"}</ol></details>`;
}

export const thinkingCss = `
.network-home [hidden] {
  display: none !important;
}
.network-home {
  background: #000;
  color: #f5f5f5;
}
.network-home > header {
  position: relative;
  z-index: 2;
  width: 100%;
  max-width: none;
  border: 0;
  padding: 22px 3.5vw;
}
.network-home #home-main {
  padding: 0;
  max-width: none;
  width: 100%;
}
.network-home .brand span {
  color: #fff;
}
.network-home .thinking-stage {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  min-height: 760px;
  height: calc(100svh - 80px);
  margin: 0;
  border: 0;
  border-radius: 0;
  background: #000;
  padding: 0;
}
.thinking-heading {
  position: absolute;
  top: 22px;
  left: 3.5vw;
  z-index: 1;
  pointer-events: none;
}
.thinking-heading .network-kicker {
  font:
    10px ui-monospace,
    monospace;
  letter-spacing: 0.15em;
  color: #969696;
  margin: 0 0 16px;
}
.thinking-heading h1 {
  font-size: clamp(30px, 3.3vw, 52px);
  font-weight: 400;
  letter-spacing: -0.055em;
  line-height: 1.05;
  margin: 0 0 14px;
}
.thinking-heading > p:last-child {
  color: #999;
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
}
.network-state {
  position: absolute;
  top: 25px;
  right: 3.5vw;
  z-index: 1;
  display: grid;
  grid-template-columns: 7px auto;
  align-items: center;
  column-gap: 9px;
  font-size: 12px;
}
.network-indicator {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 9px #ffffff90;
}
.network-state #network-mode {
  grid-column: 2;
  font:
    8px ui-monospace,
    monospace;
  letter-spacing: 0.08em;
  color: #777;
  margin-top: 8px;
}
.network-viewport {
  position: absolute;
  inset: 0 0 100px;
}
#thinking-svg {
  position: absolute;
  inset: 0;
  display: block;
  min-width: 0;
  min-height: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}
.ambient-dot {
  fill: #fff;
  opacity: 0.5;
  filter: drop-shadow(0 0 4px #ffffffa0);
}
.ambient-spark {
  fill: #fff;
  opacity: 0.12;
}
.network-edge {
  stroke: #fff;
  stroke-width: 0.65;
  stroke-opacity: 0.38;
  fill: none;
}
.network-edge.is-new {
  stroke-dasharray: 1;
  animation: connection-arrive 1.4s ease-out both;
}
.network-edge.is-selected {
  stroke-opacity: 0.95;
  stroke-width: 1.1;
}
.network-cost {
  fill: #bcbcbc;
  font:
    10px ui-monospace,
    monospace;
  paint-order: stroke;
  stroke: #000;
  stroke-width: 5px;
  stroke-linejoin: round;
  text-anchor: middle;
  pointer-events: none;
}
.network-dot {
  fill: #fff;
  filter: drop-shadow(0 0 5px #ffffffb0);
}
.network-node {
  cursor: pointer;
  outline: none;
}
.network-node .node-target {
  fill: transparent;
}
.network-node:hover .network-dot,
.network-node:focus-visible .network-dot,
.network-node.is-selected .network-dot {
  r: 5;
  filter: drop-shadow(0 0 9px #fff);
}
.network-node:focus-visible .node-target {
  stroke: #fff;
  stroke-width: 1;
}
.network-node.is-new .network-dot {
  animation: dot-arrive 1.4s ease-out both;
}
.core-dot {
  fill: #fff;
  filter: drop-shadow(0 0 8px #fff);
}
.core-halo {
  fill: #fff;
  opacity: 0.015;
  filter: blur(8px);
}
.thinking-stage.is-working .core-halo {
  animation: mind-breathe 3.5s ease-in-out infinite;
}
.thinking-stage.is-working .core-dot {
  animation: core-breathe 3.5s ease-in-out infinite;
}
.network-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  font-size: 12px;
  color: #999;
  pointer-events: none;
  background: #000;
  padding: 16px;
  width: 260px;
}
.network-empty span {
  display: block;
  color: #666;
  font-size: 10px;
  margin-top: 6px;
}
.network-empty[hidden] {
  display: none;
}
.network-bottom {
  position: absolute;
  bottom: 44px;
  left: 3.5vw;
  right: 3.5vw;
  display: flex;
  align-items: flex-end;
  gap: 32px;
  z-index: 1;
}
.network-count {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.network-count > span {
  font-size: 42px;
  font-weight: 300;
  letter-spacing: -0.07em;
  line-height: 1.3;
}
.network-count small {
  font:
    8px ui-monospace,
    monospace;
  letter-spacing: 0.12em;
  color: #888;
}
.network-bottom #network-detail {
  font:
    11px/1.8 ui-monospace,
    monospace;
  color: #999;
  max-width: 380px;
  margin: 0 auto 0 0;
  min-height: 40px;
}
.network-actions {
  display: flex;
  align-items: center;
  gap: 22px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.network-actions button {
  font-size: 10px;
  min-height: 36px;
  padding: 8px 12px;
  margin: 0;
  background: #080808;
  color: #aaa;
  border-color: #252525;
}
.network-explore {
  font-size: 11px;
  white-space: nowrap;
}
.network-explore span {
  display: inline-block;
  margin-left: 16px;
}
.network-caption {
  position: absolute;
  bottom: 14px;
  left: 3.5vw;
  right: 3.5vw;
  display: flex;
  justify-content: space-between;
  gap: 15px;
  color: #666;
  font:
    8px/1.6 ui-monospace,
    monospace;
  letter-spacing: 0.05em;
}
.network-home .network-ledger {
  max-width: 1340px;
  margin: 0 auto;
  padding: 8px 28px 24px;
  border-top: 1px solid #191919;
}
.network-ledger summary {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 12px;
  color: #bbb;
  list-style: none;
}
.network-ledger summary::before {
  content: "+";
}
.network-ledger[open] summary::before {
  content: "−";
}
.network-ledger summary span {
  margin-left: auto;
  color: #777;
  font:
    10px ui-monospace,
    monospace;
}
.network-ledger p {
  color: #888;
  font-size: 12px;
  max-width: 700px;
}
.network-ledger ol {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 360px;
  overflow: auto;
}
.network-ledger li {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid #181818;
  font:
    11px/1.7 ui-monospace,
    monospace;
  color: #bbb;
}
.network-ledger li span:last-child {
  text-align: right;
}
.network-home .headquarters-details {
  max-width: 1340px;
  padding: 50px 28px 80px;
  margin: auto;
}
.network-noscript {
  position: absolute;
  bottom: 140px;
  text-align: center;
  width: 100%;
  font-size: 12px;
  color: #999;
}
.thinking-stage.is-still * {
  animation: none !important;
}
@keyframes connection-arrive {
  from {
    stroke-dashoffset: 1;
    opacity: 0;
  }
  to {
    stroke-dashoffset: 0;
    opacity: 1;
  }
}
@keyframes dot-arrive {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@keyframes mind-breathe {
  50% {
    opacity: 0.07;
    r: 40;
  }
}
@keyframes core-breathe {
  50% {
    opacity: 0.55;
  }
}
@media (prefers-reduced-motion: reduce) {
  .thinking-stage * {
    animation: none !important;
  }
}
@media (max-width: 700px) {
  .network-home > header {
    padding: 18px 20px;
    min-height: 70px;
  }
  .network-home > header nav a {
    font-size: 10px;
    padding: 8px;
  }
  .network-home > header nav a[href="/"] {
    display: none;
  }
  .network-home .thinking-stage {
    height: calc(100svh - 70px);
    min-height: 620px;
  }
  .thinking-heading {
    top: 14px;
    left: 22px;
  }
  .thinking-heading h1 {
    font-size: 32px;
  }
  .thinking-heading .network-kicker {
    font-size: 8px;
  }
  .thinking-heading > p:last-child {
    font-size: 11px;
  }
  .network-state {
    top: 155px;
    left: 22px;
    right: auto;
    font-size: 11px;
    display: flex;
    flex-wrap: wrap;
  }
  .network-state #network-mode {
    font-size: 7px;
    margin: 0 0 0 6px;
  }
  .network-viewport {
    inset: 178px 0 165px;
  }
  .network-bottom {
    left: 22px;
    right: 22px;
    bottom: 48px;
    gap: 18px;
    flex-wrap: wrap;
  }
  .network-count > span {
    font-size: 30px;
  }
  .network-bottom #network-detail {
    font-size: 10px;
    max-width: calc(100% - 120px);
    min-height: 35px;
    flex: 1;
  }
  .network-actions {
    width: 100%;
    justify-content: space-between;
    flex-direction: row-reverse;
    gap: 10px;
    margin-top: 2px;
  }
  .network-caption {
    left: 22px;
    right: 22px;
    bottom: 12px;
    font-size: 7px;
  }
  .network-caption > span:last-child {
    max-width: 135px;
    text-align: right;
  }
  .network-empty {
    font-size: 11px;
    top: 51%;
    width: 235px;
  }
  .network-home .network-ledger {
    padding-inline: 22px;
  }
  .network-home .headquarters-details {
    padding-inline: 22px;
  }
  .network-cost {
    font-size: 9px;
  }
  .network-ledger summary span {
    font-size: 9px;
  }
  .network-ledger li {
    font-size: 10px;
  }
}
@media (max-width: 360px) {
  .network-home > header {
    padding-inline: 16px;
  }
  .network-home > header .brand {
    font-size: 16px;
  }
  .network-home > header nav {
    max-width: 175px;
    justify-content: flex-end;
  }
  .network-home > header nav a {
    font-size: 9px;
    padding: 6px;
  }
  .network-state #network-mode {
    font-size: 6px;
  }
}
`;

export const thinkingScript = String.raw`
(() => {
  "use strict";
  const host = document.querySelector("[data-thinking-network]");
  if (!host) return;
  const svg = document.getElementById("thinking-svg"),
    edges = document.getElementById("network-edges"),
    dots = document.getElementById("network-dots"),
    ambient = document.getElementById("network-ambient");
  const $ = (id) => document.getElementById(id),
    ns = "http://www.w3.org/2000/svg";
  const names = {
    thinking: "Thinking",
    research: "Research",
    communication: "Communication",
    delivery: "Delivery",
    handoff: "Business handoff",
    task: "Task",
  };
  let graph = null,
    selected = null,
    known = new Set(),
    currentRun = null,
    initialized = false,
    timer = null,
    controller = null,
    pollVersion = 0,
    paused = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const motion = $("network-motion");
  motion.hidden = false;
  function setMotion() {
    motion.setAttribute("aria-pressed", String(paused));
    motion.textContent = paused ? "Enable motion" : "Pause motion";
    host.classList.toggle("is-still", paused);
  }
  setMotion();
  motion.onclick = () => {
    paused = !paused;
    setMotion();
  };
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener(
    "change",
    (event) => {
      paused = event.matches;
      setMotion();
    },
  );
  function element(tag, attributes = {}, text) {
    const el = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes))
      el.setAttribute(key, String(value));
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function hash(text) {
    let value = 2166136261;
    for (const c of text) value = Math.imul(value ^ c.charCodeAt(0), 16777619);
    return value >>> 0;
  }
  function usd(value) {
    const n = BigInt(value),
      whole = n / 1000000n;
    let fraction = (n % 1000000n).toString().padStart(6, "0");
    fraction = fraction.endsWith("0000")
      ? fraction.slice(0, 2)
      : fraction.replace(/0+$/, "");
    return "$" + whole + "." + fraction;
  }
  function cost(node) {
    if (node.cost.micros === null)
      return node.cost.status === "pending" ? "Pending" : "Unavailable";
    return (
      usd(node.cost.micros) +
      (node.cost.status === "settled"
        ? ""
        : node.cost.status === "provisional"
          ? " ~"
          : " *")
    );
  }
  function description(node) {
    return (
      names[node.kind] +
      " · " +
      node.state +
      " · " +
      cost(node) +
      (node.cost.status === "provisional"
        ? " (provisional)"
        : node.cost.status === "recorded"
          ? " (settlement pending)"
          : "") +
      " · " +
      new Date(node.at).toISOString()
    );
  }
  function valid(data) {
    return (
      data &&
      data.version === 1 &&
      Array.isArray(data.nodes) &&
      data.nodes.length <= 48 &&
      Number.isSafeInteger(data.totalActions) &&
      data.totalActions >= data.nodes.length &&
      [
        null,
        "active",
        "waiting",
        "paused",
        "exhausted",
        "stopped",
        "failed",
      ].includes(data.state) &&
      typeof data.workerConnected === "boolean" &&
      data.nodes.every(
        (n) =>
          n &&
          /^[a-f0-9]{24}$/.test(n.id) &&
          Object.hasOwn(names, n.kind) &&
          [
            "planned",
            "reserved",
            "running",
            "completed",
            "failed",
            "unresolved",
          ].includes(n.state) &&
          Number.isFinite(Date.parse(n.at)) &&
          n.cost &&
          [
            "settled",
            "provisional",
            "recorded",
            "pending",
            "unavailable",
          ].includes(n.cost.status) &&
          (n.cost.micros === null ||
            (typeof n.cost.micros === "string" &&
              /^(0|[1-9][0-9]{0,18})$/.test(n.cost.micros))),
      ) &&
      new Set(data.nodes.map((n) => n.id)).size === data.nodes.length
    );
  }
  function dimensions() {
    const rect = svg.getBoundingClientRect();
    const width = Math.max(rect.width, 280),
      height = Math.max(rect.height, 240);
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    return {
      width,
      height,
      cx: width / 2,
      cy: height / 2,
      r: Math.min(width * 0.42, height * 0.43),
    };
  }
  function draw() {
    if (!graph) return;
    const { width, height, cx, cy, r } = dimensions();
    ambient.replaceChildren();
    for (let i = 0; i < 106; i++) {
      const a = i * 2.39996323,
        band = 0.72 + (hash("ambient" + i) % 280) / 1000,
        x = cx + Math.cos(a) * r * band,
        y = cy + Math.sin(a) * r * band * 0.95;
      ambient.append(
        element("circle", {
          cx: x,
          cy: y,
          r: i % 6 === 0 ? 1.65 : 0.85,
          class: "ambient-dot",
        }),
      );
    }
    for (let i = 0; i < 55; i++) {
      const a = i * 2.39996323,
        rad = Math.sqrt((i + 1) / 55) * r * 0.72;
      ambient.append(
        element("circle", {
          cx: cx + Math.cos(a) * rad,
          cy: cy + Math.sin(a) * rad,
          r: 0.6,
          class: "ambient-spark",
        }),
      );
    }
    for (const item of document.querySelectorAll("#network-core circle")) {
      item.setAttribute("cx", cx);
      item.setAttribute("cy", cy);
    }
    edges.replaceChildren();
    dots.replaceChildren();
    const positions = [],
      labels = [];
    graph.nodes.forEach((node, i) => {
      const seed = hash(node.id),
        angle = (seed / 4294967296) * Math.PI * 2,
        radius = r * (0.36 + (hash(node.id + "r") % 600) / 1000);
      let p = {
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius * 0.94,
      };
      for (
        let j = 0;
        j < 8 &&
        positions.some(
          (other) => Math.hypot(other.x - p.x, other.y - p.y) < 24,
        );
        j++
      ) {
        const a = angle + (j + 1) * 0.42;
        p = {
          x: cx + Math.cos(a) * radius,
          y: cy + Math.sin(a) * radius * 0.94,
        };
      }
      const previous = i && i % 5 !== 0 ? positions[i - 1] : { x: cx, y: cy };
      positions.push(p);
      const fresh = initialized && !known.has(node.id) && !paused;
      const group = element("g", { "data-connection": node.id }),
        line = element("path", {
          d: "M " + previous.x + " " + previous.y + " L " + p.x + " " + p.y,
          pathLength: 1,
          class:
            "network-edge" +
            (fresh ? " is-new" : "") +
            (selected === node.id ? " is-selected" : ""),
        });
      group.append(line);
      const dx = p.x - previous.x,
        dy = p.y - previous.y,
        length = Math.max(Math.hypot(dx, dy), 1),
        text = cost(node),
        half = text.length * (width < 700 ? 2.8 : 3.1) + 3;
      let anchor;
      for (const t of [0.5, 0.7, 0.3, 0.85, 0.15]) {
        for (const offset of [10, -14, 23, -25]) {
          const candidate = {
            x: Math.min(
              width - half - 4,
              Math.max(half + 4, previous.x + dx * t - (dy / length) * offset),
            ),
            y: Math.min(
              height - 12,
              Math.max(15, previous.y + dy * t + (dx / length) * offset),
            ),
            half,
          };
          if (
            !labels.some(
              (other) =>
                Math.abs(other.x - candidate.x) < other.half + half &&
                Math.abs(other.y - candidate.y) < 15,
            )
          ) {
            anchor = candidate;
            break;
          }
        }
        if (anchor) break;
      }
      anchor = anchor || {
        x: (p.x + previous.x) / 2,
        y: (p.y + previous.y) / 2 - 10,
        half,
      };
      labels.push(anchor);
      const label = element(
        "text",
        { x: anchor.x, y: anchor.y, class: "network-cost" },
        text,
      );
      group.append(label);
      edges.append(group);
      const dot = element("g", {
        class:
          "network-node" +
          (fresh ? " is-new" : "") +
          (selected === node.id ? " is-selected" : ""),
        role: "button",
        tabindex: 0,
        "aria-label": description(node),
        "aria-pressed": String(selected === node.id),
        "data-node": node.id,
      });
      dot.append(
        element("title", {}, description(node)),
        element("circle", { cx: p.x, cy: p.y, r: 15, class: "node-target" }),
        element("circle", {
          cx: p.x,
          cy: p.y,
          r: node.state === "running" ? 3.8 : 2.5,
          class: "network-dot",
        }),
      );
      const choose = () => {
        selected = node.id;
        $("network-detail").textContent = description(node);
        for (const entry of dots.children) {
          const chosen = entry.dataset.node === selected;
          entry.classList.toggle("is-selected", chosen);
          entry.setAttribute("aria-pressed", String(chosen));
        }
        for (const entry of edges.children)
          entry
            .querySelector("path")
            .classList.toggle(
              "is-selected",
              entry.dataset.connection === selected,
            );
      };
      dot.onclick = choose;
      dot.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      };
      dots.append(dot);
    });
    known = new Set(graph.nodes.map((n) => n.id));
  }
  function update(data) {
    const changed =
      JSON.stringify(graph?.nodes) !== JSON.stringify(data.nodes) ||
      currentRun !== data.runId;
    graph = data;
    if (currentRun !== data.runId) {
      selected = null;
      known.clear();
      initialized = initialized && currentRun === null;
      currentRun = data.runId;
    }
    const working = data.workerConnected && data.state === "active";
    host.classList.toggle("is-working", working);
    let state = data.state
      ? data.state[0].toUpperCase() + data.state.slice(1)
      : "Awaiting a run";
    if (data.state === "active")
      state = data.workerConnected
        ? data.nodes.some((n) => n.state === "running" && n.kind === "thinking")
          ? "Thinking"
          : "Working"
        : "Worker offline";
    $("network-status").textContent = state;
    $("network-mode").textContent =
      data.profile === "demo"
        ? "DEMO · SYNTHETIC ACTIVITY"
        : "RECORDED AGENT ACTIVITY";
    $("network-count").textContent = String(data.totalActions);
    $("network-empty").hidden = data.nodes.length > 0;
    $("network-window").textContent =
      "Latest " + data.nodes.length + " of " + data.totalActions;
    $("network-sync").textContent =
      "Updated " +
      new Date(data.observedAt).toLocaleTimeString([], { hour12: false }) +
      " · local time";
    if (selected && !data.nodes.some((n) => n.id === selected)) selected = null;
    if (selected)
      $("network-detail").textContent = description(
        data.nodes.find((n) => n.id === selected),
      );
    else
      $("network-detail").textContent = data.nodes.length
        ? "Select a dot to inspect its cost and status."
        : "No activity or spend is assumed.";
    if (changed) {
      const focused = document.activeElement?.getAttribute("data-node");
      draw();
      if (focused)
        dots
          .querySelector('[data-node="' + focused + '"]')
          ?.focus({ preventScroll: true });
      const list = $("network-cost-list");
      list.replaceChildren();
      data.nodes.forEach((node, index) => {
        const row = document.createElement("li"),
          name = document.createElement("span"),
          amount = document.createElement("span");
        name.textContent =
          index + 1 + ". " + names[node.kind] + " · " + node.state;
        amount.textContent =
          cost(node) +
          (node.cost.status === "provisional"
            ? " provisional"
            : node.cost.status === "recorded"
              ? " settlement pending"
              : "");
        row.append(name, amount);
        list.append(row);
      });
      if (!data.nodes.length) {
        const row = document.createElement("li");
        row.textContent = "No recorded actions yet.";
        list.append(row);
      }
    }
    initialized = true;
  }
  async function poll() {
    clearTimeout(timer);
    if (document.hidden) return;
    const version = ++pollVersion,
      request = new AbortController();
    controller?.abort();
    controller = request;
    const deadline = setTimeout(() => request.abort(), 5000);
    try {
      const response = await fetch("/api/public/graph", {
        signal: request.signal,
        cache: "no-store",
        credentials: "omit",
      });
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!valid(data)) throw new Error("Invalid activity");
      if (version === pollVersion && !document.hidden) update(data);
    } catch (error) {
      if (version === pollVersion && !document.hidden) {
        host.classList.remove("is-working");
        $("network-status").textContent = "Activity connection lost";
        $("network-sync").textContent = "Last recorded state · reconnecting";
      }
    } finally {
      clearTimeout(deadline);
      if (version === pollVersion) {
        controller = null;
        if (!document.hidden) timer = setTimeout(poll, 4000);
      }
    }
  }
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (document.hidden) {
      pollVersion++;
      controller?.abort();
      controller = null;
      host.classList.remove("is-working");
    } else poll();
  });
  window.addEventListener("pagehide", () => {
    pollVersion++;
    clearTimeout(timer);
    controller?.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) poll();
  });
  let resizeTimer;
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(draw, 100);
  }).observe(svg);
  poll();
})();
`;

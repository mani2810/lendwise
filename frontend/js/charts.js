// Small, dependency-free SVG charts (CSP-safe: built with DOM APIs, colours from CSS variables).
const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const c of children) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

const short = (n) => (Math.abs(n) >= 1e5 ? `${(n / 1e5).toFixed(1)}L` : Math.abs(n) >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : `${Math.round(n)}`);

// Monthly credits vs debits (grouped bars) - the bank statement at a glance.
export function monthlyBars(rows, { height = 190 } = {}) {
  const w = 560, pad = { l: 40, r: 8, t: 14, b: 26 };
  const max = Math.max(1, ...rows.flatMap((r) => [r.credits, r.debits]));
  const bw = (w - pad.l - pad.r) / Math.max(1, rows.length);
  const y = (v) => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  const svg = s("svg", { viewBox: `0 0 ${w} ${height}`, class: "chart", role: "img",
    "aria-label": "Monthly money in and money out" });
  for (let i = 0; i <= 3; i++) {
    const v = (max * i) / 3;
    svg.append(s("line", { x1: pad.l, x2: w - pad.r, y1: y(v), y2: y(v), class: "grid" }),
      s("text", { x: pad.l - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, short(v)));
  }
  rows.forEach((r, i) => {
    const x = pad.l + i * bw + bw * 0.14, bar = bw * 0.34;
    svg.append(
      s("rect", { x, y: y(r.credits), width: bar, height: Math.max(0, y(0) - y(r.credits)), rx: 3, class: "in" },
        s("title", {}, `${r.month} in: ₹${Math.round(r.credits).toLocaleString("en-IN")}`)),
      s("rect", { x: x + bar + 3, y: y(r.debits), width: bar, height: Math.max(0, y(0) - y(r.debits)), rx: 3, class: "out" },
        s("title", {}, `${r.month} out: ₹${Math.round(r.debits).toLocaleString("en-IN")}`)),
      s("text", { x: x + bar, y: height - 8, class: "axis", "text-anchor": "middle" }, r.month.slice(5) + "/" + r.month.slice(2, 4)));
  });
  return svg;
}

// Risk factors: diverging bars around zero (right = raises risk, left = lowers risk).
export function factorBars(factors, { limit = 8 } = {}) {
  const rows = factors.slice(0, limit);
  const w = 560, rowH = 26, labelW = 230, h = rows.length * rowH + 10;
  const max = Math.max(0.01, ...rows.map((f) => Math.abs(f.impact)));
  const mid = labelW + (w - labelW) / 2, half = (w - labelW) / 2 - 48;   // room for value labels
  const svg = s("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img", "aria-label": "Risk factors" });
  svg.append(s("line", { x1: mid, x2: mid, y1: 0, y2: h, class: "zero" }));
  rows.forEach((f, i) => {
    const y = 6 + i * rowH, len = (Math.abs(f.impact) / max) * half;
    const up = f.impact > 0;
    svg.append(
      s("text", { x: labelW - 8, y: y + 15, class: "label", "text-anchor": "end" }, f.label),
      s("rect", { x: up ? mid : mid - len, y: y + 3, width: Math.max(2, len), height: rowH - 10, rx: 3, class: up ? "risk-up" : "risk-down" },
        s("title", {}, `${f.label}: ${fmtVal(f)} (${up ? "raises" : "lowers"} risk)`)),
      s("text", { x: up ? mid + len + 4 : mid - len - 4, y: y + 15, class: "axis", "text-anchor": up ? "start" : "end" }, fmtVal(f)));
  });
  return svg;
}

const PCT = new Set(["foir", "card_utilisation", "spend_to_income"]);
function fmtVal(f) {
  const v = f.value;
  if (f.feature === "salaried") return v ? "yes" : "no";
  if (PCT.has(f.feature)) return `${Math.round(v * 100)}%`;
  if (f.feature === "loan_to_income") return `${v.toFixed(2)}×`;
  if (typeof v !== "number") return String(v);
  if (v > 0 && v < 3 && !Number.isInteger(v)) return v.toFixed(2);
  return v >= 1000 ? Math.round(v).toLocaleString("en-IN") : String(Math.round(v * 100) / 100);
}

// Horizontal gauge, e.g. FOIR vs the policy limit.
export function gauge(value, limit, { max = 1, label = "" } = {}) {
  const w = 260, h = 34, x = (v) => 4 + (Math.min(v, max) / max) * (w - 8);
  const ok = value <= limit;
  return s("svg", { viewBox: `0 0 ${w} ${h}`, class: "gauge", role: "img", "aria-label": `${label} ${Math.round(value * 100)}%` },
    s("rect", { x: 4, y: 10, width: w - 8, height: 10, rx: 5, class: "track" }),
    s("rect", { x: 4, y: 10, width: Math.max(2, x(value) - 4), height: 10, rx: 5, class: ok ? "fill-ok" : "fill-bad" }),
    s("line", { x1: x(limit), x2: x(limit), y1: 5, y2: 25, class: "limit" }),
    s("text", { x: x(limit), y: 34, class: "axis", "text-anchor": "middle" }, `limit ${Math.round(limit * 100)}%`));
}

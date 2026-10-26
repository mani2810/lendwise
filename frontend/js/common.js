// Shared helpers for all LendWise views.
import { auth } from "./api.js";
import { h, icons, toast } from "./ui.js";

export const HOME = { applicant: "#/my", underwriter: "#/queue", admin: "#/dashboard" };
export const inr = (n) => (n == null ? "–" : "₹" + Math.round(n).toLocaleString("en-IN"));
export const pct = (n, d = 0) => (n == null ? "–" : `${(n * 100).toFixed(d)}%`);
export const LABEL = {
  submitted: ["Submitted", "muted"], assessing: ["AI assessing", "busy"], review: ["Under review", "wait"],
  pending_second_approval: ["Second approval", "wait"], offered: ["Offer sent", "ok"], accepted: ["Accepted", "ok"],
  declined: ["Declined", "bad"], declined_by_applicant: ["Offer declined", "muted"], docs_requested: ["Docs requested", "wait"],
  withdrawn: ["Withdrawn", "muted"], error: ["Needs attention", "bad"],
};
export const REC = { approve: ["Approve", "ok"], counter: ["Counter-offer", "wait"], refer: ["Refer", "wait"], decline: ["Decline", "bad"] };
export const pill = (map, key) => {
  const [label, kind] = map[key] || [key || "–", "muted"];
  return h("span", { class: `pill ${kind}` }, kind === "busy" ? h("span", { class: "dot" }) : null, label);
};

export function pageHead(title, ...extra) {
  return h("header", { class: "page-head" },
    h("button", { class: "icon-btn mobile-only", "aria-label": "Menu", onclick: () => document.body.classList.toggle("nav-open"), html: icons.menu }),
    h("h1", {}, title), h("div", { class: "grow" }), ...extra);
}

export async function downloadLetter(a) {
  const res = await fetch(`/api/v1/applications/${a.id}/letter.pdf`, { headers: { Authorization: `Bearer ${auth.token()}` } });
  if (!res.ok) return toast("Download failed", "error");
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: `${a.reference}-letter.pdf` }).click();
  setTimeout(() => URL.revokeObjectURL(url), 20000);
}


// Risk admin: dashboard, credit policy (versioned), audit log, staff.
import { api } from "./api.js";
import { pageHead, pct, pill, REC } from "./common.js";
import { ago, h, toast } from "./ui.js";

export class AdminView {
  constructor(root, page, user) {
    this.root = root;
    this.page = page;
    this.user = user;
    this[page]?.();
  }

  destroy() {}

  async dashboard() {
    let d;
    try { d = await api("/admin/dashboard"); } catch (e) { return toast(e.message, "error"); }
    const t = d.totals, m = d.model || {}, llm = d.llm || {};
    const tile = (label, value, sub) => h("div", { class: "stat" }, h("span", { class: "muted small" }, label), h("strong", {}, value ?? "–"), sub ? h("span", { class: "muted small" }, sub) : null);
    const rateTable = (title, obj) => h("section", { class: "card" }, h("h3", {}, title), h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, ["Group", "Applications", "Decided", "Approval rate"].map((x) => h("th", {}, x)))),
      h("tbody", {}, Object.entries(obj).map(([k, v]) => h("tr", {}, h("td", {}, k.replace("_", "-")), h("td", {}, v.applications), h("td", {}, v.decided), h("td", {}, pct(v.approval_rate)))))));
    const lr = m.candidates?.logistic_regression || {}, xg = m.candidates?.xgboost || {};
    this.root.replaceChildren(h("div", { class: "page" }, pageHead("Lending dashboard"),
      h("div", { class: "stats" },
        tile("Applications", t.applications, `${t.decided} decided`), tile("Approval rate", pct(t.approval_rate), "approved + counter-offers"),
        tile("AI ↔ human agreement", pct(t.ai_human_agreement), `${t.overrides} override(s)`),
        tile("Median time to decision", t.median_minutes_to_decision != null ? `${t.median_minutes_to_decision} min` : "–", "submission → decision"),
        tile("AI calls / assessment", t.avg_llm_calls_per_assessment, llm.ready ? `${llm.mode} · ${Object.values(llm.models || {}).join(", ")}` : llm.problem || "")),
      h("div", { class: "grid2" },
        h("section", { class: "card" }, h("h3", {}, "Pipeline"), h("p", {}, Object.entries(d.by_status).map(([k, v]) => h("span", { class: "chip static" }, `${k.replaceAll("_", " ")}: ${v}`))),
          h("h4", {}, "AI recommendations"), h("p", {}, Object.entries(d.recommendations).map(([k, v]) => h("span", {}, pill(REC, k), ` ${v} `))),
          h("h4", {}, "Risk bands"), h("p", {}, Object.entries(d.bands).sort().map(([k, v]) => h("span", {}, h("span", { class: `band b${k}` }, k), ` ${v}  `)))),
        h("section", { class: "card" }, h("h3", {}, `Risk model · ${m.version || "–"}`),
          h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Model", "ROC-AUC", "PR-AUC", "KS", "Brier"].map((x) => h("th", {}, x)))),
            h("tbody", {}, [["Logistic regression", lr], ["XGBoost", xg]].map(([n, v]) => h("tr", { class: m.chosen?.startsWith(n.split(" ")[0].toLowerCase()) ? "chosen" : "" },
              h("td", {}, n, m.chosen?.startsWith(n.split(" ")[0].toLowerCase()) ? " ✓" : ""), h("td", {}, v.roc_auc), h("td", {}, v.pr_auc), h("td", {}, v.ks), h("td", {}, v.brier))))),
          h("p", { class: "muted small" }, m.selection_rule || ""),
          h("p", { class: "muted small" }, "Observed default rate by band: ", Object.entries(m.bands || {}).map(([b, v]) => `${b} ${pct(v.observed_default_rate, 1)}`).join(" · ")),
          h("p", { class: "muted small" }, "Synthetic training data - demonstration model (see MODEL_CARD.md)."))),
      h("div", { class: "grid2" }, rateTable("Fairness lens · by employment type", d.fairness.by_employment), rateTable("Fairness lens · by city", d.fairness.by_city)),
      h("section", { class: "card" }, h("h3", {}, "Recent overrides (human ≠ AI)"), d.overrides.length ? h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, ["Reference", "AI", "Human", "Reason"].map((x) => h("th", {}, x)))),
        h("tbody", {}, d.overrides.map((o) => h("tr", {}, h("td", {}, o.reference), h("td", {}, o.ai), h("td", {}, o.human), h("td", {}, o.note || ""))))) : h("p", { class: "muted" }, "None yet."))));
  }

  async policy() {
    let versions;
    try { versions = await api("/admin/policy"); } catch (e) { return toast(e.message, "error"); }
    const active = versions.find((v) => v.is_active);
    const inputs = {};
    const group = (kind) => h("section", { class: "card" }, h("h3", {}, kind === "hard" ? "Hard rules (AI and underwriters cannot override)" : "Soft rules (exceptions an underwriter may accept)"),
      h("div", { class: "form-grid" }, Object.entries(active.rules[kind]).map(([id, r]) => {
        const input = typeof r.value === "boolean" ? h("input", { type: "checkbox", checked: r.value }) : h("input", { type: "number", step: "any", value: r.value });
        inputs[`${kind}.${id}`] = input;
        return h("label", { class: typeof r.value === "boolean" ? "check" : "field" }, r.label, input, h("span", { class: "field-error", "data-for": id }));
      })));
    const note = h("input", { placeholder: "What changed and why (required)", maxlength: 300 });
    const save = async () => {
      const rules = JSON.parse(JSON.stringify(active.rules));
      for (const [key, el] of Object.entries(inputs)) {
        const [kind, id] = key.split(".");
        rules[kind][id].value = el.type === "checkbox" ? el.checked : Number(el.value);
      }
      this.root.querySelectorAll(".field-error").forEach((x) => { x.textContent = ""; });
      try {
        const r = await api("/admin/policy", { method: "POST", body: { rules, note: note.value } });
        toast(`Policy v${r.version} is now active`, "ok");
        this.policy();
      } catch (e) {
        const f = e.detail?.fields;
        if (f) Object.entries(f).forEach(([id, msg]) => { const el = this.root.querySelector(`.field-error[data-for="${id}"]`); if (el) el.textContent = msg; });
        else toast(e.message, "error");
      }
    };
    const canEdit = this.user.role === "admin";
    this.root.replaceChildren(h("div", { class: "page" }, pageHead(`Credit policy · v${active.version}`),
      h("p", { class: "muted" }, "Every assessment records the policy version it used. Saving creates a new version; older decisions stay explainable."),
      group("hard"), group("soft"),
      canEdit ? h("div", { class: "card row" }, note, h("button", { class: "btn primary", onclick: save }, "Save as new version")) : null,
      h("section", { class: "card" }, h("h3", {}, "History"), h("ul", { class: "plain" }, versions.map((v) => h("li", {},
        h("strong", {}, `v${v.version}`), v.is_active ? h("span", { class: "pill ok" }, "active") : null, ` · ${ago(v.created_at)} · ${v.note}`))))));
  }

  async audit() {
    let rows;
    try { rows = await api("/admin/audit?limit=300"); } catch (e) { return toast(e.message, "error"); }
    this.root.replaceChildren(h("div", { class: "page" }, pageHead("Audit log"), h("p", { class: "muted" }, "Append-only: every submission, AI assessment, decision, approval and policy change."),
      h("div", { class: "card flush table-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["When", "Application", "Who", "Action", "Details"].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "muted small" }, new Date(r.at).toLocaleString()), h("td", {}, r.reference ? h("a", { href: `#/case/${r.application_id}` }, r.reference) : "–"),
          h("td", {}, r.actor, h("div", { class: "muted small" }, r.role)), h("td", {}, r.action.replaceAll("_", " ")),
          h("td", { class: "small" }, Object.entries(r.details || {}).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => `${k}: ${v}`).join(" · ")))))))));
  }

  async users() {
    let rows;
    try { rows = await api("/admin/users"); } catch (e) { return toast(e.message, "error"); }
    const name = h("input", { placeholder: "Name", required: true }), email = h("input", { type: "email", placeholder: "Email", required: true });
    const pw = h("input", { type: "password", placeholder: "Initial password", autocomplete: "new-password", required: true });
    const role = h("select", {}, h("option", { value: "underwriter" }, "Underwriter"), h("option", { value: "admin" }, "Risk admin"));
    const form = h("form", { class: "card row wrap", onsubmit: async (e) => {
      e.preventDefault();
      try { await api("/admin/users", { method: "POST", body: { name: name.value, email: email.value, password: pw.value, role: role.value } }); toast("Staff user created", "ok"); this.users(); }
      catch (ex) { toast(ex.message, "error"); }
    } }, name, email, pw, role, h("button", { class: "btn primary", type: "submit" }, "Add staff"));
    this.root.replaceChildren(h("div", { class: "page" }, pageHead("Staff & users"), form,
      h("div", { class: "card flush table-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Name", "Email", "Role", "Active", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.map((u) => h("tr", {}, h("td", {}, u.name), h("td", {}, u.email), h("td", {}, u.role), h("td", {}, u.is_active ? "yes" : "no"),
          h("td", {}, u.id === this.user.id ? "" : h("button", { class: "btn ghost small", onclick: async () => {
            try { await api(`/admin/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } }); this.users(); } catch (e) { toast(e.message, "error"); }
          } }, u.is_active ? "Disable" : "Enable")))))))));
  }
}

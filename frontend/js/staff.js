// Underwriter workspace: review queue and the case view (evidence, AI recommendation, what-if, decision).
import { api } from "./api.js";
import { factorBars, gauge, monthlyBars } from "./charts.js";
import { downloadLetter, inr, LABEL, pageHead, pct, pill, REC } from "./common.js";
import { render } from "./markdown.js";
import { ago, h, icon, toast } from "./ui.js";

const AGENT_EMOJI = { underwriter: "🤖", system: "⚙️", guard: "🛡️", decision_engine: "⚖️" };
const TOOL_LABEL = { analyse_bank_statement: "Analysed the bank statement", pull_credit_report: "Pulled the credit report",
  check_affordability: "Checked affordability", score_credit_risk: "Scored credit risk", run_policy_checks: "Ran policy checks" };

export class QueueView {
  constructor(root, user) {
    this.root = root;
    this.user = user;
    this.filter = "open";
    this.q = "";
    this.load();
    this.timer = setInterval(() => this.load(true), 5000);
  }

  destroy() { clearInterval(this.timer); }

  async load(quiet = false) {
    try {
      this.rows = await api(`/applications?status=${encodeURIComponent(this.filter === "all" ? "" : this.filter)}`);
      this.draw();
    } catch (e) { if (!quiet) toast(e.message, "error"); }
  }

  draw() {
    const tabs = [["open", "Open"], ["review", "Under review"], ["pending_second_approval", "Second approval"],
      ["offered", "Offers sent"], ["declined", "Declined"], ["all", "All"]];
    const search = h("input", { type: "search", placeholder: "Search reference, name, city…", value: this.q, "aria-label": "Search",
      oninput: (e) => { this.q = e.target.value.toLowerCase(); this.drawTable(); } });
    this.tableHost = h("div", { class: "table-wrap card flush" });
    this.root.replaceChildren(h("div", { class: "page" },
      pageHead("Review queue", search),
      h("nav", { class: "tabs" }, tabs.map(([k, l]) => h("button", { class: this.filter === k ? "on" : "", onclick: () => { this.filter = k; this.load(); } }, l))),
      this.tableHost));
    this.drawTable();
    search.focus();
    search.setSelectionRange(this.q.length, this.q.length);
  }

  drawTable() {
    const rows = (this.rows || []).filter((r) => !this.q || `${r.reference} ${r.name} ${r.city} ${r.purpose}`.toLowerCase().includes(this.q));
    if (!rows.length) { this.tableHost.replaceChildren(h("p", { class: "empty" }, "Nothing here 🎉")); return; }
    this.tableHost.replaceChildren(h("table", { class: "tbl clickable" },
      h("thead", {}, h("tr", {}, ["Reference", "Applicant", "Amount", "AI recommends", "Risk", "Status", "Received"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.map((r) => h("tr", { tabindex: 0, onclick: () => { location.hash = `#/case/${r.id}`; },
        onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/case/${r.id}`; } },
        h("td", {}, h("strong", {}, r.reference)),
        h("td", {}, r.name, h("div", { class: "muted small" }, `${r.employment_type.replace("_", "-")} · ${r.city}`)),
        h("td", {}, inr(r.loan_amount), h("div", { class: "muted small" }, `${r.tenure_months} mo · ${r.purpose}`)),
        h("td", {}, r.recommendation ? pill(REC, r.recommendation) : h("span", { class: "muted" }, "…")),
        h("td", {}, r.risk_band ? h("span", { class: `band b${r.risk_band}` }, r.risk_band) : "–", r.pd != null ? h("span", { class: "muted small" }, ` ${pct(r.pd, 1)}`) : null),
        h("td", {}, pill(LABEL, r.status), r.override ? h("span", { class: "tag warn", title: "Human decision differs from AI" }, "override") : null),
        h("td", { class: "muted small" }, ago(r.created_at)))))));
  }
}

export class CaseView {
  constructor(root, id, user) {
    this.root = root;
    this.id = id;
    this.user = user;
    this.load();
  }

  destroy() { clearTimeout(this.timer); }

  async load() {
    try {
      this.c = await api(`/applications/${this.id}`);
    } catch (e) {
      this.root.replaceChildren(h("div", { class: "page" }, h("p", { class: "empty" }, e.message), h("a", { href: "#/queue" }, "← Back to the queue")));
      return;
    }
    this.draw();
    const asm = this.c.assessment;
    if (["submitted", "assessing"].includes(this.c.status) || (asm && asm.status === "running")) this.timer = setTimeout(() => this.load(), 1500);
  }

  draw() {
    const c = this.c, a = c.assessment;
    const head = pageHead(`${c.reference} · ${c.name}`, pill(LABEL, c.status), h("a", { class: "btn ghost", href: "#/queue" }, "← Queue"));
    const facts = h("div", { class: "facts" }, ...[
      ["Requested", `${inr(c.loan_amount)} · ${c.tenure_months} mo`], ["Purpose", c.purpose], ["Applicant", `${c.age} y · ${c.employment_type.replace("_", "-")}`],
      ["Employer", c.employer || "–"], ["Declared income", `${inr(c.declared_income)}/mo`], ["PAN", c.pan], ["City", c.city]]
      .map(([k, v]) => h("div", {}, h("span", { class: "muted small" }, k), h("strong", {}, v))));
    if (!a || a.status === "running") {
      this.root.replaceChildren(h("div", { class: "page" }, head, facts, h("div", { class: "card" }, h("h3", {}, h("span", { class: "spinner" }), " AI underwriting agent at work…"),
        this.trace(a?.trace || []))));
      return;
    }
    if (a.status === "failed") {
      this.root.replaceChildren(h("div", { class: "page" }, head, facts, h("div", { class: "card error" }, h("p", {}, "Assessment failed: ", a.error || "unknown error"),
        h("button", { class: "btn primary", onclick: () => this.reassess() }, "Re-assess"))));
      return;
    }
    const rec = a.recommendation, aff = a.affordability, risk = a.risk, st = a.statement, b = a.bureau;
    const recCard = h("section", { class: `card rec rec-${rec.decision}` },
      h("div", { class: "row between" }, h("h3", {}, "AI recommendation"), pill(REC, rec.decision)),
      h("p", { class: "lead-sm" }, rec.summary),
      h("div", { class: "kpis" },
        kpi("Risk band", h("span", { class: `band b${risk.band}` }, risk.band), `PD ${pct(risk.pd, 1)}`),
        kpi("FOIR", pct(aff.foir), `limit ${pct(this.policyValue("max_foir"))}`),
        kpi("Verified income", inr(aff.verified_income), aff.income_basis),
        kpi("Credit score", b.score ?? "No hit", b.source === "synthetic" ? "mock bureau" : "bureau")),
      rec.offer ? h("p", { class: "offer-line" }, "💰 Offer: ", h("strong", {}, `${inr(rec.offer.amount)} · ${rec.offer.tenure_months} mo · ${rec.offer.annual_rate}% · EMI ${inr(rec.offer.emi)}`)) : null,
      rec.reasons?.length ? h("div", {}, h("p", { class: "muted small" }, "Reason codes"), h("ul", { class: "reasons" }, rec.reasons.map((r) => h("li", {}, h("code", {}, r.code), " ", r.text)))) : null,
      h("p", { class: "muted small" }, `Policy v${a.policy_version} · model ${a.model_version} · ${a.llm_calls} AI calls`));

    const tabs = { evidence: "Evidence", memo: "Credit memo", policy: "Policy checks", agent: "Agent trace", audit: "Audit trail" };
    this.tab = this.tab || "evidence";
    const nav = h("nav", { class: "tabs" }, Object.entries(tabs).map(([k, l]) => h("button", { class: this.tab === k ? "on" : "", onclick: () => { this.tab = k; this.draw(); } }, l)));
    const body = {
      evidence: () => h("div", { class: "stack" },
        h("section", { class: "card" }, h("h3", {}, "Bank statement"), h("p", { class: "muted small" }, `${st.period.from} → ${st.period.to} · ${st.period.transactions} transactions`),
          h("div", { class: "legend small" }, h("i", { class: "sw in" }), " money in ", h("i", { class: "sw out" }), " money out"),
          monthlyBars(st.monthly),
          h("div", { class: "facts small-facts" }, ...[["Avg salary", inr(st.avg_salary)], ["Avg money in", inr(st.avg_credits)], ["Avg money out", inr(st.avg_debits)],
            ["EMI debits", inr(st.avg_emi_debits)], ["Spend / income", st.spend_to_income != null ? pct(st.spend_to_income) : "–"], ["Bounces", st.bounces], ["Min balance", inr(st.min_balance)]]
            .map(([k, v]) => h("div", {}, h("span", { class: "muted small" }, k), h("strong", {}, v)))),
          st.flags.length ? h("div", { class: "flags" }, st.flags.map((f) => h("details", { class: "flag" }, h("summary", {}, "🚩 ", f.text),
            f.evidence.length ? h("table", { class: "tbl" }, h("tbody", {}, f.evidence.map((e) => h("tr", {}, h("td", {}, e.date), h("td", {}, h("code", {}, e.narration)), h("td", {}, inr(e.amount)))))) : null))) :
            h("p", { class: "ok-box small" }, "No red flags in the statement.")),
        h("section", { class: "card" }, h("h3", {}, "Affordability"), gauge(aff.foir, this.policyValue("max_foir"), { max: 1, label: "FOIR" }),
          h("p", { class: "small" }, `Existing EMIs ${inr(aff.existing_emi)} + new EMI ${inr(aff.new_emi_at_assessment_rate)} (at ${aff.assessment_rate}% assessment rate) = ${pct(aff.foir)} of verified income. Room for EMIs: ${inr(aff.max_affordable_emi)}/month.`),
          aff.counter_options?.length ? h("p", { class: "muted small" }, "Affordable amounts: ", aff.counter_options.map((o) => `${inr(o.max_amount)} over ${o.tenure_months} mo`).join(" · ")) : null),
        h("section", { class: "card" }, h("h3", {}, "Why this risk score"), h("p", { class: "muted small" }, "Red bars push the probability of default up; green bars pull it down."), factorBars(risk.factors)),
        h("section", { class: "card" }, h("h3", {}, "Credit report"), h("div", { class: "facts small-facts" }, ...[["Score", b.score ?? "–"], ["Card utilisation", pct(b.card_utilisation)],
          ["Active loans", b.active_loans], ["Existing EMIs", inr(b.existing_emi)], ["Worst DPD (12m)", `${b.dpd_max_12m} days`], ["Enquiries (6m)", b.enquiries_6m], ["Write-off", b.writeoff ? "Yes" : "No"]]
          .map(([k, v]) => h("div", {}, h("span", { class: "muted small" }, k), h("strong", {}, v)))),
          b.loans?.length ? h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Loan", "EMI", "Outstanding", "Worst DPD"].map((x) => h("th", {}, x)))),
            h("tbody", {}, b.loans.map((l) => h("tr", {}, h("td", {}, l.type), h("td", {}, inr(l.emi)), h("td", {}, inr(l.outstanding)), h("td", {}, l.dpd_max))))) : null)),
      memo: () => h("section", { class: "card" }, h("div", { class: "md", html: render(a.memo_md || "") }), rec.agent_summary ? h("p", { class: "muted small" }, "Agent notes: ", rec.agent_summary) : null),
      policy: () => h("section", { class: "card" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Rule", "Type", "Threshold", "Applicant", "Result"].map((x) => h("th", {}, x)))),
        h("tbody", {}, a.policy.results.map((r) => h("tr", { class: r.passed ? "" : r.kind === "hard" ? "bad-row" : "warn-row" },
          h("td", {}, r.label), h("td", {}, r.kind), h("td", {}, fmt(r.threshold, r.id)), h("td", {}, fmt(r.actual, r.id)), h("td", {}, r.passed ? "✅ pass" : r.kind === "hard" ? "⛔ fail" : "⚠️ exception")))))),
      agent: () => h("section", { class: "card" }, this.trace(a.trace)),
      audit: () => h("section", { class: "card" }, h("ol", { class: "audit" }, c.audit.map((e) => h("li", {},
        h("span", { class: "muted small" }, new Date(e.at).toLocaleString()), " ", h("strong", {}, e.action.replaceAll("_", " ")), ` · ${e.actor || e.role}`,
        e.details?.note ? h("div", { class: "small" }, "“", e.details.note, "”") : null)))),
    }[this.tab]();

    this.root.replaceChildren(h("div", { class: "page" }, head, facts,
      h("div", { class: "case-grid" }, h("div", { class: "stack" }, recCard, nav, body), h("aside", { class: "stack" }, this.decisionPanel(), this.whatIf(), this.askPanel()))));
  }

  policyValue(id) {
    const r = this.c.assessment.policy.results.find((x) => x.id === id);
    return r ? r.threshold : 0.55;
  }

  trace(steps) {
    if (!steps.length) return h("p", { class: "muted" }, "Starting…");
    return h("ol", { class: "steps" }, steps.map((s) => h("li", { class: `step a-${s.agent}` },
      h("span", { class: "who" }, `${AGENT_EMOJI[s.agent] || "•"} ${s.agent.replace("_", " ")}`),
      h("div", { class: "what" }, s.event === "tool" ? TOOL_LABEL[s.tool] || s.detail : s.detail))));
  }

  decisionPanel() {
    const c = this.c, rec = c.assessment.recommendation;
    if (c.status === "pending_second_approval") {
      const note = h("textarea", { rows: 2, placeholder: "Note (required to send back)" });
      const isAdmin = this.user.role === "admin" && c.decided_by_id !== this.user.id;
      return h("section", { class: "card decide" }, h("h3", {}, "Second approval"),
        h("p", { class: "small" }, `Offer of ${inr(c.offer.amount)} is above the ${inr(c.four_eyes_amount)} four-eyes limit.`),
        isAdmin ? h("div", { class: "stack" }, note, h("div", { class: "row" },
          h("button", { class: "btn primary", onclick: () => this.post("second-approval", { approve: true, note: note.value }) }, "Approve offer"),
          h("button", { class: "btn ghost", onclick: () => this.post("second-approval", { approve: false, note: note.value }) }, "Send back")))
          : h("p", { class: "muted small" }, "Waiting for a risk admin (who did not make the first decision)."));
    }
    if (!["review", "docs_requested"].includes(c.status)) {
      return h("section", { class: "card decide" }, h("h3", {}, "Decision"), h("p", {}, pill(LABEL, c.status)),
        c.offer ? h("p", { class: "small" }, `${inr(c.offer.amount)} · ${c.offer.tenure_months} mo · ${c.offer.annual_rate}% · EMI ${inr(c.offer.emi)}`) : null,
        c.decision_note ? h("p", { class: "small" }, "Note: ", c.decision_note) : null,
        c.override ? h("p", { class: "warn-box small" }, "Human decision differs from the AI recommendation.") : null,
        c.letter_md ? h("button", { class: "btn ghost", onclick: () => downloadLetter(c) }, icon("download"), "Letter PDF") : null);
    }
    const hard = c.assessment.policy.hard_fails.filter((x) => !["max_foir", "max_loan_to_income"].includes(x));
    const action = h("select", { "aria-label": "Decision" },
      h("option", { value: "approve", disabled: hard.length > 0 }, "Approve as requested"),
      h("option", { value: "counter", disabled: hard.length > 0, selected: rec.decision === "counter" }, "Counter-offer"),
      h("option", { value: "decline", selected: rec.decision === "decline" }, "Decline"),
      h("option", { value: "request_docs" }, "Request documents"));
    const amount = h("input", { type: "number", min: 50000, step: 5000, value: rec.offer?.amount || c.loan_amount, "aria-label": "Offer amount" });
    const tenure = h("select", { "aria-label": "Offer tenure" }, [12, 24, 36, 48, 60].map((m) => h("option", { value: m, selected: m === (rec.offer?.tenure_months || c.tenure_months) }, `${m} months`)));
    const counterRow = h("div", { class: "row" }, amount, tenure);
    const note = h("textarea", { rows: 3, maxlength: 1000, placeholder: "Reason / note (required for declines, document requests, referred cases and overrides)" });
    const sync = () => counterRow.classList.toggle("hidden", action.value !== "counter");
    action.addEventListener("change", sync);
    sync();
    return h("section", { class: "card decide" }, h("h3", {}, "Your decision"),
      hard.length ? h("p", { class: "bad-box small" }, "⛔ Hard policy rules failed - approval is blocked: ", hard.join(", ")) : null,
      action, counterRow, note,
      h("button", { class: "btn primary block", onclick: () => this.post("decision", {
        action: action.value, note: note.value, ...(action.value === "counter" ? { amount: Number(amount.value), tenure_months: Number(tenure.value) } : {}),
      }) }, "Submit decision"),
      h("button", { class: "link small", onclick: () => this.reassess() }, "Re-run AI assessment"));
  }

  whatIf() {
    const c = this.c;
    const amount = h("input", { type: "range", min: 50000, max: Math.max(2000000, c.loan_amount * 1.5), step: 10000, value: c.loan_amount, "aria-label": "What-if amount" });
    const tenure = h("input", { type: "range", min: 12, max: 60, step: 12, value: c.tenure_months, "aria-label": "What-if tenure" });
    const out = h("div", { class: "whatif-out" });
    const label = h("p", { class: "small" });
    let t;
    const run = () => {
      label.textContent = `${inr(Number(amount.value))} over ${tenure.value} months`;
      clearTimeout(t);
      t = setTimeout(async () => {
        try {
          const r = await api(`/applications/${c.id}/what-if`, { method: "POST", body: { amount: Number(amount.value), tenure_months: Number(tenure.value) } });
          out.replaceChildren(...[pill(REC, r.decision), h("span", { class: "small" }, ` FOIR ${pct(r.foir)} · band ${r.band} · PD ${pct(r.pd, 1)}`),
            r.offer ? h("div", { class: "small" }, `EMI ${inr(r.offer.emi)} at ${r.offer.annual_rate}%`) : null,
            r.hard_fails.length ? h("div", { class: "small bad" }, "Fails: ", r.hard_fails.join(", ")) : null].filter(Boolean));
        } catch (e) { out.textContent = e.message; }
      }, 180);
    };
    amount.addEventListener("input", run);
    tenure.addEventListener("input", run);
    run();
    return h("section", { class: "card" }, h("h3", {}, "What-if simulator"), h("p", { class: "muted small" }, "Instant, deterministic - no AI call."),
      label, h("label", { class: "small" }, "Amount", amount), h("label", { class: "small" }, "Tenure", tenure), out);
  }

  askPanel() {
    const q = h("input", { placeholder: "e.g. Why is the risk band D?", maxlength: 500, "aria-label": "Ask the AI about this case" });
    const out = h("div", { class: "md small" });
    const ask = async () => {
      if (q.value.trim().length < 3) return;
      out.replaceChildren(h("span", { class: "spinner" }));
      try {
        const r = await api(`/applications/${this.c.id}/ask`, { method: "POST", body: { question: q.value.trim() } });
        out.innerHTML = render(r.answer);
      } catch (e) { out.textContent = e.message; }
    };
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
    return h("section", { class: "card" }, h("h3", {}, "Ask about this case"), h("div", { class: "row nowrap" }, q, h("button", { class: "btn ghost", onclick: ask }, "Ask")), out);
  }

  async post(path, body) {
    try {
      await api(`/applications/${this.c.id}/${path}`, { method: "POST", body });
      toast("Saved", "ok");
      this.load();
    } catch (e) { toast(e.message, "error"); }
  }

  async reassess() {
    try {
      await api(`/applications/${this.c.id}/reassess`, { method: "POST" });
      this.load();
    } catch (e) { toast(e.message, "error"); }
  }
}

function kpi(label, value, sub) {
  return h("div", { class: "kpi" }, h("span", { class: "muted small" }, label), h("strong", {}, value), sub ? h("span", { class: "muted small" }, sub) : null);
}

const PERCENT_RULES = new Set(["max_foir", "max_utilisation", "max_spend_to_income"]);
function fmt(v, id) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "number") return String(v);
  if (PERCENT_RULES.has(id)) return `${Math.round(v * 1000) / 10}%`;
  return (Math.round(v * 100) / 100).toLocaleString("en-IN");
}

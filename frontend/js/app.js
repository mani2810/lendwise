// LendWise SPA: auth, role-based shell, applicant views. Staff views live in staff.js, admin in admin.js.
import { api, auth, onAuthChange, refresh } from "./api.js";
import { render } from "./markdown.js";
import { AdminView } from "./admin.js";
import { CaseView, QueueView } from "./staff.js";
import { downloadLetter, HOME, inr, LABEL, pageHead, pill } from "./common.js";
import { ago, h, icon, icons, toast } from "./ui.js";

const app = document.getElementById("app");
const state = { user: null, meta: {}, view: null };
function theme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("lw-theme", t); } catch { /* private mode */ }
}
let saved = null;
try { saved = localStorage.getItem("lw-theme"); } catch { /* private mode */ }
theme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

onAuthChange((user) => {
  const changed = (user?.id || null) !== (state.user?.id || null);
  state.user = user;
  if (changed) route();
});

async function boot() {
  state.meta = await api("/meta", { auth: false }).catch(() => ({}));
  await refresh();
  if (!state.user) route();
  window.addEventListener("hashchange", route);
}


function route() {
  state.view?.destroy?.();
  state.view = null;
  if (!state.user) return authScreen();
  const role = state.user.role;
  const hash = location.hash || HOME[role];
  shell();
  const m = hash.match(/^#\/case\/(\d+)/);
  if (role === "applicant") {
    if (hash === "#/apply") return applyView();
    return myApplications();
  }
  if (m) return (state.view = new CaseView(state.main, Number(m[1]), state.user));
  if (role === "admin" && ["#/dashboard", "#/policy", "#/audit", "#/users"].includes(hash))
    return (state.view = new AdminView(state.main, hash.slice(2), state.user));
  if (hash === "#/dashboard") return (state.view = new AdminView(state.main, "dashboard", state.user));
  state.view = new QueueView(state.main, state.user);
}

// ------------------------------------------------------------------ auth
function authScreen() {
  let mode = "login";
  const err = h("p", { class: "form-error", role: "alert" });
  const name = h("input", { autocomplete: "name", placeholder: "Your full name", maxlength: 100 });
  const email = h("input", { type: "email", autocomplete: "email", placeholder: "you@example.com", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", placeholder: "Password", required: true });
  const nameRow = h("label", { class: "field hidden" }, "Name", name);
  const submit = h("button", { class: "btn primary block", type: "submit" }, "Log in");
  const toggle = h("button", { type: "button", class: "link" }, "New here? Create an account to apply");
  const form = h("form", { class: "auth-form", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    submit.disabled = true;
    try {
      if (mode === "login") await auth.login(email.value, pw.value);
      else await auth.register(name.value, email.value, pw.value);
      location.hash = HOME[state.user.role];
    } catch (ex) { err.textContent = ex.message; } finally { submit.disabled = false; }
  } }, nameRow, h("label", { class: "field" }, "Email", email), h("label", { class: "field" }, "Password", pw), err, submit,
    h("p", { class: "muted small center" }, toggle));
  toggle.addEventListener("click", () => {
    mode = mode === "login" ? "register" : "login";
    nameRow.classList.toggle("hidden", mode === "login");
    name.required = mode === "register";
    pw.autocomplete = mode === "login" ? "current-password" : "new-password";
    pw.placeholder = mode === "login" ? "Password" : "10+ characters, letters and digits";
    submit.textContent = mode === "login" ? "Log in" : "Create account";
    toggle.textContent = mode === "login" ? "New here? Create an account to apply" : "I already have an account";
  });
  const demo = (label, e, p) => h("button", { type: "button", class: "chip", onclick: () => { email.value = e; pw.value = p; form.requestSubmit(); } }, label);
  app.replaceChildren(h("main", { class: "auth" },
    h("section", { class: "auth-hero" },
      h("div", { class: "logo big" }, h("span", { class: "mark" }, "L"), "LendWise"),
      h("h1", {}, "Loan decisions in ", h("em", {}, "minutes"), ", explained in plain words."),
      h("p", { class: "lead" }, "An AI underwriting agent reads the bank statement and credit report, checks affordability and risk, and drafts a credit memo. Policy rules are enforced in code, and a human underwriter makes the final call."),
      h("ul", { class: "ticks" },
        h("li", {}, "📄 Upload a bank statement (PDF or CSV) - no paperwork"),
        h("li", {}, "🧮 Affordability and counter-offers calculated, not guessed"),
        h("li", {}, "📊 Explainable risk score with reason codes"),
        h("li", {}, "🛡️ Hard policy rules the AI cannot override; four-eyes approval for large loans"),
        h("li", {}, "🔒 The AI never sees your name or PAN"))),
    h("section", { class: "auth-card" }, h("h2", {}, "Welcome to LendWise"), form,
      h("div", { class: "demo" }, h("p", { class: "muted small" }, "Demo accounts (click to fill):"),
        h("div", { class: "row" }, demo("Applicant", "applicant@lendwise.dev", "Applicant2026"),
          demo("Underwriter", "underwriter@lendwise.dev", "Underwrite2026"), demo("Risk admin", "risk@lendwise.dev", "RiskAdmin2026"))),
      state.meta && state.meta.llm_ready === false ? h("p", { class: "warn-box small" }, "⚠️ AI not configured (", state.meta.llm_problem || "", ") - decisions still work via the rules engine.") : null)));
  email.focus();
}

// ------------------------------------------------------------------ shell
function shell() {
  if (state.main && app.contains(state.main)) { markNav(); return; }
  const u = state.user;
  const links = {
    applicant: [["#/my", "📋", "My applications"], ["#/apply", "➕", "New application"]],
    underwriter: [["#/queue", "📥", "Review queue"], ["#/dashboard", "📊", "Dashboard"]],
    admin: [["#/queue", "📥", "Review queue"], ["#/dashboard", "📊", "Dashboard"], ["#/policy", "⚖️", "Credit policy"],
      ["#/audit", "🧾", "Audit log"], ["#/users", "👥", "Staff"]],
  }[u.role];
  state.nav = h("nav", { class: "side-nav" }, links.map(([href, emoji, label]) =>
    h("a", { class: "nav-link", href, onclick: () => document.body.classList.remove("nav-open") }, h("span", {}, emoji), label)));
  const side = h("aside", { class: "sidebar" },
    h("div", { class: "side-top" }, h("a", { class: "logo", href: HOME[u.role] }, h("span", { class: "mark" }, "L"), "LendWise"),
      h("button", { class: "icon-btn", title: "Toggle theme", "aria-label": "Toggle theme",
        onclick: () => theme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"), html: icons.moon })),
    h("span", { class: "role-tag" }, u.role === "admin" ? "Risk admin" : u.role[0].toUpperCase() + u.role.slice(1)),
    state.nav, h("div", { class: "grow" }),
    h("div", { class: "side-foot" }, h("div", { class: "me" }, h("span", { class: "avatar" }, (u.name || "?")[0].toUpperCase()),
      h("div", {}, h("strong", {}, u.name), h("div", { class: "muted small" }, u.email)),
      h("button", { class: "icon-btn", title: "Log out", "aria-label": "Log out", onclick: () => auth.logout(), html: icons.logout }))));
  state.main = h("main", { class: "main" });
  app.replaceChildren(h("div", { class: "layout" }, side, state.main, h("div", { class: "scrim", onclick: () => document.body.classList.remove("nav-open") })));
  markNav();
}

function markNav() {
  state.nav?.querySelectorAll("a").forEach((a) => a.classList.toggle("on", location.hash.startsWith(a.getAttribute("href"))));
}

// ------------------------------------------------------------------ applicant: my applications
async function myApplications() {
  const list = h("div", { class: "stack-lg" });
  state.main.replaceChildren(h("div", { class: "page" }, pageHead("My applications",
    h("a", { class: "btn primary", href: "#/apply" }, icon("plus"), "New application")), list));
  const load = async () => {
    const apps = await api("/me/applications").catch((e) => { toast(e.message, "error"); return []; });
    if (!apps.length) {
      list.replaceChildren(h("div", { class: "card empty" }, h("p", { class: "big" }, "🏦"), h("h3", {}, "No applications yet"),
        h("p", { class: "muted" }, "Apply for a personal loan in about 2 minutes. You'll need a recent bank statement (PDF or CSV)."),
        h("a", { class: "btn primary", href: "#/apply" }, "Apply now")));
      return;
    }
    list.replaceChildren(...apps.map(applicationCard));
    if (apps.some((a) => ["submitted", "assessing"].includes(a.status))) state.timer = setTimeout(load, 2500);
  };
  state.view = { destroy: () => clearTimeout(state.timer) };
  load();

  function applicationCard(a) {
    const steps = ["submitted", "review", "decision"];
    const at = ["submitted", "assessing"].includes(a.status) ? 0 : ["review", "pending_second_approval", "docs_requested", "error"].includes(a.status) ? 1 : 2;
    const body = [];
    if (a.status === "offered" && a.offer) {
      const o = a.offer;
      body.push(h("div", { class: "offer" }, h("h3", {}, "🎉 Your loan offer"),
        h("div", { class: "offer-grid" }, ...[["Amount", inr(o.amount)], ["Tenure", `${o.tenure_months} months`], ["Interest", `${o.annual_rate}% p.a.`],
          ["Monthly EMI", inr(o.emi)], ["Processing fee", inr(o.processing_fee)], ["Total payable", inr(o.total_payable)]]
          .map(([k, v]) => h("div", {}, h("span", { class: "muted small" }, k), h("strong", {}, v)))),
        h("div", { class: "row" },
          h("button", { class: "btn primary", onclick: () => respond(a, true) }, icon("check"), "Accept offer"),
          h("button", { class: "btn ghost", onclick: () => respond(a, false) }, "Decline"))));
    }
    if (a.status === "accepted") body.push(h("p", { class: "ok-box" }, `✅ Offer accepted: ${inr(a.offer.amount)} at ${inr(a.offer.emi)}/month. Disbursal will follow KYC.`));
    if (a.status === "docs_requested") {
      const file = h("input", { type: "file", accept: ".pdf,.csv", "aria-label": "New bank statement" });
      body.push(h("div", { class: "warn-box" }, h("p", {}, "📎 We need more information: ", h("em", {}, a.docs_note || "please upload a clearer statement")),
        h("div", { class: "row" }, file, h("button", { class: "btn primary", onclick: async () => {
          if (!file.files[0]) return toast("Choose a file", "error");
          const fd = new FormData(); fd.append("statement", file.files[0]);
          try { await api(`/applications/${a.id}/statement`, { method: "POST", form: fd }); toast("Uploaded - re-assessing", "ok"); load(); }
          catch (e) { toast(e.message, "error"); }
        } }, "Upload"))));
    }
    if (a.letter_md) {
      body.push(h("details", { class: "letter" }, h("summary", {}, "📨 Read your letter"), h("div", { class: "md", html: render(a.letter_md) }),
        h("button", { class: "btn ghost", onclick: () => downloadLetter(a) }, icon("download"), "Download PDF")));
    }
    if (["submitted", "assessing"].includes(a.status)) body.push(h("p", { class: "muted" }, h("span", { class: "spinner" }), " Our AI is checking your statement and credit report - this takes under a minute."));
    if (a.status === "review" || a.status === "pending_second_approval") body.push(h("p", { class: "muted" }, "An underwriter is reviewing your application. You'll see the decision here."));
    const canWithdraw = ["submitted", "review", "docs_requested", "error"].includes(a.status);
    return h("article", { class: "card app-card" },
      h("div", { class: "row between" }, h("div", {}, h("strong", {}, a.reference), h("span", { class: "muted small" }, ` · ${inr(a.loan_amount)} · ${a.tenure_months} months · ${a.purpose}`)),
        pill(LABEL, a.status)),
      h("ol", { class: "progress" }, steps.map((s, i) => h("li", { class: i < at ? "done" : i === at ? "now" : "" }, ["Submitted", "Review", "Decision"][i]))),
      ...body,
      h("div", { class: "row between" }, h("span", { class: "muted small" }, `Applied ${ago(a.created_at)}`),
        canWithdraw ? h("button", { class: "link danger-text", onclick: async () => {
          if (!confirm("Withdraw this application?")) return;
          await api(`/applications/${a.id}/withdraw`, { method: "POST" }).catch((e) => toast(e.message, "error")); load();
        } }, "Withdraw") : null));
  }

  async function respond(a, accept) {
    try { await api(`/applications/${a.id}/offer`, { method: "POST", body: { accept } }); toast(accept ? "Offer accepted 🎉" : "Offer declined", "ok"); load(); }
    catch (e) { toast(e.message, "error"); }
  }
}

// ------------------------------------------------------------------ applicant: apply
async function applyView() {
  const purposes = state.meta.purposes || ["other"];
  const f = {};
  const field = (key, label, input, hint) => {
    f[key] = input;
    return h("label", { class: "field" }, label, input, hint ? h("span", { class: "muted small" }, hint) : null, h("span", { class: "field-error", "data-for": key }));
  };
  const fileIn = h("input", { type: "file", accept: ".pdf,.csv", required: true, "aria-label": "Bank statement" });
  const fileNote = h("span", { class: "muted small" }, "Last 6 months, PDF or CSV, up to 3 MB.");
  let sampleFile = null;
  const form = h("form", { class: "card form-grid", onsubmit: submit },
    h("h3", { class: "span2" }, "1 · About you"),
    field("full_name", "Full name (as on PAN)", h("input", { required: true, maxlength: 100 })),
    field("pan", "PAN", h("input", { required: true, maxlength: 10, placeholder: "ABCDE1234F", pattern: "[A-Za-z]{5}[0-9]{4}[A-Za-z]", class: "upper" }), "Used only to fetch your credit report. Stored as a hash; staff see it masked."),
    field("date_of_birth", "Date of birth", h("input", { type: "date", required: true })),
    field("city", "City", h("input", { required: true, maxlength: 60 })),
    h("h3", { class: "span2" }, "2 · Work & income"),
    field("employment_type", "Employment", h("select", {}, h("option", { value: "salaried" }, "Salaried"), h("option", { value: "self_employed" }, "Self-employed"))),
    field("employer", "Employer / business name", h("input", { maxlength: 120 })),
    field("years_employed", "Years in current job / business", h("input", { type: "number", min: 0, max: 50, step: 0.1, required: true })),
    field("declared_income", "Monthly income (₹, take-home)", h("input", { type: "number", min: 5000, step: 1000, required: true })),
    h("h3", { class: "span2" }, "3 · Your loan"),
    field("loan_amount", "Amount (₹)", h("input", { type: "number", min: 50000, max: 4000000, step: 5000, required: true })),
    field("tenure_months", "Tenure (months)", h("select", {}, [12, 24, 36, 48, 60].map((m) => h("option", { value: m, selected: m === 36 }, `${m} months`)))),
    field("purpose", "Purpose", h("select", {}, purposes.map((p) => h("option", { value: p }, p[0].toUpperCase() + p.slice(1))))),
    h("label", { class: "field" }, "Bank statement", fileIn, fileNote, h("span", { class: "field-error", "data-for": "statement" })),
    h("label", { class: "check span2" }, (f.consent = h("input", { type: "checkbox", required: true })),
      "I consent to LendWise fetching my credit report and analysing my bank statement to assess this application.", h("span", { class: "field-error", "data-for": "consent" })),
    h("div", { class: "span2 row end" }, h("button", { class: "btn primary", type: "submit" }, "Submit application")));
  fileIn.addEventListener("change", () => { sampleFile = null; fileNote.textContent = fileIn.files[0]?.name || ""; });

  const samples = await api("/samples").catch(() => []);
  const sampleBar = h("div", { class: "card sample-bar" }, h("p", {}, h("strong", {}, "Just exploring? "), "Fill the form with a fictional applicant and their bank statement:"),
    h("div", { class: "row" }, samples.map((s) => h("button", { type: "button", class: "chip", title: s.expect, onclick: () => useSample(s) },
      `${s.name} · ${inr(s.loan_amount)}`))));
  state.main.replaceChildren(h("div", { class: "page narrow" }, pageHead("Apply for a personal loan"), sampleBar, form));

  async function useSample(s) {
    const map = { full_name: s.name, pan: s.pan, date_of_birth: s.dob, city: s.city, employment_type: s.employment_type, employer: s.employer,
      years_employed: s.years_employed, declared_income: s.declared_income, loan_amount: s.loan_amount, tenure_months: s.tenure_months, purpose: s.purpose };
    for (const [k, v] of Object.entries(map)) f[k].value = v;
    const res = await fetch(`/api/v1/samples/${s.key}/pdf`, { headers: { Authorization: `Bearer ${auth.token()}` } });
    sampleFile = new File([await res.blob()], `${s.key}_statement.pdf`, { type: "application/pdf" });
    fileIn.value = "";
    fileIn.required = false;
    fileNote.textContent = `📎 ${sampleFile.name} (sample statement attached)`;
    toast(`Filled with ${s.name} - expected outcome: ${s.expect}`);
  }

  async function submit(e) {
    e.preventDefault();
    form.querySelectorAll(".field-error").forEach((x) => { x.textContent = ""; });
    const file = sampleFile || fileIn.files[0];
    if (!file) return showErr("statement", "Attach your bank statement");
    const fd = new FormData();
    for (const [k, el] of Object.entries(f)) fd.append(k, k === "consent" ? String(el.checked) : k === "pan" ? el.value.toUpperCase() : el.value);
    fd.append("statement", file);
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api("/applications", { method: "POST", form: fd });
      toast("Application submitted ✔", "ok");
      location.hash = "#/my";
    } catch (ex) {
      const fields = ex.detail?.fields;
      if (fields) Object.entries(fields).forEach(([k, v]) => showErr(k, v));
      else toast(ex.message, "error");
    } finally { btn.disabled = false; }
  }
  function showErr(key, msg) {
    const el = form.querySelector(`.field-error[data-for="${key}"]`);
    if (el) el.textContent = msg; else toast(msg, "error");
  }
}


boot();

"""Deterministic underwriting: bureau, affordability, features, policy rules, decision matrix, pricing.

Everything that decides money is here, in code, versioned and unit-tested. The LLM agent orchestrates
these tools and writes the narrative; it can never change a number or override a hard rule.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date

from ..ml.features import BAND_RATE

ASSESSMENT_RATE = 13.0          # % p.a. used to test affordability before the risk-based price is known
PROCESSING_FEE = 0.015
MIN_LOAN = 50_000

DEFAULT_RULES = {
    "hard": {
        "min_cibil": {"value": 650, "label": "Credit score at least"},
        "max_foir": {"value": 0.55, "label": "Total EMIs / income at most"},
        "max_dpd_12m": {"value": 30, "label": "Worst days-past-due (12 months) at most"},
        "no_writeoff": {"value": True, "label": "No written-off / settled loans"},
        "min_age": {"value": 21, "label": "Age at least"},
        "max_age_at_maturity": {"value": 60, "label": "Age at loan maturity at most"},
        "min_income": {"value": 25_000, "label": "Verified monthly income at least (₹)"},
        "max_bounces_6m": {"value": 2, "label": "Bounced payments in the statement at most"},
        "max_loan_to_income": {"value": 2.5, "label": "Loan / annual income at most"},
    },
    "soft": {
        "max_utilisation": {"value": 0.75, "label": "Credit-card utilisation at most"},
        "max_enquiries_6m": {"value": 4, "label": "Credit enquiries in 6 months at most"},
        "max_spend_to_income": {"value": 0.9, "label": "Spending / income at most"},
        "min_years_employed": {"value": 1.0, "label": "Years in current job at least"},
        "no_gambling": {"value": True, "label": "No betting / gaming payments"},
    },
    "decision": {"approve_bands": ["A", "B"], "refer_bands": ["C", "D"], "max_soft_fails_to_approve": 1},
}


def pan_hash(pan: str) -> str:
    return hashlib.sha256(("lendwise|" + pan.strip().upper()).encode()).hexdigest()


def mask_pan(pan: str) -> str:
    p = pan.strip().upper()
    return p[:2] + "x" * 5 + p[7:] if len(p) == 10 else "xxxxxxxxxx"


def age_on(dob: str, today: date | None = None) -> int:
    d = date.fromisoformat(dob)
    t = today or date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


# ---------------------------------------------------------------- bureau
def synthetic_bureau(pan_h: str) -> dict:
    """Mock bureau for PANs it has never seen: a deterministic profile derived from the PAN hash, so any
    PAN typed in the demo gets a stable, plausible report. (A real deployment calls CIBIL/Experian.)"""
    k = int(pan_h[:12], 16)
    score = 600 + k % 260
    limit = [50_000, 100_000, 150_000, 250_000][k % 4]
    balance = round(limit * ((k >> 8) % 90) / 100, -2)
    loans = [] if (k >> 4) % 3 == 0 else [{"type": "consumer durable", "emi": 2_500 + (k % 7) * 500,
                                           "outstanding": 20_000 + (k % 9) * 5_000, "dpd_max": 0}]
    return {"score": score, "cards": [{"limit": limit, "balance": balance, "paid_full_share": round((k % 10) / 10, 1)}],
            "loans": loans, "dpd_max_12m": 30 if score < 640 else 0, "enquiries_6m": (k >> 3) % 5,
            "writeoff": False, "source": "synthetic"}


def summarise_bureau(report: dict) -> dict:
    limits = sum(c["limit"] for c in report.get("cards", []))
    balances = sum(c["balance"] for c in report.get("cards", []))
    return {"score": report.get("score"), "card_utilisation": round(balances / limits, 3) if limits else 0.0,
            "card_limit_total": limits, "existing_emi": sum(lo["emi"] for lo in report.get("loans", [])),
            "active_loans": len(report.get("loans", [])), "dpd_max_12m": report.get("dpd_max_12m", 0),
            "enquiries_6m": report.get("enquiries_6m", 0), "writeoff": bool(report.get("writeoff")),
            "loans": report.get("loans", []), "source": report.get("source", "bureau")}


# ---------------------------------------------------------------- affordability
def emi(principal: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 1200
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def principal_for(emi_amount: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 1200
    return emi_amount * ((1 + r) ** months - 1) / (r * (1 + r) ** months) if r else emi_amount * months


def verified_income(app: dict, stmt: dict) -> tuple[float, str]:
    declared = app["declared_income"]
    if app["employment_type"] == "salaried":
        if stmt.get("avg_salary"):
            return min(declared, stmt["avg_salary"]), "salary credits in the bank statement"
        return declared * 0.7, "declared income (no salary credits found - 30% haircut)"
    business = stmt.get("avg_credits", 0) * 0.5            # self-employed: ~50% of turnover as income
    return (min(declared, business) if business else declared * 0.6), "half of average monthly bank credits"


def affordability(app: dict, stmt: dict, bureau: dict, max_foir: float = 0.55) -> dict:
    income, basis = verified_income(app, stmt)
    existing = max(bureau["existing_emi"], stmt.get("avg_emi_debits", 0) or 0)
    new_emi = emi(app["loan_amount"], ASSESSMENT_RATE, app["tenure_months"])
    foir = (existing + new_emi) / income if income else 9.99
    room = max(0.0, max_foir * income - existing)
    options = []
    for months in sorted({app["tenure_months"], 36, 48, 60}):
        if months >= app["tenure_months"]:
            amt = math.floor(min(principal_for(room, ASSESSMENT_RATE, months), app["loan_amount"]) / 5_000) * 5_000
            options.append({"tenure_months": months, "max_amount": max(0, amt)})
    return {"verified_income": round(income, 2), "income_basis": basis, "existing_emi": round(existing, 2),
            "new_emi_at_assessment_rate": round(new_emi, 2), "assessment_rate": ASSESSMENT_RATE,
            "foir": round(foir, 3), "max_affordable_emi": round(room, 2),
            "loan_to_income": round(app["loan_amount"] / (income * 12), 3) if income else 9.99,
            "counter_options": options}


def features(app: dict, stmt: dict, bureau: dict, aff: dict) -> dict:
    return {"cibil_score": bureau["score"] or 650, "monthly_income": aff["verified_income"], "foir": aff["foir"],
            "card_utilisation": bureau["card_utilisation"], "dpd_max_12m": bureau["dpd_max_12m"],
            "enquiries_6m": bureau["enquiries_6m"], "bounces_6m": stmt.get("bounces", 0),
            "spend_to_income": stmt.get("spend_to_income") or 0.8, "years_employed": app["years_employed"],
            "age": age_on(app["date_of_birth"]), "salaried": 1 if app["employment_type"] == "salaried" else 0,
            "loan_to_income": aff["loan_to_income"]}


# ---------------------------------------------------------------- policy
def check_policy(rules: dict, app: dict, stmt: dict, bureau: dict, aff: dict) -> dict:
    age = age_on(app["date_of_birth"])
    h, s = rules["hard"], rules["soft"]
    facts = {
        "min_cibil": (bureau["score"] or 0, lambda v, t: v >= t),
        "max_foir": (aff["foir"], lambda v, t: v <= t),
        "max_dpd_12m": (bureau["dpd_max_12m"], lambda v, t: v <= t),
        "no_writeoff": (not bureau["writeoff"], lambda v, t: v or not t),
        "min_age": (age, lambda v, t: v >= t),
        "max_age_at_maturity": (round(age + app["tenure_months"] / 12, 1), lambda v, t: v <= t),
        "min_income": (aff["verified_income"], lambda v, t: v >= t),
        "max_bounces_6m": (stmt.get("bounces", 0), lambda v, t: v <= t),
        "max_loan_to_income": (aff["loan_to_income"], lambda v, t: v <= t),
        "max_utilisation": (bureau["card_utilisation"], lambda v, t: v <= t),
        "max_enquiries_6m": (bureau["enquiries_6m"], lambda v, t: v <= t),
        "max_spend_to_income": (stmt.get("spend_to_income") or 0, lambda v, t: v <= t),
        "min_years_employed": (app["years_employed"], lambda v, t: v >= t),
        "no_gambling": (stmt.get("gambling_txns", 0) == 0, lambda v, t: v or not t),
    }
    results = []
    for kind, group in (("hard", h), ("soft", s)):
        for rid, rule in group.items():
            if rid not in facts:
                continue
            actual, ok = facts[rid]
            results.append({"id": rid, "kind": kind, "label": rule["label"], "threshold": rule["value"],
                            "actual": actual, "passed": bool(ok(actual, rule["value"]))})
    if bureau["score"] is None:
        results.append({"id": "bureau_hit", "kind": "hard", "label": "Credit bureau record found",
                        "threshold": True, "actual": False, "passed": False})
    hard_fails = [r for r in results if r["kind"] == "hard" and not r["passed"]]
    soft_fails = [r for r in results if r["kind"] == "soft" and not r["passed"]]
    return {"results": results, "hard_fails": [r["id"] for r in hard_fails],
            "soft_fails": [r["id"] for r in soft_fails]}


AFFORDABILITY_RULES = {"max_foir", "max_loan_to_income"}
REASONS = {
    "min_cibil": "Credit score is below our minimum",
    "max_foir": "Existing and new EMIs would take too large a share of income",
    "max_dpd_12m": "Recent delayed payments on existing credit",
    "no_writeoff": "A previous loan was written off or settled",
    "min_age": "Applicant is below the minimum age",
    "max_age_at_maturity": "Loan would mature after the maximum age",
    "min_income": "Verified income is below our minimum",
    "max_bounces_6m": "Several bounced payments in the bank statement",
    "max_loan_to_income": "Requested amount is high relative to income",
    "max_utilisation": "High credit-card utilisation",
    "max_enquiries_6m": "Many recent credit applications",
    "max_spend_to_income": "Spending is high relative to income",
    "min_years_employed": "Short time in current employment",
    "no_gambling": "Payments to betting or gaming merchants",
    "bureau_hit": "No credit history found with the bureau",
}


def decide(rules: dict, app: dict, aff: dict, risk: dict, policy: dict) -> dict:
    """The decision matrix. Returns recommendation + reason codes + priced offer (if any)."""
    d = rules["decision"]
    band, hard, soft = risk["band"], policy["hard_fails"], policy["soft_fails"]
    reasons = [{"code": r.upper(), "text": REASONS.get(r, r)} for r in hard + soft]
    risk_drivers = [f["label"] for f in risk["factors"] if f["impact"] > 0.15][:3]

    def offer(amount: float, months: int) -> dict:
        rate = BAND_RATE[band]
        e = emi(amount, rate, months)
        return {"amount": amount, "tenure_months": months, "annual_rate": rate, "emi": round(e, 2),
                "processing_fee": round(amount * PROCESSING_FEE, 2), "total_payable": round(e * months, 2)}

    if hard:
        only_affordability = set(hard) <= AFFORDABILITY_RULES
        if only_affordability and band not in ("E",):
            best = max(aff["counter_options"], key=lambda o: (o["max_amount"], -o["tenure_months"]), default=None)
            if best and best["max_amount"] >= max(MIN_LOAN, 0.3 * app["loan_amount"]):
                if best["max_amount"] >= app["loan_amount"]:
                    summary = (f"Not affordable over {app['tenure_months']} months; the full amount is affordable "
                               f"over {best['tenure_months']} months.")
                else:
                    summary = (f"Requested amount is not affordable; can offer ₹{best['max_amount']:,.0f} over "
                               f"{best['tenure_months']} months.")
                return {"decision": "counter", "summary": summary,
                        "reasons": reasons, "risk_drivers": risk_drivers,
                        "offer": offer(best["max_amount"], best["tenure_months"])}
        return {"decision": "decline", "summary": "Fails hard policy rule(s): " + ", ".join(hard) + ".",
                "reasons": reasons, "risk_drivers": risk_drivers, "offer": None}
    if band in d["approve_bands"] and len(soft) <= d["max_soft_fails_to_approve"]:
        return {"decision": "approve", "summary": f"Meets policy; risk band {band}.", "reasons": reasons,
                "risk_drivers": risk_drivers, "offer": offer(app["loan_amount"], app["tenure_months"])}
    if band in d["approve_bands"] + d["refer_bands"]:
        why = f"risk band {band}" + (f" and {len(soft)} soft-rule exception(s)" if soft else "")
        return {"decision": "refer", "summary": f"Needs underwriter judgement: {why}.", "reasons": reasons,
                "risk_drivers": risk_drivers, "offer": offer(app["loan_amount"], app["tenure_months"])}
    return {"decision": "decline", "summary": f"Risk too high (band {band}, PD {risk['pd']:.0%}).",
            "reasons": reasons + [{"code": "HIGH_RISK", "text": "Overall credit risk is too high"}],
            "risk_drivers": risk_drivers, "offer": None}

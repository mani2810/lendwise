"""Bank-statement analyser (CSV or text PDF) -> income, spending, EMIs, bounces and red flags.

Narrations are UNTRUSTED text written by third parties (anyone who sends money can choose it). They are
used only for keyword categorisation here and are never passed to the LLM except as short, quoted
evidence snippets.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

SALARY = re.compile(r"\b(salary|sal\b|payroll|wages|stipend)", re.I)
EMI = re.compile(r"\b(emi|nach|ecs|loan|si-|standing instr|mandate)", re.I)
BOUNCE = re.compile(r"(return|bounce|insuff|dishono|unpaid|rtn chg|chq ret|ecs ret|nach ret)", re.I)
CASH = re.compile(r"\b(atm|cash wdl|cash withdrawal|cwdr)", re.I)
GAMBLING = re.compile(r"(dream11|my11circle|rummy|poker|casino|betting|\bbet\b|stake\.com|teen ?patti)", re.I)
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d.%m.%Y")


class StatementError(ValueError):
    pass


@dataclass
class Txn:
    day: date
    narration: str
    amount: float          # + credit, - debit
    balance: float | None


def _date(s: str) -> date | None:
    s = s.strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _num(s: str | None) -> float:
    s = (s or "").replace(",", "").replace("₹", "").replace("INR", "").strip()
    if s in ("", "-"):
        return 0.0
    neg = s.endswith(("DR", "Dr")) or s.startswith("-")
    s = re.sub(r"(?i)\s*(cr|dr)$", "", s).lstrip("-")
    return -float(s) if neg else float(s)


def parse_csv(data: bytes) -> list[Txn]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    head_i = next((i for i, r in enumerate(rows[:15]) if any("date" in c.lower() for c in r)), None)
    if head_i is None:
        raise StatementError("Could not find a header row with a Date column.")
    head = [c.strip().lower() for c in rows[head_i]]

    def col(*names):
        return next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    c_date, c_desc = col("date"), col("narration", "description", "particular", "details", "remarks")
    c_dr, c_cr = col("debit", "withdrawal", "dr"), col("credit", "deposit", "cr")
    c_amt, c_bal = col("amount"), col("balance")
    if c_date is None or (c_dr is None and c_amt is None):
        raise StatementError("Statement needs Date and Debit/Credit (or Amount) columns.")
    out = []
    for r in rows[head_i + 1:]:
        if len(r) <= c_date or not (d := _date(r[c_date])):
            continue
        if c_dr is not None and c_cr is not None:
            amt = _num(r[c_cr] if c_cr < len(r) else "") - abs(_num(r[c_dr] if c_dr < len(r) else ""))
        else:
            amt = _num(r[c_amt])
        bal = _num(r[c_bal]) if c_bal is not None and c_bal < len(r) and r[c_bal].strip() else None
        out.append(Txn(d, (r[c_desc] if c_desc is not None and c_desc < len(r) else "")[:200], amt, bal))
    return out


LINE = re.compile(r"^(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[- ][A-Za-z]{3}[- ]\d{4})\s+(.*?)\s+"
                  r"([\d,]+\.\d{2})(?:\s*(CR|DR|Cr|Dr))?\s+(-?[\d,]+\.\d{2})(?:\s*(CR|DR|Cr|Dr))?\s*$")


def parse_pdf(data: bytes) -> list[Txn]:
    """Text PDFs: each line = date, narration, amount, balance. Credit/debit is taken from an explicit
    CR/DR marker, else inferred from the change in balance (works for most Indian bank layouts)."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise StatementError("The PDF is password-protected. Please upload an unlocked statement.")
        lines = "\n".join((p.extract_text() or "") for p in reader.pages[:40]).splitlines()
    except StatementError:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed PDFs are a user error, not a crash
        raise StatementError("Could not read this PDF.") from exc
    out: list[Txn] = []
    prev_bal: float | None = None
    for line in lines:
        m = LINE.match(line.strip())
        if not m:
            ob = re.search(r"opening balance\s*:?\s*([\d,]+\.\d{2})", line, re.I)
            if ob:
                prev_bal = _num(ob.group(1))
            continue
        d = _date(m.group(1).replace(".", "-"))
        if not d:
            continue
        amt, bal = _num(m.group(3)), _num(m.group(5))
        if (m.group(6) or "").upper() == "DR":            # "1,234.00 DR" balance = overdrawn
            bal = -abs(bal)
        marker = (m.group(4) or "").upper()
        if marker == "DR" or (not marker and prev_bal is not None and bal < prev_bal):
            amt = -amt
        out.append(Txn(d, m.group(2)[:200], amt, bal))
        prev_bal = bal
    if not out:
        raise StatementError("No transactions found. Upload a text (not scanned) PDF or a CSV export.")
    return out


def parse(data: bytes, filename: str) -> list[Txn]:
    name = filename.lower()
    if name.endswith(".csv"):
        txns = parse_csv(data)
    elif name.endswith(".pdf"):
        txns = parse_pdf(data)
    else:
        raise StatementError("Upload the statement as CSV or PDF.")
    if len(txns) < 5:
        raise StatementError("The statement has too few transactions to assess.")
    return sorted(txns, key=lambda t: t.day)


def _snippet(t: Txn) -> dict:
    """Evidence for humans and the LLM: short, cleaned, clearly a quote."""
    clean = re.sub(r"[^\w\s/@.&-]", " ", t.narration)
    clean = re.sub(r"\d{6,}", lambda m: "x" * 4 + m.group(0)[-4:], clean)        # mask long account numbers
    return {"date": t.day.isoformat(), "narration": " ".join(clean.split())[:60], "amount": round(t.amount, 2)}


def analyse(txns: list[Txn], declared_income: float) -> dict:
    months: dict[str, dict] = defaultdict(lambda: {"credits": 0.0, "debits": 0.0, "salary": 0.0, "emi": 0.0})
    salary_txns, emi_txns, bounces, cash, gambling = [], [], [], [], []
    for t in txns:
        key = t.day.strftime("%Y-%m")
        m = months[key]
        if t.amount >= 0:
            m["credits"] += t.amount
            if SALARY.search(t.narration):
                m["salary"] += t.amount
                salary_txns.append(t)
        else:
            m["debits"] += -t.amount
            if BOUNCE.search(t.narration):
                bounces.append(t)
            elif EMI.search(t.narration):
                m["emi"] += -t.amount
                emi_txns.append(t)
            if CASH.search(t.narration):
                cash.append(t)
            if GAMBLING.search(t.narration):
                gambling.append(t)
    keys = sorted(months)
    full = keys[1:-1] if len(keys) > 3 else keys            # first/last month are usually partial
    n = max(1, len(full))
    avg = lambda f: sum(months[k][f] for k in full) / n  # noqa: E731
    salary_months = sum(1 for k in full if months[k]["salary"] > 0)
    avg_salary = (sum(months[k]["salary"] for k in full) / salary_months) if salary_months else 0.0
    avg_credits, avg_debits, avg_emi = avg("credits"), avg("debits"), avg("emi")
    balances = [t.balance for t in txns if t.balance is not None]
    bounce_charges = [t for t in bounces if t.amount < 0]
    income_basis = avg_salary or avg_credits
    flags = []
    if len(bounce_charges):
        flags.append({"code": "BOUNCES", "text": f"{len(bounce_charges)} bounced/returned payment(s)",
                      "evidence": [_snippet(t) for t in bounce_charges[:4]]})
    if gambling:
        flags.append({"code": "GAMBLING", "text": f"{len(gambling)} payment(s) to betting/gaming merchants",
                      "evidence": [_snippet(t) for t in gambling[:4]]})
    if income_basis and avg_debits > income_basis * 1.0:
        flags.append({"code": "OVERSPEND", "text": "Average monthly spending exceeds income", "evidence": []})
    if balances and min(balances) < 0:
        flags.append({"code": "NEGATIVE_BALANCE", "text": "Account went overdrawn", "evidence": []})
    if declared_income and avg_salary and avg_salary < declared_income * 0.8:
        flags.append({"code": "INCOME_MISMATCH", "text": f"Salary credits (₹{avg_salary:,.0f}) are well below "
                                                         f"declared income (₹{declared_income:,.0f})", "evidence": []})
    if salary_months and salary_months < max(1, len(full) - 1):
        flags.append({"code": "IRREGULAR_SALARY", "text": f"Salary found in only {salary_months} of {len(full)} "
                                                          "full months", "evidence": []})
    return {
        "period": {"from": txns[0].day.isoformat(), "to": txns[-1].day.isoformat(), "months": len(keys),
                   "full_months": len(full), "transactions": len(txns)},
        "monthly": [{"month": k, **{f: round(v, 2) for f, v in months[k].items()}} for k in keys],
        "avg_salary": round(avg_salary, 2), "salary_months": salary_months,
        "avg_credits": round(avg_credits, 2), "avg_debits": round(avg_debits, 2),
        "avg_emi_debits": round(avg_emi, 2),
        # EMIs are excluded here: they are already counted in FOIR
        "spend_to_income": round((avg_debits - avg_emi) / income_basis, 3) if income_basis else None,
        "bounces": len(bounce_charges), "cash_withdrawals": len(cash), "gambling_txns": len(gambling),
        "avg_balance": round(sum(balances) / len(balances), 2) if balances else None,
        "min_balance": round(min(balances), 2) if balances else None,
        "flags": flags,
    }

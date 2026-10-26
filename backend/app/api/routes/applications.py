"""Applications: applicants apply and track; underwriters review, simulate, ask, and decide."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from ...agent import underwriting as U
from ...core.ratelimit import limiter
from ...db.models import OPEN_FOR_REVIEW, Application, AuditEvent, Document, User
from ...llm.gateway import LLMUnavailable
from ...renderers.base import RenderInput
from ...renderers.documents import render_pdf
from ...schemas import DecisionIn, OfferResponseIn, QuestionIn, SecondApprovalIn, WhatIfIn
from ...services import lending as L
from ..deps import DB, Admin, Applicant, CurrentUser, Staff, VisibleApplication

router = APIRouter(tags=["applications"])
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
STATUS_LABEL = {"submitted": "Submitted", "assessing": "Being assessed", "review": "Under review",
                "pending_second_approval": "Final approval", "offered": "Offer ready", "accepted": "Accepted",
                "declined": "Declined", "declined_by_applicant": "Offer declined", "docs_requested":
                "Documents needed", "withdrawn": "Withdrawn", "error": "Under review"}


def applicant_view(a: Application) -> dict:
    """What the applicant sees: their own data, status, offer and letter - no internal scores or memo."""
    return {"id": a.id, "reference": a.reference, "status": a.status, "status_label": STATUS_LABEL.get(a.status),
            "loan_amount": a.loan_amount, "tenure_months": a.tenure_months, "purpose": a.purpose,
            "offer": a.offer if a.status in ("offered", "accepted", "declined_by_applicant") else None,
            "letter_md": a.letter_md, "docs_note": a.decision_note if a.status == "docs_requested" else None,
            "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat()}


def staff_row(a: Application) -> dict:
    return {"id": a.id, "reference": a.reference, "status": a.status, "name": a.full_name, "pan": a.pan_masked,
            "city": a.city, "employment_type": a.employment_type, "loan_amount": a.loan_amount,
            "tenure_months": a.tenure_months, "purpose": a.purpose, "recommendation": a.recommendation,
            "risk_band": a.risk_band, "pd": a.pd, "decision": a.decision, "override": a.override,
            "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat()}


# ---------------------------------------------------------------- applicant
@router.post("/applications", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def apply(request: Request, db: DB, user: Applicant, statement: UploadFile = File(...),
                full_name: str = Form(...), pan: str = Form(...), date_of_birth: str = Form(...),
                city: str = Form(...), employment_type: str = Form(...), employer: str = Form(""),
                years_employed: float = Form(...), declared_income: float = Form(...),
                loan_amount: float = Form(...), tenure_months: int = Form(...), purpose: str = Form(...),
                consent: str = Form("false")):
    data = await statement.read(int(3.5 * 1_048_576))
    form = {"full_name": full_name, "pan": pan, "date_of_birth": date_of_birth, "city": city,
            "employment_type": employment_type, "employer": employer, "years_employed": years_employed,
            "declared_income": declared_income, "loan_amount": loan_amount, "tenure_months": tenure_months,
            "purpose": purpose, "consent": consent}
    a = await asyncio.to_thread(L.create_application, db, user, form, statement.filename or "statement", data)
    L.queue_assessment(a.id)
    return applicant_view(a)


@router.get("/me/applications")
def my_applications(db: DB, user: Applicant):
    rows = db.scalars(select(Application).where(Application.applicant_id == user.id)
                      .order_by(Application.created_at.desc()))
    return [applicant_view(a) for a in rows]


@router.post("/applications/{app_id}/offer")
def respond(app_id: int, body: OfferResponseIn, db: DB, user: Applicant, a: VisibleApplication):
    return applicant_view(L.respond_to_offer(db, a, user, body.accept))


@router.post("/applications/{app_id}/withdraw")
def withdraw(app_id: int, db: DB, user: Applicant, a: VisibleApplication):
    if a.status not in ("submitted", "review", "docs_requested", "error"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This application can no longer be withdrawn")
    a.status = "withdrawn"
    L.audit(db, a.id, user, "withdrawn")
    db.commit()
    return applicant_view(a)


@router.post("/applications/{app_id}/statement")
async def upload_new_statement(app_id: int, db: DB, user: Applicant, a: VisibleApplication,
                               statement: UploadFile = File(...)):
    if a.status != "docs_requested":
        raise HTTPException(status.HTTP_409_CONFLICT, "No documents were requested")
    data = await statement.read(int(3.5 * 1_048_576))
    L.check_upload(statement.filename or "", data)
    path, digest = L.save_statement(a.id, statement.filename, data)
    db.add(Document(application_id=a.id, filename=statement.filename[:200], size=len(data), path=path,
                    sha256=digest,
                    mime="application/pdf" if statement.filename.lower().endswith(".pdf") else "text/csv"))
    L.audit(db, a.id, user, "statement_reuploaded")
    db.commit()
    L.queue_assessment(a.id)
    return applicant_view(a)


@router.get("/applications/{app_id}/letter.pdf")
def letter_pdf(app_id: int, user: CurrentUser, a: VisibleApplication):
    if not a.letter_md:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No letter yet")
    title = "Loan offer" if a.decision != "declined" else "Loan application decision"
    pdf = render_pdf(RenderInput(title=title, markdown=f"# {title}\n\n{a.letter_md}",
                                 meta={"generated": a.decided_at.strftime("%d %b %Y") if a.decided_at else ""}))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{a.reference}-letter.pdf"'})


@router.get("/samples")
def samples(user: CurrentUser):
    """Fictional applicants with statements, so anyone can try the full flow."""
    import json
    out = []
    for p in sorted(SAMPLES.glob("*.json")):
        out.append({"key": p.stem, **json.loads(p.read_text(encoding="utf-8"))["form"]})
    return out


@router.get("/samples/{key}/{fmt}")
def sample_file(key: str, fmt: str, user: CurrentUser):
    if fmt not in ("csv", "pdf") or not key.replace("_", "").isalnum():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    path = SAMPLES / f"{key}_statement.{fmt}"
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(path, filename=path.name, media_type="application/pdf" if fmt == "pdf" else "text/csv")


# ---------------------------------------------------------------- staff
@router.get("/applications")
def queue(db: DB, staff: Staff, status_filter: str | None = Query(None, alias="status"), q: str = "",
          limit: int = Query(200, le=500)):
    stmt = select(Application).order_by(Application.created_at.desc()).limit(limit)
    if status_filter == "open":
        stmt = stmt.where(Application.status.in_(OPEN_FOR_REVIEW + ("assessing", "submitted", "error")))
    elif status_filter:
        stmt = stmt.where(Application.status == status_filter)
    rows = [staff_row(a) for a in db.scalars(stmt)]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r["reference"] + r["name"] + r["city"] + r["purpose"]).lower()]
    return rows


@router.get("/applications/{app_id}")
def case(app_id: int, db: DB, user: CurrentUser, a: VisibleApplication):
    if user.role == "applicant":
        return applicant_view(a)
    asm = L.latest_assessment(db, a.id)
    events = db.scalars(select(AuditEvent).where(AuditEvent.application_id == a.id).order_by(AuditEvent.id)).all()
    actor_ids = {e.actor_id for e in events if e.actor_id}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(actor_ids)))}
    return {**staff_row(a), "date_of_birth": a.date_of_birth, "age": U.age_on(a.date_of_birth), "employer": a.employer,
            "years_employed": a.years_employed, "declared_income": a.declared_income, "offer": a.offer,
            "letter_md": a.letter_md, "decision_note": a.decision_note, "decided_by_id": a.decided_by_id,
            "four_eyes_amount": L.get_settings().four_eyes_amount,
            "assessment": None if not asm else {
                "id": asm.id, "status": asm.status, "error": asm.error, "policy_version": asm.policy_version,
                "model_version": asm.model_version, "statement": asm.statement, "bureau": asm.bureau,
                "affordability": asm.affordability, "risk": asm.risk, "policy": asm.policy,
                "recommendation": asm.recommendation, "memo_md": asm.memo_md, "trace": asm.trace,
                "llm_calls": asm.llm_calls, "tokens": asm.tokens, "created_at": asm.created_at.isoformat(),
                "finished_at": asm.finished_at.isoformat() if asm.finished_at else None},
            "audit": [{"action": e.action, "role": e.actor_role, "actor": names.get(e.actor_id), "details": e.details,
                       "at": e.created_at.isoformat()} for e in events]}


@router.post("/applications/{app_id}/what-if")
def simulate(app_id: int, body: WhatIfIn, db: DB, staff: Staff, a: VisibleApplication):
    return L.what_if(db, a, body.amount, body.tenure_months)


@router.post("/applications/{app_id}/decision")
def decide(app_id: int, body: DecisionIn, db: DB, staff: Staff, a: VisibleApplication):
    L.decide(db, a, staff, body.action, body.note, body.amount, body.tenure_months)
    return staff_row(a)


@router.post("/applications/{app_id}/second-approval")
def second(app_id: int, body: SecondApprovalIn, db: DB, admin: Admin, a: VisibleApplication):
    L.second_approval(db, a, admin, body.approve, body.note)
    return staff_row(a)


@router.post("/applications/{app_id}/reassess", status_code=status.HTTP_202_ACCEPTED)
def reassess(app_id: int, db: DB, staff: Staff, a: VisibleApplication):
    if a.status not in ("review", "docs_requested", "error"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot re-assess an application that is {a.status}")
    L.audit(db, a.id, staff, "reassessment_requested")
    db.commit()
    L.queue_assessment(a.id)
    return {"status": "assessing"}


@router.post("/applications/{app_id}/ask")
@limiter.limit("20/minute")
async def ask(request: Request, app_id: int, body: QuestionIn, db: DB, staff: Staff, a: VisibleApplication):
    asm = L.latest_assessment(db, a.id)
    if not asm or asm.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "No completed assessment yet")
    try:
        answer = await asyncio.to_thread(L.ask_case, asm, body.question)
    except LLMUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI unavailable: {exc.reason}")
    return {"answer": answer}

"""Risk admin: dashboard (volumes, approval rates, AI-human agreement, fairness lens), policy versions,
staff users and the audit log."""
import logging
from collections import Counter, defaultdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ...agent.underwriting import DEFAULT_RULES
from ...core.security import hash_password
from ...db.models import Application, Assessment, AuditEvent, PolicyVersion, User
from ...llm.gateway import llm_status
from ...ml.model import metrics as model_metrics
from ...schemas import PolicyIn, StaffUserIn, UserOut, UserUpdateIn
from ...services import lending as L
from ...services import sessions
from ..deps import DB, Admin, Staff

router = APIRouter(prefix="/admin", tags=["admin"])
audit = logging.getLogger("audit")

BOUNDS = {"min_cibil": (300, 900), "max_foir": (0.1, 0.9), "max_dpd_12m": (0, 180), "min_age": (18, 30),
          "max_age_at_maturity": (50, 80), "min_income": (5_000, 500_000), "max_bounces_6m": (0, 20),
          "max_loan_to_income": (0.2, 10), "max_utilisation": (0.1, 1.5), "max_enquiries_6m": (0, 30),
          "max_spend_to_income": (0.3, 2.0), "min_years_employed": (0, 10)}


@router.get("/dashboard")
def dashboard(db: DB, staff: Staff):
    apps = db.scalars(select(Application)).all()
    decided = [a for a in apps if a.decision]
    by_status = Counter(a.status for a in apps)
    agree = [a for a in decided if a.recommendation in ("approve", "counter", "decline")]
    agreement = sum(1 for a in agree if not a.override) / len(agree) if agree else None
    tat = [(a.decided_at - a.created_at).total_seconds() / 60 for a in decided if a.decided_at]

    def rate(group: list) -> dict:
        d = [a for a in group if a.decision]
        ok = sum(1 for a in d if a.decision in ("approved", "countered"))
        return {"applications": len(group), "decided": len(d), "approval_rate": round(ok / len(d), 3) if d else None}
    by_emp, by_city = defaultdict(list), defaultdict(list)
    for a in apps:
        by_emp[a.employment_type].append(a)
        by_city[a.city].append(a)
    assessments = db.scalars(select(Assessment).where(Assessment.status == "done")).all()
    return {
        "totals": {"applications": len(apps), "decided": len(decided), **rate(apps),
                   "ai_human_agreement": round(agreement, 3) if agreement is not None else None,
                   "overrides": sum(1 for a in decided if a.override),
                   "median_minutes_to_decision": round(sorted(tat)[len(tat) // 2], 1) if tat else None,
                   "avg_llm_calls_per_assessment": round(sum(x.llm_calls for x in assessments) / len(assessments), 1)
                   if assessments else None},
        "by_status": dict(by_status),
        "recommendations": dict(Counter(a.recommendation for a in apps if a.recommendation)),
        "bands": dict(Counter(a.risk_band for a in apps if a.risk_band)),
        "fairness": {"by_employment": {k: rate(v) for k, v in by_emp.items()},
                     "by_city": {k: rate(v) for k, v in sorted(by_city.items(), key=lambda kv: -len(kv[1]))[:8]}},
        "overrides": [{"reference": a.reference, "ai": a.recommendation, "human": a.decision, "note": a.decision_note}
                      for a in decided if a.override][-10:],
        "model": model_metrics(), "llm": llm_status(),
    }


@router.get("/policy")
def policies(db: DB, staff: Staff):
    L.active_policy(db)
    rows = db.scalars(select(PolicyVersion).order_by(PolicyVersion.version.desc())).all()
    return [{"version": p.version, "rules": p.rules, "note": p.note, "is_active": p.is_active,
             "created_at": p.created_at.isoformat()} for p in rows]


@router.post("/policy", status_code=status.HTTP_201_CREATED)
def new_policy(body: PolicyIn, db: DB, admin: Admin):
    rules = body.rules
    errors = {}
    for group in ("hard", "soft"):
        for key, base in DEFAULT_RULES[group].items():
            value = rules.get(group, {}).get(key, {}).get("value")
            if isinstance(base["value"], bool):
                if not isinstance(value, bool):
                    errors[key] = "must be true or false"
            elif not isinstance(value, (int, float)) or isinstance(value, bool):
                errors[key] = "must be a number"
            elif key in BOUNDS and not BOUNDS[key][0] <= value <= BOUNDS[key][1]:
                errors[key] = f"must be between {BOUNDS[key][0]} and {BOUNDS[key][1]}"
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"fields": errors})
    clean = {"hard": {k: {**v, "value": rules["hard"][k]["value"]} for k, v in DEFAULT_RULES["hard"].items()},
             "soft": {k: {**v, "value": rules["soft"][k]["value"]} for k, v in DEFAULT_RULES["soft"].items()},
             "decision": DEFAULT_RULES["decision"]}
    current = L.active_policy(db)
    current.is_active = False
    p = PolicyVersion(version=current.version + 1, rules=clean, note=body.note.strip(), created_by_id=admin.id,
                      is_active=True)
    db.add(p)
    L.audit(db, None, admin, "policy_changed", {"version": p.version, "note": p.note})
    db.commit()
    return {"version": p.version}


@router.get("/audit")
def audit_log(db: DB, admin: Admin, limit: int = 200):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 1000))).all()
    refs = {a.id: a.reference for a in db.scalars(select(Application).where(
        Application.id.in_({r.application_id for r in rows if r.application_id})))}
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(actor_ids)))}
    return [{"id": r.id, "application_id": r.application_id, "reference": refs.get(r.application_id),
             "actor": names.get(r.actor_id) or r.actor_role,
             "role": r.actor_role, "action": r.action, "details": r.details, "at": r.created_at.isoformat()}
            for r in rows]


@router.get("/users", response_model=list[UserOut])
def users(db: DB, admin: Admin):
    return db.scalars(select(User).order_by(User.role, User.created_at)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_staff(body: StaffUserIn, db: DB, admin: Admin):
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    u = User(email=email, name=body.name.strip(), role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    L.audit(db, None, admin, "staff_created", {"email": email, "role": body.role})
    db.commit()
    return u


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdateIn, db: DB, admin: Admin):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't disable yourself")
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            sessions.revoke_user(db, u.id)
    L.audit(db, None, admin, "user_updated", {"user_id": u.id, "is_active": u.is_active})
    db.commit()
    return u

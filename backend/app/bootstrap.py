"""Startup tasks and demo data.

    python -m app.bootstrap --seed      (from backend/)
"""
import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent import underwriting as U
from .core.config import BACKEND_DIR, DEMO_ADMIN_PASSWORD, get_settings
from .core.security import hash_password, verify_password
from .db.models import Application, BureauRecord, User
from .db.session import Base, SessionLocal, engine

log = logging.getLogger(__name__)
SAMPLES = Path(__file__).resolve().parent / "samples"
DEMO = [("Demo Applicant", "applicant@lendwise.dev", "applicant", "Applicant2026"),
        ("Uma Underwriter", "underwriter@lendwise.dev", "underwriter", "Underwrite2026"),
        ("Rhea Risk", "risk@lendwise.dev", "admin", "RiskAdmin2026")]


def init_db() -> None:
    s = get_settings()
    if s.environment == "test":
        Base.metadata.create_all(engine)
        return
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["app_logging"] = True
    if s.environment == "development":
        command.upgrade(cfg, "head")
        return
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current != head:
        raise RuntimeError(f"Database schema is at {current}, code expects {head}: run `alembic upgrade head`")


def ensure_admin(db: Session) -> None:
    s = get_settings()
    if not db.scalar(select(User.id).where(User.role == "admin")):
        db.add(User(email=s.bootstrap_admin_email.lower(), name="Admin", role="admin",
                    password_hash=hash_password(s.bootstrap_admin_password)))
        db.commit()
        log.warning("Created bootstrap admin %s - change the password!", s.bootstrap_admin_email)


def warn_insecure_defaults(db: Session) -> None:
    for admin in db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True))):
        if verify_password(DEMO_ADMIN_PASSWORD, admin.password_hash):
            log.warning("SECURITY: admin %s still uses the public demo password", admin.email)


def seed_bureau(db: Session) -> None:
    """The mock bureau knows the sample applicants (others get a deterministic synthetic profile)."""
    for p in SAMPLES.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        h = U.pan_hash(data["form"]["pan"])
        if not db.scalar(select(BureauRecord.id).where(BureauRecord.pan_hash == h)):
            b = data["bureau"]
            db.add(BureauRecord(pan_hash=h, score=b["score"], report={k: v for k, v in b.items() if k != "score"}))
    db.commit()


def seed_demo(db: Session, with_applications: bool = True) -> None:
    from .services import lending as L

    for name, email, role, pw in DEMO:
        if not db.scalar(select(User.id).where(User.email == email)):
            db.add(User(email=email, name=name, role=role, password_hash=hash_password(pw)))
    db.commit()
    seed_bureau(db)
    L.active_policy(db)
    if not with_applications or db.scalar(select(Application.id)):
        return
    # Four sample applicants already in the queue (assessed by the rules engine: instant, no LLM tokens).
    for key in ("sneha_borderline", "arun_risky", "priya_large", "karthik_self_employed"):
        data = json.loads((SAMPLES / f"{key}.json").read_text(encoding="utf-8"))["form"]
        email = data["name"].split()[0].lower() + "@example.com"
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, name=data["name"], role="applicant", password_hash=hash_password("Customer2026"))
            db.add(user)
            db.commit()
        form = {"full_name": data["name"], "pan": data["pan"], "date_of_birth": data["dob"], "city": data["city"],
                "employment_type": data["employment_type"], "employer": data["employer"],
                "years_employed": data["years_employed"], "declared_income": data["declared_income"],
                "loan_amount": data["loan_amount"], "tenure_months": data["tenure_months"], "purpose": data["purpose"],
                "consent": "true"}
        name = f"{key}_statement.pdf"
        a = L.create_application(db, user, form, name, (SAMPLES / name).read_bytes())
        L.run_assessment(a.id, use_llm=False)
    print("Demo logins:")
    for name, email, role, pw in DEMO:
        print(f"  {role:<12} {email} / {pw}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as session:
        ensure_admin(session)
        if args.seed:
            seed_demo(session)

"""Database tables.

    users ─┬─ refresh_tokens
           └─ applications ─┬─ audit_events      append-only trail: who did what, when (never updated)
                            ├─ assessments       one per AI run: tool outputs, model score, policy result,
                            │                    recommendation, memo (re-assessment keeps the history)
                            └─ documents         uploaded bank statements
    policy_versions          versioned rule sets; every assessment records the version it used
    bureau_records           mock credit bureau (CIBIL-like), keyed by a hash of the PAN
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base
from .types import UTCDateTime

ROLES = ("applicant", "underwriter", "admin")
STAFF = ("underwriter", "admin")
# submitted -> assessing -> review (AI done, waiting for a human) -> approved / offered / declined / docs_requested
# offered -> accepted / rejected_by_applicant ; pending_second_approval for large loans (four eyes)
STATUSES = ("submitted", "assessing", "review", "pending_second_approval", "approved", "offered", "accepted",
            "declined", "declined_by_applicant", "docs_requested", "withdrawn", "error")
OPEN_FOR_REVIEW = ("review", "pending_second_approval", "docs_requested")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(12), default="applicant")
    password_hash: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(12), unique=True, index=True)       # LW-7F3K2Q
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="submitted", index=True)

    # applicant details (PII: shown masked to staff lists, never sent to the LLM)
    full_name: Mapped[str] = mapped_column(String(100))
    pan_hash: Mapped[str] = mapped_column(String(64), index=True)
    pan_masked: Mapped[str] = mapped_column(String(10))                                  # ABxxxxx12F
    date_of_birth: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(60))
    employment_type: Mapped[str] = mapped_column(String(16))                             # salaried | self_employed
    employer: Mapped[str] = mapped_column(String(120), default="")
    years_employed: Mapped[float] = mapped_column(Float, default=0)
    declared_income: Mapped[float] = mapped_column(Float)                                # monthly, INR
    loan_amount: Mapped[float] = mapped_column(Float)
    tenure_months: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(40))
    consent_at: Mapped[datetime] = mapped_column(UTCDateTime)

    # latest AI recommendation (denormalised for the queue) and the final human decision
    recommendation: Mapped[str | None] = mapped_column(String(16), nullable=True)      # approve|counter|refer|decline
    risk_band: Mapped[str | None] = mapped_column(String(2), nullable=True)
    pd: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)            # approved|offered|declined
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    second_approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    override: Mapped[bool] = mapped_column(Boolean, default=False)                    # human disagreed with AI
    offer: Mapped[dict | None] = mapped_column(JSON, nullable=True)                    # amount, tenure, rate, emi
    letter_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    applicant: Mapped[User] = relationship(foreign_keys=[applicant_id], lazy="joined")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="bank_statement")
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="running")                 # running | done | failed
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    statement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bureau: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    affordability: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk: Mapped[dict | None] = mapped_column(JSON, nullable=True)                     # pd, band, shap factors
    policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)                   # rule results
    recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)           # decision, reasons, offer
    memo_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace: Mapped[list] = mapped_column(JSON, default=list)                             # agent steps
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AuditEvent(Base):
    """Append-only. Rows are never updated or deleted by the application."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"),
                                                       nullable=True, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(12))        # applicant | underwriter | admin | system | ai
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    rules: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BureauRecord(Base):
    """Mock credit bureau (stands in for a CIBIL / Experian API call)."""
    __tablename__ = "bureau_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    pan_hash: Mapped[str] = mapped_column(String(64), unique=True)
    score: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict] = mapped_column(JSON)          # cards, loans, dpd history, enquiries, write-offs

"""Isolated database + scripted offline LLM: tests need no key and no network."""
import os
import tempfile
import time
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="lendwise-tests-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "DATA_DIR": str(_tmp / "data"),
    "LLM_MODE": "fake",
    "GROQ_API_KEY": "",
    "BCRYPT_ROUNDS": "4",
    "LOG_LEVEL": "WARNING",
    "APPLICATIONS_PER_USER_PER_DAY": "100",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import limiter  # noqa: E402
from app.main import app  # noqa: E402

limiter.enabled = False
SAMPLES = Path(__file__).resolve().parents[1] / "app" / "samples"
API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        from app.bootstrap import seed_demo
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            seed_demo(db, with_applications=False)
        yield c


def login(client, email, password):
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


_n = [0]


@pytest.fixture
def applicant(client):
    _n[0] += 1
    r = client.post(f"{API}/auth/register", json={"email": f"a{_n[0]}_{int(time.time()*1000) % 99999}@example.com",
                                                  "name": "Test Applicant", "password": "Password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def underwriter(client):
    return login(client, "underwriter@lendwise.dev", "Underwrite2026")


@pytest.fixture
def risk_admin(client):
    return login(client, "risk@lendwise.dev", "RiskAdmin2026")


def sample_form(key: str) -> dict:
    import json
    f = json.loads((SAMPLES / f"{key}.json").read_text(encoding="utf-8"))["form"]
    return {"full_name": f["name"], "pan": f["pan"], "date_of_birth": f["dob"], "city": f["city"],
            "employment_type": f["employment_type"], "employer": f["employer"], "years_employed": f["years_employed"],
            "declared_income": f["declared_income"], "loan_amount": f["loan_amount"],
            "tenure_months": f["tenure_months"], "purpose": f["purpose"], "consent": "true"}


def apply(client, headers, key="ravi_strong", fmt="pdf", **overrides):
    form = {**sample_form(key), **overrides}
    path = SAMPLES / f"{key}_statement.{fmt}"
    r = client.post(f"{API}/applications", data={k: str(v) for k, v in form.items()},
                    files={"statement": (path.name, path.read_bytes())}, headers=headers)
    return r


def wait_status(client, headers, app_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = client.get(f"{API}/applications/{app_id}", headers=headers).json()
        if a["status"] in statuses:
            return a
        time.sleep(0.1)
    raise AssertionError(f"application {app_id} stuck in {a['status']}")

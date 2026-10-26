"""End-to-end through the API: apply -> AI assessment -> human decision -> letter -> applicant accepts."""
from conftest import API, apply, wait_status


def test_apply_assess_approve_accept(client, applicant, underwriter):
    r = apply(client, applicant, "ravi_strong")
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["reference"].startswith("LW-") and "pd" not in app            # applicants never see scores
    case = wait_status(client, underwriter, app["id"], {"review"})
    asm = case["assessment"]
    assert case["recommendation"] == "approve" and case["risk_band"] == "A"
    assert asm["memo_md"] and asm["policy_version"] == 1 and asm["model_version"]
    tools = [s.get("tool") for s in asm["trace"] if s.get("event") == "tool"]
    assert {"analyse_bank_statement", "pull_credit_report", "check_affordability", "score_credit_risk",
            "run_policy_checks"} <= set(tools)
    assert case["pan"].count("x") == 5                                         # PAN is masked for staff too

    r = client.post(f"{API}/applications/{app['id']}/decision", json={"action": "approve"}, headers=underwriter)
    assert r.status_code == 200, r.text
    mine = client.get(f"{API}/applications/{app['id']}", headers=applicant).json()
    assert mine["status"] == "offered" and mine["offer"]["emi"] > 0 and mine["letter_md"]
    pdf = client.get(f"{API}/applications/{app['id']}/letter.pdf", headers=applicant)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    r = client.post(f"{API}/applications/{app['id']}/offer", json={"accept": True}, headers=applicant)
    assert r.json()["status"] == "accepted"
    actions = [e["action"] for e in client.get(f"{API}/applications/{app['id']}", headers=underwriter).json()["audit"]]
    assert actions[:2] == ["submitted", "ai_assessed"]
    assert "decision_approved" in actions and "offer_accepted" in actions


def test_hard_rule_cannot_be_overridden(client, applicant, underwriter):
    app = apply(client, applicant, "arun_risky").json()
    case = wait_status(client, underwriter, app["id"], {"review"})
    assert case["recommendation"] == "decline"
    r = client.post(f"{API}/applications/{app['id']}/decision",
                    json={"action": "approve", "note": "I like this customer a lot"}, headers=underwriter)
    assert r.status_code == 409 and "cannot be approved" in r.json()["detail"]
    r = client.post(f"{API}/applications/{app['id']}/decision", json={"action": "decline"}, headers=underwriter)
    assert r.status_code == 422                                               # a decline needs a reason
    r = client.post(f"{API}/applications/{app['id']}/decision",
                    json={"action": "decline", "note": "Multiple hard policy failures"}, headers=underwriter)
    assert r.status_code == 200
    letter = client.get(f"{API}/applications/{app['id']}", headers=applicant).json()["letter_md"]
    assert "pd" not in letter.lower().split() and "band" not in letter.lower()


def test_counter_offer_and_what_if(client, applicant, underwriter):
    app = apply(client, applicant, "karthik_self_employed", fmt="csv").json()
    case = wait_status(client, underwriter, app["id"], {"review"})
    assert case["recommendation"] == "counter"
    w = client.post(f"{API}/applications/{app['id']}/what-if", json={"amount": 600000, "tenure_months": 36},
                    headers=underwriter).json()
    assert w["decision"] in ("approve", "refer") and w["foir"] < case["assessment"]["affordability"]["foir"]
    r = client.post(f"{API}/applications/{app['id']}/decision",
                    json={"action": "counter", "amount": 600000, "tenure_months": 36,
                          "note": "Lower amount keeps FOIR comfortable"}, headers=underwriter)
    assert r.status_code == 200, r.text
    mine = client.get(f"{API}/applications/{app['id']}", headers=applicant).json()
    assert mine["status"] == "offered" and mine["offer"]["amount"] == 600000


def test_four_eyes_for_large_loans(client, applicant, underwriter, risk_admin):
    app = apply(client, applicant, "priya_large").json()
    wait_status(client, underwriter, app["id"], {"review"})
    r = client.post(f"{API}/applications/{app['id']}/decision", json={"action": "approve"}, headers=underwriter)
    assert r.json()["status"] == "pending_second_approval"
    assert client.get(f"{API}/applications/{app['id']}", headers=applicant).json()["offer"] is None
    r = client.post(f"{API}/applications/{app['id']}/second-approval", json={"approve": True}, headers=underwriter)
    assert r.status_code == 403                                               # underwriters can't second-approve
    r = client.post(f"{API}/applications/{app['id']}/second-approval", json={"approve": True}, headers=risk_admin)
    assert r.json()["status"] == "offered"


def test_override_needs_reason_and_is_tracked(client, applicant, underwriter, risk_admin):
    app = apply(client, applicant, "ravi_strong").json()
    wait_status(client, underwriter, app["id"], {"review"})
    r = client.post(f"{API}/applications/{app['id']}/decision", json={"action": "decline", "note": "short"},
                    headers=underwriter)
    assert r.status_code == 422
    r = client.post(f"{API}/applications/{app['id']}/decision",
                    json={"action": "decline", "note": "Employer could not be verified by phone"}, headers=underwriter)
    assert r.json()["override"] is True
    dash = client.get(f"{API}/admin/dashboard", headers=risk_admin).json()
    assert dash["totals"]["overrides"] >= 1 and any(o["reference"] == app["reference"] for o in dash["overrides"])


def test_access_control(client, applicant, underwriter):
    from conftest import _n
    app = apply(client, applicant, "ravi_strong").json()
    _n[0] += 1
    other = client.post(f"{API}/auth/register", json={"email": f"other{_n[0]}@example.com", "name": "Other",
                                                      "password": "Password123"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"{API}/applications/{app['id']}", headers=oh).status_code == 404
    assert client.get(f"{API}/applications/{app['id']}/letter.pdf", headers=oh).status_code == 404
    assert client.get(f"{API}/applications", headers=applicant).status_code == 403
    assert client.post(f"{API}/applications/{app['id']}/decision", json={"action": "approve"},
                       headers=applicant).status_code == 403
    assert client.get(f"{API}/admin/audit", headers=underwriter).status_code == 403
    assert all(a["id"] != app["id"] for a in client.get(f"{API}/me/applications", headers=oh).json())


def test_validation_and_uploads(client, applicant):
    r = apply(client, applicant, "ravi_strong", pan="12345")
    assert r.status_code == 422 and "pan" in r.json()["detail"]["fields"]
    r = apply(client, applicant, "ravi_strong", consent="false")
    assert "consent" in r.json()["detail"]["fields"]
    form = {"full_name": "X", "pan": "ABCDE1234F", "date_of_birth": "1990-01-01", "city": "Pune",
            "employment_type": "salaried", "years_employed": "3", "declared_income": "50000", "loan_amount": "100000",
            "tenure_months": "24", "purpose": "travel", "consent": "true"}
    r = client.post(f"{API}/applications", data=form, files={"statement": ("s.pdf", b"not a pdf")}, headers=applicant)
    assert r.status_code == 422
    r = client.post(f"{API}/applications", data=form, files={"statement": ("s.exe", b"MZ")}, headers=applicant)
    assert r.status_code == 422


def test_policy_change_is_versioned_and_validated(client, risk_admin, underwriter):
    pols = client.get(f"{API}/admin/policy", headers=risk_admin).json()
    rules = pols[0]["rules"]
    bad = {**rules, "hard": {**rules["hard"], "min_cibil": {**rules["hard"]["min_cibil"], "value": 9999}}}
    r = client.post(f"{API}/admin/policy", json={"rules": bad, "note": "typo"}, headers=risk_admin)
    assert r.status_code == 422
    good = {**rules, "hard": {**rules["hard"], "min_cibil": {**rules["hard"]["min_cibil"], "value": 660}}}
    r = client.post(f"{API}/admin/policy", json={"rules": good, "note": "Tighten minimum score"}, headers=risk_admin)
    assert r.status_code == 201
    assert client.post(f"{API}/admin/policy", json={"rules": good, "note": "x" * 5},
                       headers=underwriter).status_code == 403
    active = [p for p in client.get(f"{API}/admin/policy", headers=risk_admin).json() if p["is_active"]]
    assert len(active) == 1 and active[0]["rules"]["hard"]["min_cibil"]["value"] == 660

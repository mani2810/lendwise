"""Generate a synthetic Indian personal-loan dataset and train the credit-risk model.

    python -m app.ml.train            (from backend/) -> app/ml/artifacts/{model.json, metrics.json, MODEL_CARD.md}

Why synthetic: real loan-performance data is personal and licensed. The generator below encodes widely
documented credit-risk relationships (lower bureau score, higher FOIR, recent delinquency, bounced EMIs
and credit hunger raise default risk) with noise and interactions, so the model has something real to
learn, while the README is explicit that this is a demonstration model, not a production scorecard.

Two candidates are compared on a held-out set (ROC-AUC, PR-AUC, KS, Brier); the better one is kept.
Both are saved as plain JSON (coefficients / XGBoost's native format): loading never executes code,
unlike pickle files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.config import get_settings
from .features import BANDS, LABELS, NAMES, band_for

SEED = 7


def generate(n: int = 40_000, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    salaried = (rng.random(n) < 0.72).astype(int)
    age = np.clip(rng.normal(34, 8, n), 21, 62).round()
    years_employed = np.clip(rng.gamma(2.0, 2.2, n) * np.where(salaried, 1, 1.3), 0, age - 20).round(1)
    income = np.exp(rng.normal(np.log(55_000), 0.55, n)) * (1 + 0.012 * (age - 30)).clip(0.8, 1.4)
    income = income.clip(15_000, 600_000).round(-2)
    # credit behaviour: a latent "discipline" drives score, delinquency, utilisation, bounces together
    discipline = rng.normal(0, 1, n)
    cibil = np.clip(735 + 55 * discipline + rng.normal(0, 35, n), 300, 900).round()
    util = np.clip(0.38 - 0.16 * discipline + rng.normal(0, 0.18, n), 0, 1.4).round(3)
    dpd = np.select([discipline < -1.9, discipline < -1.3, discipline < -0.8], [90, 60, 30], 0)
    dpd = np.where(rng.random(n) < 0.04, 30, dpd)
    enquiries = rng.poisson(np.clip(1.4 - 0.5 * discipline, 0.2, 6))
    bounces = rng.poisson(np.clip(0.35 - 0.45 * discipline, 0.02, 5))
    spend_to_income = np.clip(rng.normal(0.72, 0.15, n) - 0.04 * discipline, 0.2, 1.6).round(3)
    loan_to_income = np.clip(rng.gamma(2.2, 0.28, n), 0.05, 3.5).round(3)
    existing_emi_share = np.clip(rng.gamma(1.6, 0.11, n) - 0.05 * discipline, 0, 0.8)
    new_emi_share = loan_to_income * 12 / 36 * 1.18 / 12 * 12 / 12            # ~ 3-year EMI / income
    foir = np.clip(existing_emi_share + new_emi_share, 0.02, 1.4).round(3)

    logit = (-3.05 - 0.0115 * (cibil - 735) + 3.1 * (foir - 0.42) + 1.1 * (util - 0.38) + 0.022 * dpd
             + 0.16 * enquiries + 0.38 * bounces + 1.6 * np.clip(spend_to_income - 0.85, 0, None)
             - 0.09 * np.minimum(years_employed, 10) - 0.45 * np.log(income / 55_000) + 0.35 * (1 - salaried)
             - 0.012 * (age - 34) + 0.25 * np.clip(loan_to_income - 1.2, 0, None)
             + 0.9 * ((foir > 0.6) & (cibil < 680))                              # interaction: stretched + weak
             + rng.normal(0, 0.45, n))
    default = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return pd.DataFrame({"cibil_score": cibil, "monthly_income": income, "foir": foir, "card_utilisation": util,
                         "dpd_max_12m": dpd, "enquiries_6m": enquiries, "bounces_6m": bounces,
                         "spend_to_income": spend_to_income, "years_employed": years_employed, "age": age,
                         "salaried": salaried, "loan_to_income": loan_to_income, "default": default})


def ks_stat(y, p) -> float:
    order = np.argsort(p)
    y = np.asarray(y)[order]
    cum_bad = np.cumsum(y) / max(1, y.sum())
    cum_good = np.cumsum(1 - y) / max(1, (1 - y).sum())
    return float(np.max(np.abs(cum_bad - cum_good)))


def evaluate(y, p) -> dict:
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    return {"roc_auc": round(float(roc_auc_score(y, p)), 4), "pr_auc": round(float(average_precision_score(y, p)), 4),
            "ks": round(ks_stat(y, p), 4), "brier": round(float(brier_score_loss(y, p)), 4)}


def train(out_dir: Path | None = None, n: int = 40_000) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBClassifier

    out = Path(out_dir or get_settings().model_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = generate(n)
    X, y = df[NAMES], df["default"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=SEED, stratify=y)

    logreg = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight=None))
    logreg.fit(X_tr, y_tr)
    m_lr = evaluate(y_te, logreg.predict_proba(X_te)[:, 1])

    xgb = XGBClassifier(n_estimators=350, max_depth=4, learning_rate=0.05, subsample=0.85, colsample_bytree=0.85,
                        min_child_weight=5, reg_lambda=2.0, objective="binary:logistic", eval_metric="auc",
                        random_state=SEED, n_jobs=4)
    xgb.fit(X_tr, y_tr)
    p_xgb = xgb.predict_proba(X_te)[:, 1]
    m_xgb = evaluate(y_te, p_xgb)

    # Selection rule: the simpler, fully transparent model wins unless the complex one is clearly better.
    p_lr = logreg.predict_proba(X_te)[:, 1]
    chosen = "xgboost" if m_xgb["roc_auc"] > m_lr["roc_auc"] + 0.005 else "logistic_regression"
    p_best = p_xgb if chosen == "xgboost" else p_lr

    # band table on the test set: observed default rate must rise from A to E, or pricing makes no sense
    bands = pd.DataFrame({"band": [band_for(p) for p in p_best], "y": y_te.values})
    band_table = {b: {"share": round(float((bands.band == b).mean()), 3),
                      "observed_default_rate": round(float(bands[bands.band == b].y.mean()), 3)
                      if (bands.band == b).any() else None} for _, b in BANDS}

    version = datetime.now(timezone.utc).strftime(("xgb" if chosen == "xgboost" else "lr") + "-%Y%m%d")
    if chosen == "xgboost":
        xgb.get_booster().save_model(str(out / "model.json"))
        importance = dict(zip(NAMES, xgb.feature_importances_.tolist()))
        params = {"type": "xgboost", "file": "model.json"}
    else:
        scaler, lr = logreg.named_steps["standardscaler"], logreg.named_steps["logisticregression"]
        params = {"type": "logistic", "intercept": float(lr.intercept_[0]),
                  "coef": dict(zip(NAMES, lr.coef_[0].tolist())), "mean": dict(zip(NAMES, scaler.mean_.tolist())),
                  "scale": dict(zip(NAMES, scaler.scale_.tolist()))}
        importance = {k: abs(v) for k, v in params["coef"].items()}
    total = sum(importance.values()) or 1
    importance = sorted(((k, v / total) for k, v in importance.items()), key=lambda kv: -kv[1])
    (out / "model_params.json").write_text(json.dumps({"version": version, **params}, indent=2), encoding="utf-8")
    metrics = {"version": version, "trained_at": datetime.now(timezone.utc).isoformat(), "rows": n,
               "default_rate": round(float(y.mean()), 4), "test_rows": int(len(y_te)),
               "candidates": {"logistic_regression": m_lr, "xgboost": m_xgb}, "chosen": chosen,
               "selection_rule": "simpler model wins unless XGBoost beats it by more than 0.005 ROC-AUC",
               "bands": band_table, "feature_importance": {k: round(v, 4) for k, v in importance},
               "features": NAMES}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (out / "MODEL_CARD.md").write_text(model_card(metrics), encoding="utf-8")
    return metrics


def model_card(m: dict) -> str:
    lr, xg = m["candidates"]["logistic_regression"], m["candidates"]["xgboost"]
    star_lr, star_xg = ("**", "") if m["chosen"] == "logistic_regression" else ("", "**")
    rows = "\n".join(f"| {b} | {v['share']:.1%} | "
                     f"{v['observed_default_rate'] if v['observed_default_rate'] is not None else '-'} |"
                     for b, v in m["bands"].items())
    feats = "\n".join(f"- `{k}` - {LABELS[k]} (importance {v})" for k, v in m["feature_importance"].items())
    return f"""# Model card: LendWise credit-risk model ({m['version']})

**Purpose:** estimate the probability that a personal-loan applicant defaults (90+ days past due within
12 months). Used as ONE input to a decision; hard policy rules and a human underwriter decide.

**Data:** {m['rows']:,} synthetic Indian applicants (default rate {m['default_rate']:.1%}), generated with documented
credit-risk relationships and noise (`app/ml/train.py`). **Not real customers - a demonstration model.**

**Excluded on purpose:** gender, religion, caste, marital status, name, PAN, address/pincode.

| Model | ROC-AUC | PR-AUC | KS | Brier |
|---|---|---|---|---|
| {star_lr}Logistic regression{star_lr} | {lr['roc_auc']} | {lr['pr_auc']} | {lr['ks']} | {lr['brier']} |
| {star_xg}XGBoost{star_xg} | {xg['roc_auc']} | {xg['pr_auc']} | {xg['ks']} | {xg['brier']} |

**Chosen: {m['chosen'].replace('_', ' ')}.** Rule: {m['selection_rule']} - a transparent scorecard is
easier to explain to applicants, auditors and regulators.

**Risk bands** (test set; observed default rate must rise from A to E):

| Band | Share | Observed default rate |
|---|---|---|
{rows}

**Features:**
{feats}

**Explanations:** per-applicant contributions (exact for the linear model: coefficient x standardised
distance from the average applicant; SHAP `pred_contribs` for XGBoost), turned into plain-language reason codes.

**Limitations:** synthetic data; no time dimension; must be re-trained and validated on real portfolio data,
with bias testing across protected groups, before any real use.
"""


if __name__ == "__main__":
    m = train()
    print(json.dumps({k: m[k] for k in ("version", "default_rate", "candidates", "bands")}, indent=2))

# Model card: LendWise credit-risk model (lr-20261026)

**Purpose:** estimate the probability that a personal-loan applicant defaults (90+ days past due within
12 months). Used as ONE input to a decision; hard policy rules and a human underwriter decide.

**Data:** 40,000 synthetic Indian applicants (default rate 9.7%), generated with documented
credit-risk relationships and noise (`app/ml/train.py`). **Not real customers - a demonstration model.**

**Excluded on purpose:** gender, religion, caste, marital status, name, PAN, address/pincode.

| Model | ROC-AUC | PR-AUC | KS | Brier |
|---|---|---|---|---|
| **Logistic regression** | 0.881 | 0.5592 | 0.61 | 0.06 |
| XGBoost | 0.8793 | 0.5528 | 0.6172 | 0.0603 |

**Chosen: logistic regression.** Rule: simpler model wins unless XGBoost beats it by more than 0.005 ROC-AUC - a transparent scorecard is
easier to explain to applicants, auditors and regulators.

**Risk bands** (test set; observed default rate must rise from A to E):

| Band | Share | Observed default rate |
|---|---|---|
| A | 57.8% | 0.017 |
| B | 15.4% | 0.054 |
| C | 9.8% | 0.108 |
| D | 5.8% | 0.196 |
| E | 11.2% | 0.509 |

**Features:**
- `cibil_score` - Credit score (importance 0.2318)
- `foir` - Total EMIs as a share of income (FOIR) (importance 0.1428)
- `dpd_max_12m` - Worst days-past-due in 12 months (importance 0.1414)
- `bounces_6m` - Bounced payments in 6 months (importance 0.0887)
- `card_utilisation` - Credit-card utilisation (importance 0.0856)
- `years_employed` - Years in current job/business (importance 0.0731)
- `monthly_income` - Verified monthly income (importance 0.0721)
- `enquiries_6m` - Credit enquiries in 6 months (importance 0.0638)
- `salaried` - Salaried employment (importance 0.0486)
- `age` - Age (importance 0.0371)
- `loan_to_income` - Loan amount / annual income (importance 0.0078)
- `spend_to_income` - Spending as a share of income (importance 0.0073)

**Explanations:** per-applicant contributions (exact for the linear model: coefficient x standardised
distance from the average applicant; SHAP `pred_contribs` for XGBoost), turned into plain-language reason codes.

**Limitations:** synthetic data; no time dimension; must be re-trained and validated on real portfolio data,
with bias testing across protected groups, before any real use.

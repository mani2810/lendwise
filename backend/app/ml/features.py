"""The risk model's features: one definition shared by data generation, training and scoring.

Every feature is computable from what the underwriting tools produce (bank statement, bureau report,
application form). No protected attributes (gender, religion, caste, marital status) are used, and
neither is anything that proxies for them directly.
"""
FEATURES = [
    # name,                  label for explanations,                        direction ("+" = raises risk)
    ("cibil_score", "Credit score", "-"),
    ("monthly_income", "Verified monthly income", "-"),
    ("foir", "Total EMIs as a share of income (FOIR)", "+"),
    ("card_utilisation", "Credit-card utilisation", "+"),
    ("dpd_max_12m", "Worst days-past-due in 12 months", "+"),
    ("enquiries_6m", "Credit enquiries in 6 months", "+"),
    ("bounces_6m", "Bounced payments in 6 months", "+"),
    ("spend_to_income", "Spending as a share of income", "+"),
    ("years_employed", "Years in current job/business", "-"),
    ("age", "Age", "-"),
    ("salaried", "Salaried employment", "-"),
    ("loan_to_income", "Loan amount / annual income", "+"),
]
NAMES = [f[0] for f in FEATURES]
LABELS = {f[0]: f[1] for f in FEATURES}

BANDS = [(0.04, "A"), (0.08, "B"), (0.15, "C"), (0.25, "D"), (1.01, "E")]
BAND_RATE = {"A": 10.99, "B": 12.49, "C": 14.99, "D": 17.99, "E": 21.99}     # annual interest, %


def band_for(pd: float) -> str:
    return next(b for limit, b in BANDS if pd < limit)


def vector(f: dict) -> list[float]:
    return [float(f[n]) for n in NAMES]

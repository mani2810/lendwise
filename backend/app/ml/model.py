"""Load the trained risk model (plain JSON, no pickle) and score an applicant with explanations."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from ..core.config import get_settings
from .features import LABELS, NAMES, band_for, vector


class RiskModel:
    def __init__(self, params: dict, folder: Path):
        self.params = params
        self.version = params["version"]
        self.type = params["type"]
        self._booster = None
        if self.type == "xgboost":
            import xgboost as xgb
            self._booster = xgb.Booster()
            self._booster.load_model(str(folder / params["file"]))

    def score(self, features: dict) -> dict:
        """-> {pd, band, factors: [{feature, label, value, impact}], version}; impact > 0 raises risk."""
        missing = [n for n in NAMES if n not in features]
        if missing:
            raise ValueError(f"missing features: {missing}")
        if self.type == "logistic":
            p = self.params
            contrib = {n: p["coef"][n] * (float(features[n]) - p["mean"][n]) / p["scale"][n] for n in NAMES}
            logit = p["intercept"] + sum(contrib.values())
        else:
            import numpy as np
            import xgboost as xgb
            dm = xgb.DMatrix(np.array([vector(features)]), feature_names=NAMES)
            raw = self._booster.predict(dm, pred_contribs=True)[0]
            contrib = dict(zip(NAMES, raw[:-1].tolist()))
            logit = float(raw.sum())
        pd = 1 / (1 + math.exp(-logit))
        factors = sorted(({"feature": n, "label": LABELS[n], "value": features[n], "impact": round(v, 4)}
                          for n, v in contrib.items()), key=lambda f: -abs(f["impact"]))
        return {"pd": round(pd, 4), "band": band_for(pd), "factors": factors, "version": self.version}


@lru_cache(maxsize=1)
def get_model() -> RiskModel:
    folder = Path(get_settings().model_dir)
    path = folder / "model_params.json"
    if not path.exists():                       # first run on a fresh clone: train once (~10 s)
        from .train import train
        train(folder)
    return RiskModel(json.loads(path.read_text(encoding="utf-8")), folder)


def metrics() -> dict:
    path = Path(get_settings().model_dir) / "metrics.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

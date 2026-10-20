"""Fitted logistic model for the risk windows.

The v1 windows are the URI times fixed multipliers, which are not
probabilities. Once outcome data exists, `python -m uri.calibrate` fits one
logistic model per horizon on snapshot features, and the engine reports those
probabilities instead. This module holds the feature definition shared by
fitting and scoring, so the two cannot drift apart.
"""

from __future__ import annotations

import json
import math

HORIZONS_H = (6, 12, 24, 36, 48)

FEATURES = (
    "news2", "qsofa", "mews", "sofa", "sofa_rise", "crs", "crs2", "charlson",
    "trajectory", "age", "organs_measured", "critical_values", "resp_support", "n_pressors", "sofa_acute",
    "hours_since_vitals", "hours_since_lactate", "hours_since_hgb", "hours_since_creatinine",
)


def features(snap: dict) -> dict:
    """Model inputs from a snapshot (the same dict the engine emits and logs)."""
    sc = snap["scores"]
    age = snap.get("context", {}).get("age")
    return {
        "news2": sc["news2"]["total"],
        "qsofa": sc["qsofa"]["total"],
        "mews": sc["mews"]["total"],
        "sofa": sc["sofa"]["total"],
        "sofa_rise": sc["sofa"].get("rise_48h") or 0.0,
        "crs": sc["crs"]["total"],
        "crs2": sc["crs2"]["total"],
        "charlson": sc["charlson"]["total"],
        "trajectory": snap["trajectory"]["points"],
        "age": age if age is not None else 60.0,
        # How much of SOFA was measured: ordering labs is itself a signal of
        # clinician concern, and it separates "normal" from "not checked".
        "organs_measured": 6 - len(sc["sofa"]["missing"]),
        "critical_values": sum(1 for c in snap.get("critical_values", []) if c["severity"] == "critical"),
        "resp_support": snap.get("context", {}).get("resp_support") or 0,
        "n_pressors": snap.get("context", {}).get("n_pressors") or 0,
        # organ dysfunction above the patient's own pre-episode baseline (Sepsis-3)
        "sofa_acute": sc["sofa"].get("acute", sc["sofa"]["total"]),
        # How old the latest value is (capped at 72 h; 72 when never measured):
        # lets calibration learn how much an older result still predicts,
        # instead of relying only on the fixed staleness windows.
        **{f"hours_since_{k}": snap.get("context", {}).get("hours_since", {}).get(k, 72.0)
           for k in ("vitals", "lactate", "hgb", "creatinine")},
    }


class Model:
    def __init__(self, spec: dict):
        if tuple(spec["features"]) != FEATURES:
            raise ValueError("model was fitted on a different feature set; refit it")
        self.spec = spec

    @classmethod
    def load(cls, path: str) -> "Model":
        with open(path) as fh:
            return cls(json.load(fh))

    def predict(self, snap: dict) -> dict[str, float]:
        x = features(snap)
        z = [(x[f] - m) / s for f, m, s in zip(FEATURES, self.spec["mean"], self.spec["scale"])]
        out = {}
        for h, p in self.spec["horizons"].items():
            eta = p["intercept"] + sum(c * zi for c, zi in zip(p["coef"], z))
            out[h] = 1 / (1 + math.exp(-eta))
        return out

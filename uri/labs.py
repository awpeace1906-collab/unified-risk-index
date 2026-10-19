"""Critical / flag thresholds from the v1 Patient Input sheet.

The v1 tool listed a "Critical / Flag Threshold" next to every lab but only
six labs fed the score. Here every threshold is evaluated on each recompute
and reported in the snapshot's ``critical_values`` panel. The count of
critical values is also a model feature, so the calibration can decide how
much weight they carry. Values are in canonical units (see events.PARAMETERS);
v1 magnesium thresholds in mEq/L are converted to mg/dL (x 1.215).
"""

from __future__ import annotations

from .config import Config

# (parameter, comparison, threshold, severity, label)
THRESHOLDS: list[tuple[str, str, float, str, str]] = [
    ("lactate", ">=", 4.0, "critical", "lactate >= 4.0 mmol/L"),
    ("lactate", ">=", 2.0, "flag", "lactate >= 2.0 mmol/L"),
    ("wbc", ">", 20, "critical", "WBC > 20"),
    ("wbc", "<", 2, "critical", "WBC < 2"),
    ("wbc", ">", 12, "flag", "WBC > 12 (SIRS)"),
    ("wbc", "<", 4, "flag", "WBC < 4 (SIRS)"),
    ("hgb", "<", 7, "critical", "Hgb < 7 g/dL"),
    ("hgb", "<", 10, "flag", "Hgb < 10 g/dL"),
    ("plt", "<", 50, "critical", "platelets < 50"),
    ("plt", "<", 100, "flag", "platelets < 100"),
    ("bnp", ">", 400, "critical", "BNP > 400 pg/mL"),
    ("bnp", ">", 100, "flag", "BNP > 100 pg/mL"),
    ("ph", "<", 7.30, "critical", "pH < 7.30"),
    ("ph", ">", 7.50, "critical", "pH > 7.50"),
    ("pco2", ">", 50, "flag", "pCO2 > 50 mmHg"),
    ("po2", "<", 40, "critical", "pO2 < 40 mmHg"),
    ("po2", "<", 60, "flag", "pO2 < 60 mmHg"),
    ("na", "<", 130, "critical", "Na < 130"),
    ("na", ">", 150, "critical", "Na > 150"),
    ("k", "<", 3.0, "critical", "K < 3.0"),
    ("k", ">", 6.0, "critical", "K > 6.0"),
    ("hco3", "<", 15, "critical", "HCO3 < 15"),
    ("hco3", ">", 32, "critical", "HCO3 > 32"),
    ("cl", "<", 98, "flag", "Cl < 98"),
    ("cl", ">", 108, "flag", "Cl > 108"),
    ("anion_gap", ">", 20, "critical", "anion gap > 20"),
    ("anion_gap", ">", 12, "flag", "anion gap > 12"),
    ("glucose", "<", 60, "critical", "glucose < 60 mg/dL"),
    ("glucose", ">", 400, "critical", "glucose > 400 mg/dL"),
    ("procalcitonin", ">=", 2.0, "critical", "procalcitonin >= 2.0 (sepsis)"),
    ("procalcitonin", ">=", 0.5, "flag", "procalcitonin >= 0.5 (bacterial)"),
    ("crp", ">", 50, "flag", "CRP > 50 mg/L"),
    ("inr", ">", 3.0, "critical", "INR > 3.0"),
    ("inr", ">", 1.5, "flag", "INR > 1.5"),
    ("fibrinogen", "<", 150, "critical", "fibrinogen < 150 mg/dL"),
    ("bilirubin", ">", 5, "flag", "bilirubin > 5 mg/dL"),
    ("ammonia", ">", 100, "critical", "ammonia > 100 umol/L (encephalopathy)"),
    ("ammonia", ">", 50, "flag", "ammonia > 50 umol/L"),
    ("mg", "<", 1.8, "flag", "Mg < 1.8 mg/dL (1.5 mEq/L)"),
    ("mg", ">", 3.6, "flag", "Mg > 3.6 mg/dL (3.0 mEq/L)"),
    ("phos", "<", 1.5, "critical", "phosphorus < 1.5 mg/dL"),
    ("phos", ">", 5.5, "flag", "phosphorus > 5.5 mg/dL"),
    ("cortisol", "<", 15, "flag", "random cortisol < 15 ug/dL"),
    ("tsh", "<", 0.1, "critical", "TSH < 0.1"),
    ("tsh", ">", 10, "critical", "TSH > 10"),
    ("alt", ">", 120, "flag", "ALT > 3x ULN"),
    ("ast", ">", 120, "flag", "AST > 3x ULN"),
]

_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b}


def critical_values(v: dict, ctx: dict, cfg: Config) -> list[dict]:
    """Every threshold breached, most severe per parameter and direction only."""
    out, seen = [], set()
    rows = list(THRESHOLDS)
    rows.append(("lipase", ">", 3 * cfg.lipase_uln_u_l, "flag", "lipase > 3x ULN"))
    rows.append(("troponin_hs", ">", cfg.troponin_url_ng_l, "flag", "hs-troponin > 99th percentile"))
    age = ctx.get("age")
    ddimer_cut = max(0.5, age * 0.01) if age and age > 50 else 0.5
    rows.append(("ddimer", ">", ddimer_cut, "flag", f"D-dimer > {ddimer_cut:g} (age-adjusted)"))
    for param, op, threshold, severity, label in rows:
        x = v.get(param)
        if not isinstance(x, (int, float)) or not _OPS[op](x, threshold):
            continue
        key = (param, op[0])
        if key in seen:  # a critical row for this direction already matched
            continue
        seen.add(key)
        out.append({"parameter": param, "value": x, "severity": severity, "label": label})
    return out

"""Component scores (NEWS2, qSOFA, MEWS, CRS) and the URI composite.

Pure functions over a dict of current values in canonical units. A missing
parameter scores 0 and is reported in ``missing`` so the snapshot can show how
complete the picture is (the same behaviour as the v1 spreadsheet, made
visible). See docs/EXCEL_RECONCILIATION.md for where these deliberately differ
from the v1 spreadsheet formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .registry import ALL, OBSTETRIC_IDS


@dataclass
class ScoreResult:
    total: float
    components: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"total": round(self.total, 2), "components": self.components,
                "missing": self.missing, "flags": self.flags}


def avpu_level(v: dict) -> str | None:
    """A / C / V / P / U from charted AVPU, else approximated from GCS."""
    if v.get("avpu"):
        return str(v["avpu"]).upper()[0]
    gcs = v.get("gcs")
    if gcs is None:
        return None
    if gcs >= 15:
        return "A"
    if gcs >= 13:
        return "V"
    if gcs >= 9:
        return "P"
    return "U"


def altered_mentation(v: dict) -> bool | None:
    level = avpu_level(v)
    return None if level is None else level != "A"


def shock_index(v: dict) -> float | None:
    hr, sbp = v.get("hr"), v.get("sbp")
    if hr is None or not sbp:
        return None
    return hr / sbp


def mean_arterial(v: dict) -> float | None:
    if v.get("map") is not None:
        return v["map"]
    if v.get("sbp") is not None and v.get("dbp") is not None:
        return (v["sbp"] + 2 * v["dbp"]) / 3
    return None


# --- NEWS2 (Royal College of Physicians, 2017) ---------------------------------

def _news2_rr(x):
    return 3 if x <= 8 else 1 if x <= 11 else 0 if x <= 20 else 2 if x <= 24 else 3


def _news2_spo2_scale1(x):
    return 3 if x <= 91 else 2 if x <= 93 else 1 if x <= 95 else 0


def _news2_spo2_scale2(x, on_o2):
    if x <= 83:
        return 3
    if x <= 85:
        return 2
    if x <= 87:
        return 1
    if x <= 92 or not on_o2:
        return 0
    return 1 if x <= 94 else 2 if x <= 96 else 3


def _news2_sbp(x):
    return 3 if x <= 90 else 2 if x <= 100 else 1 if x <= 110 else 0 if x <= 219 else 3


def _news2_hr(x):
    return 3 if x <= 40 else 1 if x <= 50 else 0 if x <= 90 else 1 if x <= 110 else 2 if x <= 130 else 3


def _news2_temp(x):
    return 3 if x <= 35.0 else 1 if x <= 36.0 else 0 if x <= 38.0 else 1 if x <= 39.0 else 2


def news2(v: dict, hypercapnic_target: bool = False) -> ScoreResult:
    r = ScoreResult(0)
    on_o2 = bool(v.get("o2_supplemental")) or (v.get("fio2") is not None and v["fio2"] > 21)
    parts = {
        "rr": (v.get("rr"), _news2_rr),
        "spo2": (v.get("spo2"), (lambda x: _news2_spo2_scale2(x, on_o2)) if hypercapnic_target
                 else _news2_spo2_scale1),
        "sbp": (v.get("sbp"), _news2_sbp),
        "hr": (v.get("hr"), _news2_hr),
        "temp_c": (v.get("temp_c"), _news2_temp),
    }
    for key, (value, fn) in parts.items():
        if value is None:
            r.missing.append(key)
            continue
        r.components[key] = fn(value)
    level = avpu_level(v)
    if level is None:
        r.missing.append("consciousness")
    else:
        r.components["consciousness"] = 0 if level == "A" else 3
    r.components["o2"] = 2 if on_o2 else 0
    r.total = sum(r.components.values())
    if any(p == 3 for p in r.components.values()):
        r.flags.append("single parameter scored 3")
    return r


# --- qSOFA (Sepsis-3, Singer 2016) --------------------------------------------

def qsofa(v: dict) -> ScoreResult:
    r = ScoreResult(0)
    rr, sbp, altered = v.get("rr"), v.get("sbp"), altered_mentation(v)
    for key, value, hit in (("rr", rr, rr is not None and rr >= 22),
                            ("mentation", altered, bool(altered)),
                            ("sbp", sbp, sbp is not None and sbp <= 100)):
        if value is None:
            r.missing.append(key)
        else:
            r.components[key] = int(hit)
    r.total = sum(r.components.values())
    if r.total >= 2:
        r.flags.append("qSOFA >= 2: sepsis concern")
    return r


# --- MEWS (Subbe 2001) --------------------------------------------------------

def mews(v: dict) -> ScoreResult:
    r = ScoreResult(0)

    def sbp(x):
        return 3 if x <= 70 else 2 if x <= 80 else 1 if x <= 100 else 0 if x < 200 else 2

    def hr(x):
        return 2 if x < 41 else 1 if x <= 50 else 0 if x <= 100 else 1 if x <= 110 else 2 if x < 130 else 3

    def rr(x):
        return 2 if x < 9 else 0 if x <= 14 else 1 if x <= 20 else 2 if x < 30 else 3

    def temp(x):
        return 2 if x < 35 else 0 if x < 38.5 else 2

    for key, fn in (("sbp", sbp), ("hr", hr), ("rr", rr), ("temp_c", temp)):
        if v.get(key) is None:
            r.missing.append(key)
        else:
            r.components[key] = fn(v[key])
    level = avpu_level(v)
    if level is None:
        r.missing.append("avpu")
    else:
        r.components["avpu"] = {"A": 0, "C": 1, "V": 1, "P": 2, "U": 3}[level]
    r.total = sum(r.components.values())
    return r


# --- Composite Risk Score (URI v1 novel component) ---------------------------

def crs(v: dict, ctx: dict, factors: set[str], meds: set[str], news2_total: float,
        qsofa_total: float, cfg: Config) -> ScoreResult:
    r = ScoreResult(0)
    d = r.components
    d["vital_instability"] = round(news2_total * 0.22, 2)
    d["sepsis_screen"] = qsofa_total * 4

    metabolic = 0
    if v.get("ph") is not None and v["ph"] < 7.30:
        metabolic += 4
    lac = v.get("lactate")
    if lac is not None:
        metabolic += 4 if lac > 4 else 2 if lac > 2 else 0
    d["metabolic"] = metabolic

    cr = v.get("creatinine")
    d["renal"] = 0 if cr is None else 4 if cr > 3 else 2 if cr > 1.5 else 0

    hgb = v.get("hgb")
    d["hematologic"] = 0 if hgb is None else 4 if hgb < 7 else 2 if hgb < 10 else 0

    cardiac = 0
    if v.get("troponin_hs") is not None and v["troponin_hs"] > cfg.troponin_url_ng_l:
        cardiac += 3
    if (v.get("bnp") is not None and v["bnp"] > cfg.bnp_severe_pg_ml) or \
       (v.get("ntprobnp") is not None and v["ntprobnp"] > cfg.ntprobnp_severe_pg_ml):
        cardiac += 2
    d["cardiac"] = cardiac

    d["comorbidity"] = sum(ALL[f].crs_points for f in factors if f in ALL and f not in OBSTETRIC_IDS)
    d["medication"] = sum(ALL[m].crs_points for m in meds if m in ALL)

    frailty = 0
    age = ctx.get("age")
    if age is not None:
        frailty += 2 if age > 80 else 1 if age > 65 else 0
    else:
        r.missing.append("age")
    if ctx.get("cfs") is not None and ctx["cfs"] >= 5:
        frailty += 2
    d["frailty_age"] = frailty

    ob_log_or = sum(ALL[f].log_odds for f in factors if f in OBSTETRIC_IDS)
    d["obstetric_hemorrhage"] = round(min(cfg.ob_points_cap, cfg.ob_points_per_log_or * ob_log_or), 2)

    for lab in ("lactate", "creatinine", "hgb", "ph", "troponin_hs"):
        if v.get(lab) is None:
            r.missing.append(lab)
    r.total = min(float(cfg.score_max["crs"]), sum(d.values()))
    return r


# --- URI composite -----------------------------------------------------------

BANDS = [(81, "CRITICAL"), (61, "HIGH"), (41, "MODERATE"), (21, "LOW-MODERATE"), (0, "LOW")]
BAND_ORDER = {name: i for i, (_, name) in enumerate(reversed(BANDS))}


def band(score: float) -> str:
    # v1 bands are integer ranges (0-20, 21-40, ...); round before banding.
    s = round(score)
    return next(name for lo, name in BANDS if s >= lo)


def window_level(pct: float) -> str:
    if pct >= 70:
        return "VERY HIGH"
    if pct >= 45:
        return "HIGH"
    if pct >= 25:
        return "MODERATE"
    if pct >= 10:
        return "LOW-MOD"
    return "LOW"


def composite(totals: dict, cfg: Config) -> tuple[float, dict]:
    """URI v1: weighted sum of max-normalized component scores, 0-100."""
    breakdown = {}
    uri = 0.0
    for name, w in cfg.weights().items():
        raw = totals[name]
        norm = min(1.0, raw / cfg.score_max[name])
        contribution = norm * w * 100
        uri += contribution
        breakdown[name] = {"raw": round(raw, 2), "normalized": round(norm, 3),
                           "weight": w, "contribution": round(contribution, 1)}
    return round(uri, 1), breakdown


def time_windows(uri: float, cfg: Config) -> list[dict]:
    out = []
    for window, m in cfg.window_multipliers.items():
        pct = min(cfg.window_cap, round(uri * m))
        out.append({"window": window, "estimate_pct": pct, "level": window_level(pct)})
    return out

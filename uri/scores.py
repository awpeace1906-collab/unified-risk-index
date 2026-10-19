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
from .registry import ALL, CHARLSON, COMORBIDITY_IDS


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

    d["comorbidity"] = sum(ALL[f].crs_points for f in factors if f in COMORBIDITY_IDS)
    d["medication"] = sum(ALL[m].crs_points for m in meds if m in ALL)

    d["frailty_age"] = _frailty_age(ctx, r)

    for lab in ("lactate", "creatinine", "hgb", "ph", "troponin_hs"):
        if v.get(lab) is None:
            r.missing.append(lab)
    r.total = min(float(cfg.score_max["crs"]), sum(d.values()))
    return r


def _frailty_age(ctx: dict, r: ScoreResult) -> int:
    points = 0
    age = ctx.get("age")
    if age is not None:
        points += 2 if age > 80 else 1 if age > 65 else 0
    else:
        r.missing.append("age")
    if ctx.get("cfs") is not None and ctx["cfs"] >= 5:
        points += 2
    return points


# --- SOFA (Vincent 1996; Sepsis-3 organ dysfunction) -------------------------

def estimated_fio2(v: dict) -> tuple[float | None, bool]:
    """(FiO2 %, measured?) from charted FiO2, nasal-cannula flow, or room air."""
    if v.get("fio2") is not None:
        return v["fio2"], True
    if (v.get("resp_support") or 0) >= 2:
        return None, False  # HFNC / NIV / ventilator flow says nothing about FiO2
    if v.get("o2_supplemental"):
        flow = v.get("o2_flow")
        # ~4% per L/min over room air is the usual low-flow approximation.
        return (min(45.0, 21 + 4 * flow), False) if flow is not None else (None, False)
    return 21.0, False


def pf_equivalent(v: dict) -> tuple[float | None, str]:
    """PaO2/FiO2, or the S/F-derived equivalent (Rice 2007: S/F = 64 + 0.84 P/F)
    when no arterial PaO2 is available. S/F is only used for SpO2 <= 97%,
    where the oxyhemoglobin curve is not flat."""
    fio2, _ = estimated_fio2(v)
    if fio2 is None:
        return None, ""
    frac = fio2 / 100
    if v.get("po2") is not None:
        return v["po2"] / frac, "P/F"
    spo2 = v.get("spo2")
    if spo2 is None:
        return None, ""
    if spo2 > 97:
        if fio2 <= 21:
            return 400.0, "S/F"  # saturating on room air: no dysfunction detectable
        # On oxygen the curve is flat above 97%; scoring at 97% gives the
        # conservative (worst plausible) estimate.
        spo2 = 97
    return (spo2 / frac - 64) / 0.84, "S/F"


def sofa(v: dict, meds: set[str], n_pressors: int = 0) -> ScoreResult:
    r = ScoreResult(0)
    d = r.components

    pf, method = pf_equivalent(v)
    if pf is None:
        r.missing.append("respiration")
    else:
        # 3-4 points require respiratory support; a charted FiO2 above room
        # air (HFNC / NIV / ventilator) is taken as support.
        fio2, measured = estimated_fio2(v)
        support = (measured and fio2 > 21) or (v.get("resp_support") or 0) >= 2
        pts = 0 if pf >= 400 else 1 if pf >= 300 else 2 if pf >= 200 else (3 if pf >= 100 else 4)
        d["respiration"] = pts if support else min(pts, 2)
        if method == "S/F":
            r.flags.append("respiration from S/F surrogate")

    plt = v.get("plt")
    if plt is None:
        r.missing.append("coagulation")
    else:
        d["coagulation"] = 0 if plt >= 150 else 1 if plt >= 100 else 2 if plt >= 50 else 3 if plt >= 20 else 4

    bili = v.get("bilirubin")
    if bili is None:
        r.missing.append("liver")
    else:
        d["liver"] = 0 if bili < 1.2 else 1 if bili < 2.0 else 2 if bili < 6.0 else 3 if bili < 12.0 else 4

    m = mean_arterial(v)
    if n_pressors >= 2 or "vasopressor" in meds:
        # Doses are not streamed yet. One agent is scored 3 (norepinephrine /
        # epinephrine <= 0.1 mcg/kg/min equivalent); two or more concurrent
        # agents imply refractory shock and are scored 4.
        d["cardiovascular"] = 4 if n_pressors >= 2 else 3
        r.flags.append("cardiovascular from vasopressor count (dose unknown)")
    elif m is None:
        r.missing.append("cardiovascular")
    else:
        d["cardiovascular"] = 1 if m < 70 else 0

    gcs = v.get("gcs")
    if gcs is None:
        r.missing.append("cns")
    else:
        d["cns"] = 0 if gcs >= 15 else 1 if gcs >= 13 else 2 if gcs >= 10 else 3 if gcs >= 6 else 4

    cr = v.get("creatinine")
    if cr is None:
        r.missing.append("renal")
    else:
        d["renal"] = 0 if cr < 1.2 else 1 if cr < 2.0 else 2 if cr < 3.5 else 3 if cr < 5.0 else 4

    r.total = sum(d.values())
    return r


# --- Padua Prediction Score for VTE (Barbar 2010) ------------------------------

def padua(factors: set[str], age: float | None) -> ScoreResult:
    """Built from registry factors. Reduced mobility (3) and hormonal therapy
    (1) are not in the stream, so the score can under-estimate."""
    r = ScoreResult(0)
    items = [
        ("active_cancer", 3, bool(factors & {"malignancy", "cci_malignancy", "cci_metastatic", "active_chemo"})),
        ("previous_vte", 3, "dvt_pe" in factors),
        ("thrombophilia", 3, "thrombophilia" in factors),
        ("recent_trauma_surgery", 2, "recent_surgery" in factors),
        ("age_ge_70", 1, age is not None and age >= 70),
        ("heart_or_respiratory_failure", 1, bool(factors & {"chf", "cci_chf", "copd", "cci_pulmonary"})),
        ("mi_or_ischemic_stroke", 1, bool(factors & {"cci_mi", "stroke_tia"})),
        ("infection_or_rheumatologic", 1, bool(factors & {"sepsis_prior", "cci_rheumatic", "bone_joint_infection",
                                                          "tb_infectious"})),
        ("obesity", 1, "obesity" in factors),
    ]
    r.components = {name: pts for name, pts, hit in items if hit}
    r.total = sum(r.components.values())
    r.missing = ["reduced_mobility", "hormonal_therapy"]
    return r


# --- Charlson Comorbidity Index (Quan 2011 weights) --------------------------

_CCI_SUPERSEDES = {"cci_dm_complicated": "cci_dm", "cci_liver_severe": "cci_liver_mild",
                   "cci_metastatic": "cci_malignancy"}


def charlson(factors: set[str]) -> ScoreResult:
    present = {f.id for f in CHARLSON if f.id in factors}
    for higher, lower in _CCI_SUPERSEDES.items():
        if higher in present:
            present.discard(lower)
    r = ScoreResult(0)
    r.components = {f.id: f.charlson_weight for f in CHARLSON if f.id in present}
    r.total = sum(r.components.values())
    return r


# --- CRS2: biomarkers and host factors not already in NEWS2 or SOFA ----------

def crs2(v: dict, ctx: dict, factors: set[str], meds: set[str], cci_total: float, cfg: Config) -> ScoreResult:
    """Unlike v1 CRS it does not re-score vital signs (NEWS2 covers them) or
    creatinine/platelets/bilirubin (SOFA covers them), and it uses the
    Charlson index instead of ad-hoc comorbidity points."""
    r = ScoreResult(0)
    d = r.components

    def pts(key, rule):
        return 0 if v.get(key) is None else rule(v[key])

    d["metabolic"] = min(6, pts("lactate", lambda x: 4 if x > 4 else 2 if x > 2 else 0)
                         + pts("ph", lambda x: 2 if x < 7.20 else 1 if x < 7.30 else 0)
                         + pts("hco3", lambda x: 1 if x < 18 else 0))
    d["electrolyte_glucose"] = min(4, pts("k", lambda x: 2 if x > 6.0 or x < 3.0 else 1 if x > 5.5 else 0)
                                   + pts("na", lambda x: 2 if x < 125 or x > 155 else 1 if x < 130 or x > 150 else 0)
                                   + pts("glucose", lambda x: 2 if x < 60 or x > 400 else 1 if x < 70 or x > 250 else 0))
    d["hematologic"] = min(4, pts("hgb", lambda x: 4 if x < 7 else 2 if x < 10 else 0)
                           + pts("inr", lambda x: 2 if x > 3.0 else 1 if x > 1.5 else 0))
    cardiac = pts("troponin_hs", lambda x: 3 if x > cfg.troponin_url_ng_l else 0)
    if (v.get("bnp") is not None and v["bnp"] > cfg.bnp_severe_pg_ml) or \
       (v.get("ntprobnp") is not None and v["ntprobnp"] > cfg.ntprobnp_severe_pg_ml):
        cardiac += 2
    d["cardiac"] = cardiac
    d["infection"] = min(4, pts("procalcitonin", lambda x: 2 if x >= 2 else 1 if x >= 0.5 else 0)
                         + pts("wbc", lambda x: 2 if x > 20 or x < 2 else 1 if x > 12 or x < 4 else 0))
    d["comorbidity_charlson"] = min(6, cci_total)
    d["medication"] = sum(ALL[m].crs_points for m in meds if m in ALL)
    d["frailty_age"] = _frailty_age(ctx, r)

    for lab in ("lactate", "hgb", "troponin_hs", "k", "glucose", "wbc"):
        if v.get(lab) is None:
            r.missing.append(lab)
    r.total = min(float(cfg.score_max["crs2"]), sum(d.values()))
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


def floor(news2_result: ScoreResult, sofa_rise: float | None, v: dict, meds: set[str]) -> tuple[float, str]:
    """Minimum URI implied by the escalation tiers of the validated
    instruments themselves. A weighted average dilutes one alarming channel
    (NEWS2 7 with normal labs scores ~16 by weight alone); the floor keeps the
    URI at least as urgent as the published tier that channel triggers.
    Returns (floor, reason)."""
    tiers = [(0.0, "")]
    n2 = news2_result.total
    if n2 >= 7:
        tiers.append((61.0, "NEWS2 >= 7 (RCP high: emergency response)"))
    elif n2 >= 5:
        tiers.append((41.0, "NEWS2 5-6 (RCP medium: urgent response)"))
    elif news2_result.flags:
        tiers.append((21.0, "NEWS2 single parameter = 3 (RCP low-medium)"))
    if sofa_rise is not None and sofa_rise >= 2:
        tiers.append((41.0, "acute SOFA rise >= 2 (Sepsis-3 organ dysfunction)"))
    if "vasopressor" in meds and v.get("lactate") is not None and v["lactate"] > 2:
        tiers.append((81.0, "vasopressor with lactate > 2 (Sepsis-3 septic shock criteria)"))
    return max(tiers)


def composite(totals: dict, weights: dict, cfg: Config) -> tuple[float, dict]:
    """Weighted sum of max-normalized component scores, 0-100."""
    breakdown = {}
    uri = 0.0
    for name, w in weights.items():
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

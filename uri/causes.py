"""Correctable-cause panel.

The 15 causes, actions and time sensitivities are carried over verbatim from
the v1 Risk Dashboard; v2 adds postpartum hemorrhage and makes every trigger
an explicit rule over streamed data, with the reason it fired. Rules fire only
on data that is present: a missing lab never triggers a cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .scores import mean_arterial, shock_index


@dataclass(frozen=True)
class Cause:
    priority: int
    name: str
    action: str
    time_sensitivity: str
    rule: Callable[["Facts"], str | None]


@dataclass
class Facts:
    v: dict
    factors: set
    meds: set
    signals: set
    qsofa: float
    ob_active: bool

    def has(self, key) -> bool:
        return self.v.get(key) is not None

    def lt(self, key, x) -> bool:
        return self.has(key) and self.v[key] < x

    def gt(self, key, x) -> bool:
        return self.has(key) and self.v[key] > x


def _why(*pairs) -> str | None:
    hits = [label for cond, label in pairs if cond]
    return ", ".join(hits) if hits else None


def _volume(f: Facts):
    m = mean_arterial(f.v)
    return _why((f.lt("sbp", 90), f"SBP {f.v.get('sbp')}"),
                (m is not None and m < 65, f"MAP {m:.0f}" if m is not None else ""),
                (f.gt("hr", 100) and f.gt("lactate", 2), "HR > 100 with lactate > 2"))


def _sepsis(f: Facts):
    t = f.v.get("temp_c")
    sirs_temp = t is not None and (t > 38.3 or t < 36)
    sirs_wbc = f.gt("wbc", 12) or f.lt("wbc", 4)
    return _why((f.qsofa >= 2, "qSOFA >= 2"),
                (f.gt("procalcitonin", 0.49), "procalcitonin >= 0.5"),
                (sirs_temp and sirs_wbc, "abnormal temperature and WBC"))


def _electrolyte(f: Facts):
    return _why((f.gt("k", 6.0), f"K {f.v.get('k')}"), (f.lt("k", 3.0), f"K {f.v.get('k')}"),
                (f.lt("na", 130), f"Na {f.v.get('na')}"), (f.gt("na", 150), f"Na {f.v.get('na')}"))


def _hypoglycemia(f: Facts):
    return _why((f.lt("glucose", 60), f"glucose {f.v.get('glucose')}"))


def _acs(f: Facts):
    return _why(("troponin_rising" in f.signals, "rising hs-troponin"))


def _adhf(f: Facts):
    bnp_hi = f.gt("bnp", 400) or f.gt("ntprobnp", 1800)
    return _why((bnp_hi and (f.lt("spo2", 92) or "chf" in f.factors), "natriuretic peptide high with hypoxia / CHF"))


def _resp(f: Facts):
    return _why((f.lt("spo2", 90), f"SpO2 {f.v.get('spo2')}"), (f.gt("rr", 25), f"RR {f.v.get('rr')}"),
                ("oxygenation_decline" in f.signals, "worsening oxygenation"))


def _acidosis(f: Facts):
    return _why((f.lt("ph", 7.30), f"pH {f.v.get('ph')}"), (f.lt("hco3", 18), f"HCO3 {f.v.get('hco3')}"))


def _pe(f: Facts):
    risk = "dvt_pe" in f.factors or f.gt("ddimer", 0.5)
    return _why((risk and f.lt("spo2", 94) and f.gt("hr", 100), "hypoxia + tachycardia with VTE risk / D-dimer"))


def _dysrhythmia(f: Facts):
    return _why((f.gt("hr", 150), f"HR {f.v.get('hr')}"), (f.lt("hr", 40), f"HR {f.v.get('hr')}"))


def _htn_emergency(f: Facts):
    organ = f.lt("gcs", 15) or "troponin_rising" in f.signals or "aki" in f.signals
    return _why((f.gt("sbp", 179) and organ, "SBP >= 180 with end-organ signal"))


def _opioid(f: Facts):
    return _why(("opioid" in f.meds and f.lt("rr", 10), "RR < 10 on opioids"),
                (f.gt("etco2", 50) and "opioid" in f.meds, "ETCO2 > 50 on opioids"))


def _adrenal(f: Facts):
    steroid = "steroid_dependent" in f.factors or "adrenal_insufficiency" in f.factors or "corticosteroid" in f.meds
    return _why((steroid and f.lt("sbp", 90), "hypotension in steroid-dependent patient"),
                (f.lt("na", 130) and f.gt("k", 5.5) and f.lt("sbp", 90), "hypoNa + hyperK + hypotension"))


def _dka(f: Facts):
    acid = f.lt("ph", 7.30) or f.lt("hco3", 18) or f.gt("anion_gap", 12)
    return _why((f.gt("glucose", 250) and acid, "glucose > 250 with acidosis / AG"),
                (f.gt("glucose", 600), f"glucose {f.v.get('glucose')}"))


def _gi_bleed(f: Facts):
    unstable = f.gt("hr", 100) or f.lt("sbp", 90)
    context = "gi_bleed" in f.factors or "anticoagulant" in f.meds
    return _why(("hgb_drop" in f.signals and (unstable or context), "falling Hgb with instability / bleeding risk"),
                (f.lt("hgb", 8) and unstable and context, "Hgb < 8 with instability"))


def _pph(f: Facts):
    if not f.ob_active:
        return None
    si = shock_index(f.v)
    return _why(("qbl_ge_1000" in f.signals, "QBL >= 1000 mL"),
                ("hgb_drop" in f.signals, "falling Hgb"),
                (si is not None and si >= 0.9, f"shock index {si:.2f}" if si is not None else ""),
                ("fibrinogen_low" in f.signals, "fibrinogen < 200"))


CAUSES: list[Cause] = [
    Cause(1, "Fluid resuscitation / volume depletion",
          "IV fluid challenge 500 mL NS or LR, reassess MAP q30min, consider POC echo LVOT-VTI", "0-1 hour", _volume),
    Cause(2, "Sepsis / undifferentiated infection",
          "Blood cultures x2 before Abx -> broad-spectrum Abx within 1 hr of recognition; consider source control",
          "0-1 hour", _sepsis),
    Cause(3, "Hyperkalemia / electrolyte emergency",
          "Calcium gluconate IV, insulin/dextrose, sodium bicarb, loop diuretic, consider dialysis consult",
          "0-30 min", _electrolyte),
    Cause(4, "Hypoglycemia",
          "25 g D50 IV push; recheck glucose q15min; address underlying cause (insulin, sulfonylurea, hepatic)",
          "0-15 min", _hypoglycemia),
    Cause(5, "Acute coronary syndrome / STEMI",
          "ASA 324 mg, heparin, cath lab activation (STEMI); ACS protocol; avoid beta-blockers if in shock",
          "<90 min door-to-balloon", _acs),
    Cause(6, "Acute decompensated heart failure",
          "IV furosemide, CPAP/BiPAP, nitrates (if SBP > 110), avoid fluids; consider inotropes if cardiogenic shock",
          "0-2 hours", _adhf),
    Cause(7, "Respiratory failure / hypoxia",
          "Supplemental O2 -> HFNC/NIV -> early RSI if failing; identify cause: PE, pneumonia, COPD, fluid",
          "0-30 min", _resp),
    Cause(8, "Metabolic acidosis (HAGMA / NAGMA)",
          "Treat underlying cause: DKA -> insulin/fluids, lactic -> resuscitate/source control; NaHCO3 if pH < 7.1",
          "0-2 hours", _acidosis),
    Cause(9, "Pulmonary embolism (massive / submassive)",
          "Anticoagulate; systemic thrombolytics if massive PE + shock; IR/CT-PE consult for submassive",
          "0-1 hour", _pe),
    Cause(10, "Dysrhythmia (AF-RVR, VT, SVT, bradycardia)",
          "Unstable -> cardioversion; stable SVT -> adenosine; AF-RVR -> rate control; VT -> amiodarone/lidocaine",
          "Immediate if unstable", _dysrhythmia),
    Cause(11, "Hypertensive emergency (end-organ damage)",
          "Nicardipine or labetalol IV; goal 20-25% MAP reduction in 1 hr; avoid rapid drops (stroke risk)",
          "0-1 hour", _htn_emergency),
    Cause(12, "Opioid / sedative toxidrome",
          "Naloxone 0.4-2 mg IV/IM; supportive airway; consider infusion for long-acting agents",
          "0-5 min if apneic", _opioid),
    Cause(13, "Adrenal crisis / cortisol deficiency",
          "Hydrocortisone 100 mg IV bolus -> 50 mg q6h; aggressive IV fluids; electrolyte correction",
          "0-1 hour", _adrenal),
    Cause(14, "DKA / HHS",
          "Insulin gtt, IV fluids, K+ replacement before insulin if K+ < 3.5; HHS -> careful rehydration, no insulin bolus",
          "0-2 hours", _dka),
    Cause(15, "GI bleed / hemorrhagic shock",
          "2 large-bore IVs, type & crossmatch, pRBC transfusion (target Hgb > 7), GI/IR consult; hold anticoagulants",
          "0-1 hour", _gi_bleed),
    # Only fires for obstetric patients, where it outranks the general list.
    Cause(0, "Postpartum hemorrhage",
          "Activate obstetric hemorrhage protocol; assess the 4 Ts (tone, trauma, tissue, thrombin); uterotonics; "
          "TXA 1 g IV within 3 h of birth; massive transfusion protocol; replace fibrinogen if < 200 mg/dL",
          "0-30 min", _pph),
]


def evaluate(facts: Facts) -> list[dict]:
    out = []
    for c in CAUSES:
        why = c.rule(facts)
        if why:
            out.append({"priority": c.priority, "cause": c.name, "triggered_by": why,
                        "action": c.action, "time_sensitivity": c.time_sensitivity})
    return sorted(out, key=lambda c: c["priority"])

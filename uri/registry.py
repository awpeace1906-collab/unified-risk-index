"""Risk-factor registry.

"Step One: rigorously define the risk factors." Every factor the engine
monitors is declared here once, with how it is detected in the EMR and what
evidence backs its weight. Factors are monitored whether or not they carry a
weight yet: an unweighted factor still shows up in the snapshot and can still
trigger a correctable-cause rule, it just adds no points until there is
evidence to weight it.

Evidence status values:
  pooled_or  - pooled odds ratio from a systematic review / meta-analysis
  v1_prior   - point weight carried over from URI v1 (expert prior, unvalidated)
  tracked    - monitored and displayed; no weight until evidence is attached
  candidate  - proposed factor; confirm against the source review before use
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskFactor:
    id: str
    label: str
    domain: str
    icd10: tuple[str, ...] = ()        # ICD-10-CM prefixes (dots ignored)
    ingredients: tuple[str, ...] = ()  # medication ingredient keywords
    crs_points: float = 0.0            # contribution to the CRS when present
    odds_ratio: float | None = None    # pooled OR, when the evidence provides one
    status: str = "tracked"
    source: str = ""
    notes: str = ""

    @property
    def log_odds(self) -> float:
        return math.log(self.odds_ratio) if self.odds_ratio else 0.0


V1 = "URI v1 Excel tool, Composite Risk Score"
PPH_SLIDE = ("Systematic review & meta-analysis of PPH risk factors "
             "(27 studies, 47 factors assessed, 15 definite/likely); pooled OR")


def _f(id, label, domain, icd10=(), **kw) -> RiskFactor:
    return RiskFactor(id=id, label=label, domain=domain, icd10=tuple(icd10), **kw)


# --- Comorbidities (the v1 PMHx panel) ----------------------------------------
COMORBIDITIES: list[RiskFactor] = [
    _f("chf", "CHF / HFrEF / HFpEF", "cardiovascular", ["I50"], crs_points=2.0, status="v1_prior", source=V1),
    _f("copd", "COPD / emphysema", "pulmonary", ["J44", "J43"], crs_points=2.0, status="v1_prior", source=V1),
    _f("cad", "CAD / prior MI", "cardiovascular", ["I25"], crs_points=1.5, status="v1_prior", source=V1),
    _f("ckd", "CKD / ESRD", "renal", ["N18", "Z992"], crs_points=1.0, status="v1_prior", source=V1,
       notes="v1 formula referenced the DM type 1 row; the manuscript lists CKD. Confirm."),
    _f("malignancy", "Active malignancy", "oncologic", ["C"], crs_points=1.5, status="v1_prior", source=V1),
    _f("htn", "Hypertension", "cardiovascular", ["I10", "I11", "I12", "I13", "I15", "I16"]),
    _f("dm1", "Diabetes mellitus type 1", "metabolic", ["E10"]),
    _f("dm2", "Diabetes mellitus type 2", "metabolic", ["E11"]),
    _f("stroke_tia", "Stroke / TIA history", "neurologic", ["I63", "G45", "Z8673"]),
    _f("dvt_pe", "DVT / PE history", "hematologic", ["Z8671", "I26", "I824"]),
    _f("sepsis_prior", "Sepsis (prior / current)", "infectious", ["A40", "A41", "R652"]),
    _f("aud", "Alcohol use disorder", "behavioral", ["F10"]),
    _f("obesity", "Obesity (BMI > 35)", "metabolic", ["E66", "Z6835", "Z6836", "Z6837", "Z6838", "Z6839", "Z684"]),
    _f("dementia", "Dementia / cognitive impairment", "neurologic", ["F01", "F02", "F03", "G30"]),
    _f("immunosuppressant_tx", "Immunosuppressant therapy", "immunologic", ["Z796"]),
    _f("afib", "Dysrhythmia (AF / flutter)", "cardiovascular", ["I48"]),
    _f("asthma", "Asthma", "pulmonary", ["J45"]),
    _f("valvular", "Valvular heart disease", "cardiovascular", ["I05", "I06", "I07", "I08", "I34", "I35", "I36", "I37"]),
    _f("pulm_htn", "Pulmonary HTN / cor pulmonale", "pulmonary", ["I27"]),
    _f("cirrhosis", "Cirrhosis / hepatic failure", "hepatic", ["K703", "K74", "K72"]),
    _f("immunocompromised", "Immunocompromised", "immunologic", ["D80", "D81", "D82", "D83", "D84"]),
    _f("seizure", "Seizure disorder", "neurologic", ["G40"]),
    _f("coagulopathy", "Coagulopathy", "hematologic", ["D65", "D66", "D67", "D68"]),
    _f("vent_dependent", "Prior intubation / vent dependent", "pulmonary", ["Z9911"]),
    _f("sud", "Substance use disorder", "behavioral", ["F11", "F12", "F13", "F14", "F15", "F16", "F18", "F19"]),
    _f("malnutrition", "Malnutrition / cachexia", "metabolic", ["E40", "E41", "E42", "E43", "E44", "E45", "E46", "R64"]),
    _f("frailty_dx", "Frailty", "functional", ["R54"]),
    _f("transplant", "Transplant recipient", "immunologic", ["Z94"]),
    _f("dysnatremia", "Hypo/hypernatremia history", "metabolic", ["E870", "E871"]),
    _f("adrenal_insufficiency", "Adrenal insufficiency", "endocrine", ["E271", "E272", "E274"]),
    _f("anemia", "Anemia", "hematologic", ["D50", "D51", "D52", "D53", "D55", "D56", "D57", "D58", "D59",
                                            "D60", "D61", "D62", "D63", "D64"]),
    _f("chronic_opioid", "Chronic pain / opioid use", "behavioral", ["G89", "Z79891"]),
    _f("osa", "OSA / CPAP dependent", "pulmonary", ["G4733"]),
    _f("hiv", "HIV / AIDS", "immunologic", ["B20", "Z21"]),
    _f("aortic_aneurysm", "Known aortic aneurysm", "cardiovascular", ["I71"]),
    _f("bone_joint_infection", "Septic arthritis / osteomyelitis", "infectious", ["M00", "M86"]),
    _f("steroid_dependent", "Steroid dependent", "endocrine", ["Z7952"]),
    _f("active_chemo", "Active chemotherapy", "oncologic", ["Z5111"]),
    _f("radiation", "Active radiation therapy", "oncologic", ["Z510"]),
    _f("hyperkalemia_hx", "Hyperkalemia history", "renal", ["E875"]),
    _f("thyroid", "Thyroid disease", "endocrine", ["E01", "E02", "E03", "E04", "E05", "E06", "E07"]),
    _f("gi_bleed", "GI bleed (recent / active)", "hematologic", ["K920", "K921", "K922", "K625", "I8511"]),
    _f("psych", "Psychiatric comorbidity", "behavioral", ["F2", "F3", "F40", "F41", "F43"]),
    _f("neuromuscular", "Neuromuscular disease", "neurologic", ["G12", "G35", "G70", "G71"]),
    _f("tb_infectious", "TB / active infectious disease", "infectious", ["A15", "A16", "A17", "A18", "A19"]),
    _f("hardware_line", "Indwelling hardware / line", "infectious", ["Z95", "Z96", "Z452"]),
    _f("dnr", "DNR / DNI", "goals_of_care", ["Z66"]),
    _f("homeless", "No fixed address", "social", ["Z590"]),
    _f("nonadherence", "Medication non-adherence", "social", ["Z911"]),
    _f("dialysis", "Dialysis patient", "renal", ["Z992", "Z49"]),
    _f("pregnancy", "Active pregnancy / postpartum", "obstetric", ["O", "Z33", "Z34", "Z39", "Z3A"],
       notes="Activates the obstetric hemorrhage module."),
]

# --- Obstetric hemorrhage (the meta-analysis slide) ---------------------------
# The five factors with the greatest confirmed pooled risk are weighted by
# their pooled ORs. The remaining "definite/likely" factors from the review
# were not on the slide: they are listed as candidates with no OR so they are
# monitored but contribute nothing until the review's numbers are entered.
OBSTETRIC: list[RiskFactor] = [
    _f("ob_previa_abruption", "Placenta previa / abruption", "obstetric", ["O44", "O45"],
       odds_ratio=2.74, status="pooled_or", source=PPH_SLIDE),
    _f("ob_uterine_rupture", "Uterine rupture", "obstetric", ["O710", "O711"],
       odds_ratio=2.35, status="pooled_or", source=PPH_SLIDE),
    _f("ob_prior_pph", "Prior postpartum hemorrhage", "obstetric", ["Z8759"],
       odds_ratio=2.25, status="pooled_or", source=PPH_SLIDE,
       notes="Z87.59 is broad (any prior pregnancy complication); prefer an explicit history flag."),
    _f("ob_multiple_gestation", "Multiple gestation", "obstetric", ["O30"],
       odds_ratio=2.16, status="pooled_or", source=PPH_SLIDE),
    _f("ob_prolonged_labor", "Prolonged labor", "obstetric", ["O63"],
       odds_ratio=1.76, status="pooled_or", source=PPH_SLIDE),
    _f("ob_accreta", "Placenta accreta spectrum", "obstetric", ["O432"], status="candidate"),
    _f("ob_chorioamnionitis", "Chorioamnionitis", "obstetric", ["O4112"], status="candidate"),
    _f("ob_hypertensive", "Preeclampsia / HELLP", "obstetric", ["O11", "O14"], status="candidate"),
    _f("ob_prior_cesarean", "Prior cesarean delivery", "obstetric", ["O3421"], status="candidate"),
    _f("ob_polyhydramnios", "Polyhydramnios", "obstetric", ["O40"], status="candidate"),
    _f("ob_macrosomia", "Fetal macrosomia", "obstetric", ["O366"], status="candidate"),
    _f("ob_fibroids", "Uterine fibroids in pregnancy", "obstetric", ["O341"], status="candidate"),
    _f("ob_anemia", "Anemia in pregnancy", "obstetric", ["O990"], status="candidate"),
]

# --- High-risk medication classes ------------------------------------------
MEDICATIONS: list[RiskFactor] = [
    RiskFactor("anticoagulant", "Anticoagulants", "medication", crs_points=1.0, status="v1_prior", source=V1,
               ingredients=("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban",
                            "heparin", "enoxaparin", "dalteparin", "fondaparinux")),
    RiskFactor("insulin", "Insulin", "medication", crs_points=1.5, status="v1_prior", source=V1,
               ingredients=("insulin",)),
    RiskFactor("opioid", "Opioids", "medication", crs_points=1.0, status="v1_prior", source=V1,
               ingredients=("morphine", "hydromorphone", "fentanyl", "oxycodone", "hydrocodone",
                            "methadone", "tramadol", "oxymorphone", "tapentadol", "buprenorphine")),
    RiskFactor("diuretic", "Diuretics (loop / thiazide)", "medication",
               ingredients=("furosemide", "bumetanide", "torsemide", "hydrochlorothiazide",
                            "chlorthalidone", "metolazone"),
               notes="Named in the v1 CRS label but not weighted in the v1 formula."),
    RiskFactor("antiplatelet", "Antiplatelets", "medication",
               ingredients=("aspirin", "clopidogrel", "prasugrel", "ticagrelor")),
    RiskFactor("beta_blocker", "Beta-blockers", "medication",
               ingredients=("metoprolol", "atenolol", "carvedilol", "propranolol", "labetalol", "bisoprolol")),
    RiskFactor("acei_arb", "ACE-I / ARB", "medication",
               ingredients=("lisinopril", "enalapril", "ramipril", "losartan", "valsartan", "irbesartan")),
    RiskFactor("sglt2i", "SGLT2 inhibitors", "medication",
               ingredients=("empagliflozin", "dapagliflozin", "canagliflozin", "ertugliflozin")),
    RiskFactor("sulfonylurea", "Sulfonylureas", "medication", ingredients=("glipizide", "glyburide", "glimepiride")),
    RiskFactor("corticosteroid", "Corticosteroids (systemic)", "medication",
               ingredients=("prednisone", "prednisolone", "methylprednisolone", "dexamethasone", "hydrocortisone")),
    RiskFactor("immunosuppressant", "Immunosuppressants", "medication",
               ingredients=("tacrolimus", "cyclosporine", "mycophenolate", "azathioprine", "sirolimus")),
    RiskFactor("benzodiazepine", "Benzodiazepines", "medication",
               ingredients=("lorazepam", "diazepam", "alprazolam", "clonazepam", "midazolam")),
    RiskFactor("antipsychotic", "Antipsychotics", "medication",
               ingredients=("haloperidol", "quetiapine", "olanzapine", "risperidone")),
    RiskFactor("digoxin", "Digoxin", "medication", ingredients=("digoxin",)),
    RiskFactor("lithium", "Lithium", "medication", ingredients=("lithium",)),
    RiskFactor("methotrexate", "Methotrexate", "medication", ingredients=("methotrexate",)),
    RiskFactor("antiarrhythmic", "Antiarrhythmics", "medication",
               ingredients=("amiodarone", "sotalol", "flecainide", "dofetilide")),
    RiskFactor("vasoactive", "Vasopressors / vasodilators", "medication",
               ingredients=("norepinephrine", "epinephrine", "vasopressin", "phenylephrine", "dopamine",
                            "dobutamine", "nitroglycerin", "nicardipine")),
    RiskFactor("nephrotoxin", "Nephrotoxins", "medication",
               ingredients=("gentamicin", "tobramycin", "amikacin", "vancomycin", "ibuprofen", "ketorolac", "naproxen")),
]

ALL: dict[str, RiskFactor] = {f.id: f for f in COMORBIDITIES + OBSTETRIC + MEDICATIONS}
OBSTETRIC_IDS = {f.id for f in OBSTETRIC}


def _norm(code: str) -> str:
    return code.replace(".", "").strip().upper()


def factors_for_icd10(code: str) -> list[RiskFactor]:
    """All condition factors whose ICD-10 prefixes match ``code``."""
    c = _norm(code)
    return [f for f in COMORBIDITIES + OBSTETRIC
            if any(c.startswith(_norm(p)) for p in f.icd10)]


def medication_classes(text: str) -> list[RiskFactor]:
    """Medication classes whose ingredient keywords appear in ``text``."""
    t = text.lower()
    return [f for f in MEDICATIONS if any(i in t for i in f.ingredients)]

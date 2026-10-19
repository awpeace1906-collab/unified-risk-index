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
  validated_index - category of a published, validated index (e.g. Charlson)
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
    charlson_weight: int = 0           # Charlson category weight (Quan 2011)
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

# --- Charlson Comorbidity Index -----------------------------------------------
# Quan H et al. Coding algorithms for defining comorbidities in ICD-9-CM and
# ICD-10 administrative data. Med Care 2005;43:1130-9 (ICD-10 codes), with the
# updated weights of Quan H et al. Am J Epidemiol 2011;173:676-82. Hierarchies
# (complicated > uncomplicated diabetes, severe > mild liver disease,
# metastatic > any malignancy) are applied in scores.charlson().
QUAN = "Quan 2005 ICD-10 Charlson coding; Quan 2011 weights"


def _cci(id, label, weight, icd10) -> RiskFactor:
    return RiskFactor(id=id, label=label, domain="charlson", icd10=tuple(icd10), charlson_weight=weight,
                      status="validated_index", source=QUAN)


CHARLSON: list[RiskFactor] = [
    _cci("cci_mi", "Myocardial infarction", 0, ["I21", "I22", "I252"]),
    _cci("cci_chf", "Congestive heart failure", 2,
         ["I099", "I110", "I130", "I132", "I255", "I420", "I425", "I426", "I427", "I428", "I429", "I43", "I50",
          "P290"]),
    _cci("cci_pvd", "Peripheral vascular disease", 0,
         ["I70", "I71", "I731", "I738", "I739", "I771", "I790", "I792", "K551", "K558", "K559", "Z958", "Z959"]),
    _cci("cci_cvd", "Cerebrovascular disease", 0, ["G45", "G46", "H340", "I6"]),
    _cci("cci_dementia", "Dementia", 2, ["F00", "F01", "F02", "F03", "F051", "G30", "G311"]),
    _cci("cci_pulmonary", "Chronic pulmonary disease", 1,
         ["I278", "I279", "J40", "J41", "J42", "J43", "J44", "J45", "J46", "J47", "J60", "J61", "J62", "J63",
          "J64", "J65", "J66", "J67", "J684", "J701", "J703"]),
    _cci("cci_rheumatic", "Rheumatic disease", 1,
         ["M05", "M06", "M315", "M32", "M33", "M34", "M351", "M353", "M360"]),
    _cci("cci_pud", "Peptic ulcer disease", 0, ["K25", "K26", "K27", "K28"]),
    _cci("cci_liver_mild", "Mild liver disease", 2,
         ["B18", "K700", "K701", "K702", "K703", "K709", "K713", "K714", "K715", "K717", "K73", "K74", "K760",
          "K762", "K763", "K764", "K768", "K769", "Z944"]),
    _cci("cci_dm", "Diabetes without chronic complication", 0,
         ["E100", "E101", "E106", "E108", "E109", "E110", "E111", "E116", "E118", "E119",
          "E130", "E131", "E136", "E138", "E139"]),
    _cci("cci_dm_complicated", "Diabetes with chronic complication", 1,
         ["E102", "E103", "E104", "E105", "E107", "E112", "E113", "E114", "E115", "E117",
          "E132", "E133", "E134", "E135", "E137"]),
    _cci("cci_plegia", "Hemiplegia / paraplegia", 2,
         ["G041", "G114", "G801", "G802", "G81", "G82", "G830", "G831", "G832", "G833", "G834", "G839"]),
    _cci("cci_renal", "Renal disease", 1,
         ["I120", "I131", "N032", "N033", "N034", "N035", "N036", "N037", "N052", "N053", "N054", "N055",
          "N056", "N057", "N18", "N19", "N250", "Z490", "Z491", "Z492", "Z940", "Z992"]),
    _cci("cci_malignancy", "Any malignancy (incl. lymphoma, leukemia)", 2,
         [f"C{n:02d}" for n in list(range(0, 27)) + list(range(30, 35)) + list(range(37, 42)) + [43]
          + list(range(45, 59)) + list(range(60, 77)) + list(range(81, 86)) + [88] + list(range(90, 98))]),
    _cci("cci_liver_severe", "Moderate / severe liver disease", 4,
         ["I850", "I859", "I864", "I982", "K704", "K711", "K721", "K729", "K765", "K766", "K767"]),
    _cci("cci_metastatic", "Metastatic solid tumor", 6, ["C77", "C78", "C79", "C80"]),
    _cci("cci_hiv", "AIDS / HIV", 4, ["B20", "B21", "B22", "B24"]),
]

# --- Obstetric hemorrhage: example module (off by default) --------------------
# This is the worked example from the lecture slide, kept as a template for
# how a pooled-OR-weighted factor set plugs in. Enable with
# Config(obstetric_module=True).
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

ALL: dict[str, RiskFactor] = {f.id: f for f in COMORBIDITIES + CHARLSON + OBSTETRIC + MEDICATIONS}
OBSTETRIC_IDS = {f.id for f in OBSTETRIC}
COMORBIDITY_IDS = {f.id for f in COMORBIDITIES}
CHARLSON_IDS = {f.id for f in CHARLSON}


def _norm(code: str) -> str:
    return code.replace(".", "").strip().upper()


def factors_for_icd10(code: str) -> list[RiskFactor]:
    """All condition factors whose ICD-10 prefixes match ``code``."""
    c = _norm(code)
    return [f for f in COMORBIDITIES + CHARLSON + OBSTETRIC
            if any(c.startswith(_norm(p)) for p in f.icd10)]


def medication_classes(text: str) -> list[RiskFactor]:
    """Medication classes whose ingredient keywords appear in ``text``."""
    t = text.lower()
    return [f for f in MEDICATIONS if any(i in t for i in f.ingredients)]
